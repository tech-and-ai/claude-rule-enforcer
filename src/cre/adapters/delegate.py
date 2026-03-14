"""
Delegate adapter — for AI tools that use external program delegation.

Many AI coding tools support delegating permission decisions to external
programs. The tool passes the action details to the program and uses its
exit code to decide whether to proceed.

Protocol:
  - Tool name via AGENT_TOOL_NAME env var (or in stdin JSON)
  - Tool arguments as JSON on stdin
  - Exit codes: 0 = allow, 1 = ask user, 2 = reject
  - Stderr messages are passed back to the user/model

Compatible tools:
  - Sourcegraph Amp (delegate permissions)
  - Any tool adopting the external-program permission pattern

Setup varies per tool. Example for Amp:
  amp permissions add delegate Bash --to "cre gate --format delegate"
  amp permissions add delegate '*' --to "cre gate --format delegate"

For conversation-aware features (PIN override, L2 context), the wrapper
script should set CRE_SESSIONS_DIR and CRE_CONVERSATION_FILE to point
at the tool's local conversation/thread storage.
"""

import glob
import json
import os
import sys


class DelegateAdapter:
    """Adapter for AI tools using external program delegation."""

    name = "delegate"
    non_interactive = True  # Delegate mode has no retry loop; ADVISE = hard deny

    # Configurable paths for conversation access
    SESSION_FILE_ENV = "CRE_DELEGATE_SESSION_FILE"  # JSON file with active session ID
    THREADS_DIR_ENV = "CRE_DELEGATE_THREADS_DIR"    # Directory containing conversation files

    def get_active_thread_file(self):
        """Find the active conversation file.

        Reads a session file (JSON with a thread/session ID) then finds the
        matching conversation file in the threads directory. Falls back to
        the most recently modified file.
        """
        session_file = os.environ.get(
            self.SESSION_FILE_ENV,
            os.path.expanduser("~/.local/share/amp/session.json")
        )
        threads_dir = os.environ.get(
            self.THREADS_DIR_ENV,
            os.path.expanduser("~/.local/share/amp/threads")
        )

        # Try to read session file for active thread ID
        try:
            with open(session_file, 'r') as f:
                data = json.load(f)
            # Support different session file formats
            thread_id = (
                data.get("lastThreadId") or
                data.get("activeThread") or
                data.get("sessionId") or
                ""
            )
            if thread_id:
                path = os.path.join(threads_dir, f"{thread_id}.json")
                if os.path.exists(path):
                    return path
        except Exception:
            pass

        # Fallback: newest JSON file in threads dir
        if os.path.isdir(threads_dir):
            files = glob.glob(os.path.join(threads_dir, "*.json"))
            return max(files, key=os.path.getmtime) if files else None
        return None

    def read_user_messages(self, limit=10):
        """Read user messages from the active conversation thread.

        Supports conversation files with a messages array where each
        message has role and content fields. Content can be a string
        or an array of {type, text} objects.
        """
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
        """Parse delegate input into CRE normalized format.

        Tool name comes from AGENT_TOOL_NAME env var or stdin JSON.
        Tool arguments arrive as JSON on stdin.
        """
        tool_name = os.environ.get("AGENT_TOOL_NAME") or raw.get("tool_name") or ""
        tool_input = raw

        tool_type = tool_name.lower()

        # Normalize common tool names to CRE conventions
        name_map = {
            "bash": "bash",
            "writefile": "write",
            "write_file": "write",
            "create_file": "write",
            "editfile": "edit",
            "edit_file": "edit",
            "readfile": "read",
            "read_file": "read",
            "listfiles": "glob",
            "list_files": "glob",
            "search": "grep",
            "websearch": "websearch",
            "webfetch": "webfetch",
        }
        tool_type = name_map.get(tool_type, tool_type)

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
        """Allow: empty stdout, exit 0."""
        return ""

    def format_allow_with_context(self, context_text):
        """Allow with context on stderr (informational)."""
        print(context_text, file=sys.stderr)
        return ""

    def format_deny(self, reason):
        """Reject: reason on stderr, exit 2."""
        print(reason, file=sys.stderr)
        return ""

    def format_ask(self, reason):
        """Ask user: reason on stderr, exit 1."""
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
        """Exit 1 = ask user for confirmation."""
        return 1
