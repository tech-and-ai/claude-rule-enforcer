"""
Generic adapter — simple JSON protocol for any AI coding tool.

Input format:
  {"tool": "bash", "command": "ls -la"}
  {"tool": "write", "file_path": "/tmp/x", "content": "..."}
  {"tool": "edit", "file_path": "/tmp/x", "content": "..."}

Output format:
  {"decision": "allow", "reason": "..."}
  {"decision": "deny", "reason": "..."}

Exit codes:
  0 = allow
  1 = deny
"""

import json


class GenericAdapter:
    """Generic adapter — works with any tool that can pipe JSON."""

    name = "generic"

    def parse_input(self, raw):
        """Parse generic JSON into CRE normalized format."""
        tool_type = raw.get("tool", "bash").lower()
        normalized = {
            "tool_type": tool_type,
            "command": raw.get("command", ""),
            "file_path": raw.get("file_path", ""),
            "content": raw.get("content", ""),
            "tool_name": raw.get("tool", "bash"),   # Original casing for alignment check
            "tool_input": raw,                        # Full input dict for alignment check
            "raw": raw,
        }
        return normalized

    def format_allow(self):
        return json.dumps({"decision": "allow", "reason": "Permitted"})

    def format_deny(self, reason):
        return json.dumps({"decision": "deny", "reason": reason})

    @property
    def exit_allow(self):
        return 0

    @property
    def exit_deny(self):
        return 1
