"""
CRE MCP Server — Universal integration layer for any AI coding tool.

Exposes CRE's gate, override, and context as MCP tools that any AI
assistant can call. Works alongside delegate/hook enforcement:
- Delegate/hook = mandatory gate (can't be bypassed)
- MCP = intelligence layer (AI queries CRE for context, PIN validation, rules)

Usage:
    python -m cre.mcp_server                    # stdio transport (default)
    amp mcp add cre -- python -m cre.mcp_server # Add to Amp
    claude mcp add cre -- python -m cre.mcp_server  # Add to Claude Code

Tools:
    cre_check     - Test a command against CRE rules (L1 regex + L2 context)
    cre_override  - Submit a PIN to override an L1 block, returns credentials/context
    cre_status    - Show current CRE state (enabled, rules count, recent blocks)
    cre_rules     - List active rules by category
"""

import json
import os
import sys
import time

# Ensure CRE package is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from fastmcp import FastMCP

from . import config
from .gate import regex_check, _get_kb_context, call_permission_check

mcp = FastMCP(
    "Claude Rule Enforcer",
    instructions="Two-layer policy enforcement for AI coding assistants. "
                "Checks commands against L1 regex rules and L2 LLM review. "
                "Provides PIN override with credential injection.",
)

# Track recent blocks for cre_status
_recent_blocks = []
MAX_RECENT_BLOCKS = 20


def _load_rules():
    """Load rules from config."""
    return config.load_rules() or {}


@mcp.tool()
def cre_check(command: str, user_context: str = "") -> str:
    """Check a command against CRE rules before running it.

    Returns whether the command would be allowed or blocked, with the reason.
    Use this BEFORE executing potentially dangerous commands.

    Args:
        command: The shell command to check (e.g. "git push --force origin main")
        user_context: What the user asked for, so L2 can check intent alignment.
                      If empty, only L1 regex runs. If provided, L2 checks whether
                      the command matches the user's intent.
    """
    rules = _load_rules()
    if not rules:
        return json.dumps({"decision": "error", "reason": "Could not load rules"})

    if not rules.get("enabled", True):
        return json.dumps({"decision": "allow", "reason": "CRE is disabled"})

    # L1: regex check
    decision, reason = regex_check(command, rules)

    # L2: intent check (only if user_context provided and L1 didn't hard block)
    l2_result = None
    if user_context and decision != "deny":
        try:
            l2_decision, l2_reason = call_permission_check(
                command, f"User said: {user_context}", rules
            )
            if l2_decision == "DENY":
                decision = "deny"
                reason = f"Intent mismatch: {l2_reason}"
                l2_result = {"layer": "L2", "decision": "deny", "reason": l2_reason}
            elif l2_decision == "ALLOW":
                l2_result = {"layer": "L2", "decision": "allow", "reason": l2_reason}
        except Exception as e:
            config.log(f"MCP L2 error: {e}")

    # Build KB context for the command
    normalized = {
        "tool_type": "bash",
        "command": command,
        "file_path": "",
        "content": "",
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "raw": {"command": command},
    }
    kb_context = _get_kb_context(normalized)

    result = {
        "decision": decision,
        "reason": reason,
    }

    if l2_result:
        result["l2"] = l2_result

    if kb_context:
        result["context"] = kb_context

    if decision == "deny":
        result["override_available"] = bool(config.OVERRIDE_PIN)
        result["override_hint"] = "Use cre_override tool with your PIN to unlock this command"
        _recent_blocks.append({
            "command": command[:200],
            "reason": reason,
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
        if len(_recent_blocks) > MAX_RECENT_BLOCKS:
            _recent_blocks.pop(0)

    config.log(f"MCP cre_check: {command[:100]} -> {decision}" +
               (f" (L2: {l2_result['decision']})" if l2_result else ""))
    return json.dumps(result, indent=2)


@mcp.tool()
def cre_override(pin: str, command: str) -> str:
    """Submit a PIN to override a blocked command.

    After CRE blocks a command, the USER (not you) must provide a PIN.
    You should ask the user for the PIN, then call this tool with it.
    If the PIN is valid, this returns the credentials and context needed
    to execute the command.

    IMPORTANT: Only call this with a PIN the user explicitly typed.
    Never guess or fabricate PINs.

    Args:
        pin: The override PIN provided by the user
        command: The command that was blocked
    """
    configured_pin = config.OVERRIDE_PIN
    if not configured_pin:
        return json.dumps({
            "success": False,
            "reason": "No override PIN configured. Set CRE_OVERRIDE_PIN in .env"
        })

    if pin != configured_pin:
        config.log(f"MCP cre_override: PIN mismatch for {command[:80]}")
        return json.dumps({
            "success": False,
            "reason": "Invalid PIN. Ask the user for the correct override PIN."
        })

    # PIN valid, get KB context with credentials
    normalized = {
        "tool_type": "bash",
        "command": command,
        "file_path": "",
        "content": "",
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "raw": {"command": command},
    }
    kb_context = _get_kb_context(normalized)

    config.log(f"MCP cre_override: PIN accepted for {command[:80]}")
    config._audit(f"MCP PIN OVERRIDE: `{command[:120]}`")

    result = {
        "success": True,
        "reason": "PIN accepted. Command is now allowed.",
        "command": command,
    }

    if kb_context:
        result["context"] = kb_context
        result["instructions"] = "Use the credentials and context above to execute the command."

    return json.dumps(result, indent=2)


@mcp.tool()
def cre_status() -> str:
    """Show CRE's current state: enabled/disabled, rule counts, recent blocks.

    Call this to understand CRE's current configuration and what it has
    blocked recently.
    """
    rules = _load_rules()
    enabled = config.is_enabled() and rules.get("enabled", True)
    llm_enabled = rules.get("llm_review_enabled", True) and bool(config.LLM_API_KEY)

    result = {
        "enabled": enabled,
        "llm_review": llm_enabled,
        "pin_override": bool(config.OVERRIDE_PIN),
        "rules": {
            "always_block": len(rules.get("always_block", [])),
            "always_allow": len(rules.get("always_allow", [])),
            "needs_llm_review": len(rules.get("needs_llm_review", [])),
        },
        "recent_blocks": _recent_blocks[-5:],
    }

    config.log("MCP cre_status called")
    return json.dumps(result, indent=2)


@mcp.tool()
def cre_rules(category: str = "all") -> str:
    """List CRE's active rules.

    Args:
        category: Which rules to show. One of: "all", "block", "allow", "review"
    """
    rules = _load_rules()
    result = {}

    if category in ("all", "block"):
        result["always_block"] = [
            {"pattern": r.get("pattern", ""), "reason": r.get("reason", "")}
            for r in rules.get("always_block", [])
        ]
    if category in ("all", "allow"):
        result["always_allow"] = [
            {"pattern": r.get("pattern", "")}
            for r in rules.get("always_allow", [])[:10]
        ]
    if category in ("all", "review"):
        result["needs_llm_review"] = [
            {"pattern": r.get("pattern", ""), "context": r.get("context", "")}
            for r in rules.get("needs_llm_review", [])
        ]

    config.log(f"MCP cre_rules: {category}")
    return json.dumps(result, indent=2)


def main():
    """Run the CRE MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
