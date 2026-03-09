"""
Claude Code adapter — translates Claude Code PreToolUse hook format to/from CRE.

Input format (from Claude Code):
  {"tool_name": "Bash", "tool_input": {"command": "ls -la"}}
  {"tool_name": "Write", "tool_input": {"file_path": "/tmp/x", "content": "..."}}
  {"tool_name": "Edit", "tool_input": {"file_path": "/tmp/x", "old_string": "...", "new_string": "..."}}

Output format (to Claude Code):
  Allow: {}
  Deny:  {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", ...}}

Exit codes:
  0 = allow (or empty response)
  2 = deny
"""

import json


class ClaudeCodeAdapter:
    """Adapter for Claude Code PreToolUse hooks."""

    name = "claude-code"

    def parse_input(self, raw):
        """Parse Claude Code hook JSON into CRE normalized format."""
        tool_name = raw.get("tool_name", "")
        tool_input = raw.get("tool_input", {})

        normalized = {
            "tool_type": tool_name.lower(),  # "bash", "write", "edit", "websearch", etc.
            "command": tool_input.get("command", ""),
            "file_path": tool_input.get("file_path", ""),
            "content": tool_input.get("content", tool_input.get("new_string", "")),
            "tool_name": tool_name,           # Original casing for alignment check
            "tool_input": tool_input,          # Full input dict for alignment check
            "raw": raw,
        }
        return normalized

    def format_allow(self):
        """Empty JSON = allow in Claude Code."""
        return json.dumps({})

    def format_allow_with_context(self, context_text):
        """Allow with injected context — Claude sees this before executing."""
        return json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "additionalContext": context_text
            }
        })

    def format_deny(self, reason):
        """Claude Code deny format with hookSpecificOutput."""
        return json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason
            }
        })

    @property
    def exit_allow(self):
        return 0

    @property
    def exit_deny(self):
        return 2
