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

import glob
import json
import os
import sys


class AmpAdapter:
    """Adapter for Sourcegraph Amp delegate permissions."""

    name = "amp"
    non_interactive = True  # Delegate mode has no retry loop; ADVISE = hard deny

    def get_active_thread_file(self):
        """Find the active Amp thread file by reading session.json."""
        session_file = os.path.expanduser("~/.local/share/amp/session.json")
        threads_dir = os.path.expanduser("~/.local/share/amp/threads")
        try:
            with open(session_file, 'r') as f:
                thread_id = json.load(f).get("lastThreadId", "")
            if thread_id:
                path = os.path.join(threads_dir, f"{thread_id}.json")
                if os.path.exists(path):
                    return path
        except Exception:
            pass
        # Fallback: newest thread file
        files = glob.glob(os.path.join(threads_dir, "*.json"))
        return max(files, key=os.path.getmtime) if files else None

    def read_user_messages(self, limit=10):
        """Read user messages from the active Amp thread."""
        thread_file = self.get_active_thread_file()
        if not thread_file:
            return []
        try:
            with open(thread_file, 'r') as f:
                thread = json.load(f)
            messages = []
            for msg in thread.get("messages", []):
                if msg.get("role") != "user":
                    continue
                content = msg.get("content", [])
                text = ""
                if isinstance(content, str):
                    text = content
                elif isinstance(content, list):
                    for c in content:
                        if isinstance(c, dict) and c.get("type") == "text":
                            text = c.get("text", "")
                            break
                if text and len(text.strip()) > 1:
                    messages.append({"role": "user", "content": text[:500], "timestamp": ""})
            return messages[-limit:]
        except Exception:
            return []

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
