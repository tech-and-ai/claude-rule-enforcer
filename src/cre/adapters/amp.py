"""
Amp (Sourcegraph) adapter — translates Amp delegate permission format to/from CRE.

Amp delegate permissions pass tool calls to external programs:
  - Tool name via AGENT_TOOL_NAME env var
  - Tool arguments as JSON on stdin

Exit codes (Amp convention):
  0 = allow
  1 = ask user (CRE maps ADVISE to this)
  2 = reject (stderr = reason)

Setup:
  amp permissions add delegate Bash --to "cre gate --format amp"
  amp permissions add delegate WriteFile --to "cre gate --format amp"
  amp permissions add delegate EditFile --to "cre gate --format amp"

Or delegate all tools:
  amp permissions add delegate '*' --to "cre gate --format amp"
"""

import os
import sys


class AmpAdapter:
    """Adapter for Sourcegraph Amp delegate permissions."""

    name = "amp"

    def parse_input(self, raw):
        """Parse Amp delegate input into CRE normalized format.

        Amp sends tool name via AGENT_TOOL_NAME env var.
        Tool arguments arrive as JSON on stdin (already parsed into raw).
        """
        tool_name = os.environ.get("AGENT_TOOL_NAME") or raw.get("tool_name") or ""
        tool_input = raw

        # Amp tool names: Bash, WriteFile, EditFile, ReadFile, etc.
        # Map to CRE tool types
        tool_type = tool_name.lower()

        # Normalize Amp tool names to CRE conventions
        amp_to_cre = {
            "bash": "bash",
            "writefile": "write",
            "editfile": "edit",
            "readfile": "read",
            "listfiles": "glob",
            "search": "grep",
            "websearch": "websearch",
            "webfetch": "webfetch",
        }
        tool_type = amp_to_cre.get(tool_type, tool_type)

        normalized = {
            "tool_type": tool_type,
            "command": raw.get("command", raw.get("cmd", "")),
            "file_path": raw.get("file_path", raw.get("path", "")),
            "content": raw.get("content", raw.get("new_string", "")),
            "tool_name": tool_name,
            "tool_input": tool_input,
            "raw": raw,
        }
        return normalized

    def format_allow(self):
        """Amp allow: empty stdout, exit 0."""
        return ""

    def format_allow_with_context(self, context_text):
        """Amp doesn't support context injection via delegate.
        Print context to stderr as informational, still allow."""
        print(context_text, file=sys.stderr)
        return ""

    def format_deny(self, reason):
        """Amp reject: reason on stderr, exit 2."""
        print(reason, file=sys.stderr)
        return ""

    def format_ask(self, reason):
        """Amp ask: reason on stderr, exit 1.
        Maps to CRE ADVISE decisions."""
        print(reason, file=sys.stderr)
        return ""

    @property
    def exit_allow(self):
        return 0

    @property
    def exit_deny(self):
        return 2

    @property
    def exit_ask(self):
        """Amp-specific: exit 1 = ask user for confirmation."""
        return 1
