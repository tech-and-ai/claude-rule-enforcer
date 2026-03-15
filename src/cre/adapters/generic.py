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
        """Parse generic JSON into CRE normalized format.

        Handles multiple input formats:
          Generic:  {"tool": "bash", "command": "ls"}
          Cursor:   {"command": "ls", "cwd": "/path", "hook_event_name": "beforeShellExecution"}
          Windsurf: {"tool_info": {"command_line": "ls"}, "agent_action_name": "pre_run_command"}
        """
        # Extract command from different formats
        command = raw.get("command", "")
        if not command:
            tool_info = raw.get("tool_info", {})
            if isinstance(tool_info, dict):
                command = tool_info.get("command_line", "")

        tool_type = raw.get("tool", "bash").lower()
        normalized = {
            "tool_type": tool_type,
            "command": command,
            "file_path": raw.get("file_path", ""),
            "content": raw.get("content", ""),
            "tool_name": raw.get("tool", "bash"),
            "tool_input": raw,
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
