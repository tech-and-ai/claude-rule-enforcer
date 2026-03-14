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
from . import db
from .gate import regex_check, _get_kb_context, call_permission_check

mcp = FastMCP(
    "Claude Rule Enforcer",
    instructions="Two-layer policy enforcement for AI coding assistants. "
                "Checks commands against L1 regex rules and L2 LLM review. "
                "Provides PIN override with credential injection.",
)

# Initialize SQLite database
try:
    db.init_db()
except Exception:
    pass

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

    # Log to SQLite
    try:
        db.log_event("mcp", command[:200], decision, reason[:500], "L2" if l2_result else "L1", "cre_check")
    except Exception:
        pass

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


@mcp.tool()
def cre_run(command: str, user_context: str = "") -> str:
    """Execute a shell command through CRE's enforcement layer.

    Use this instead of running commands directly. CRE checks L1 regex
    and L2 intent before executing. If blocked, returns the reason and
    override instructions. If allowed, executes and returns the output.

    Args:
        command: The shell command to execute
        user_context: What the user asked for (enables L2 intent checking)
    """
    import subprocess

    rules = _load_rules()
    if not rules:
        return json.dumps({"status": "error", "reason": "Could not load rules"})

    # L1 check
    decision, reason = regex_check(command, rules)

    # L2 check if context provided and L1 didn't block
    if user_context and decision != "deny":
        try:
            l2_decision, l2_reason = call_permission_check(
                command, f"User said: {user_context}", rules
            )
            if l2_decision == "DENY":
                decision = "deny"
                reason = f"Intent mismatch: {l2_reason}"
        except Exception as e:
            config.log(f"cre_run L2 error: {e}")

    if decision == "deny":
        config.log(f"MCP cre_run BLOCKED: {command[:100]}")
        _recent_blocks.append({
            "command": command[:200],
            "reason": reason,
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
        if len(_recent_blocks) > MAX_RECENT_BLOCKS:
            _recent_blocks.pop(0)
        return json.dumps({
            "status": "blocked",
            "reason": reason,
            "override_available": bool(config.OVERRIDE_PIN),
            "override_hint": "Ask the user for their PIN, then call cre_override",
        }, indent=2)

    # Execute
    config.log(f"MCP cre_run EXEC: {command[:100]}")
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=30
        )
        return json.dumps({
            "status": "executed",
            "exit_code": result.returncode,
            "stdout": result.stdout[:5000],
            "stderr": result.stderr[:2000],
        }, indent=2)
    except subprocess.TimeoutExpired:
        return json.dumps({"status": "error", "reason": "Command timed out (30s)"})
    except Exception as e:
        return json.dumps({"status": "error", "reason": str(e)})


@mcp.tool()
def cre_write(file_path: str, content: str, user_context: str = "") -> str:
    """Write a file through CRE's enforcement layer.

    Use this instead of writing files directly. CRE checks whether the
    write is allowed based on rules and intent before writing.

    Args:
        file_path: Path to the file to write
        content: Content to write to the file
        user_context: What the user asked for (enables L2 intent checking)
    """
    rules = _load_rules()
    if not rules:
        return json.dumps({"status": "error", "reason": "Could not load rules"})

    # Self-protection: block writes to CRE config
    protected = ["settings.json", "rules.json", "cre_enabled", "policy_gate_enabled"]
    for p in protected:
        if p in file_path:
            content_lower = content.lower()
            if any(kw in content_lower for kw in ["cre", "gate", "pretooluse", "hook", "delegate"]):
                config.log(f"MCP cre_write BLOCKED (self-protection): {file_path}")
                return json.dumps({
                    "status": "blocked",
                    "reason": f"Cannot modify CRE configuration in {os.path.basename(file_path)}",
                })

    # L2 check if context provided
    if user_context:
        try:
            l2_decision, l2_reason = call_permission_check(
                f"write {file_path}", f"User said: {user_context}", rules
            )
            if l2_decision == "DENY":
                config.log(f"MCP cre_write BLOCKED (L2): {file_path}")
                return json.dumps({
                    "status": "blocked",
                    "reason": f"Intent mismatch: {l2_reason}",
                }, indent=2)
        except Exception as e:
            config.log(f"cre_write L2 error: {e}")

    # Write the file
    config.log(f"MCP cre_write: {file_path}")
    try:
        dir_path = os.path.dirname(file_path)
        if dir_path:
            os.makedirs(dir_path, exist_ok=True)
        with open(file_path, 'w') as f:
            f.write(content)
        return json.dumps({
            "status": "written",
            "file_path": file_path,
            "bytes": len(content),
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "reason": str(e)})


@mcp.tool()
def cre_read(file_path: str) -> str:
    """Read a file through CRE.

    Args:
        file_path: Path to the file to read
    """
    config.log(f"MCP cre_read: {file_path}")
    try:
        with open(file_path, 'r') as f:
            content = f.read()
        return json.dumps({
            "status": "ok",
            "file_path": file_path,
            "content": content[:10000],
            "truncated": len(content) > 10000,
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "reason": str(e)})


def main():
    """Run the CRE MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
