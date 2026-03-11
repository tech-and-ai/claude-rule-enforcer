#!/usr/bin/env python3
"""
CRE Chat History — adapter-agnostic conversation log.

CRE's own chat file is the single source of truth for L2a/L2b.
Any adapter (Claude Code, Codex, Cursor, OpenClaw) writes to this file.

Format: one JSON object per line in /tmp/cre_chat_{instance_id}.jsonl
  {"role": "user|assistant", "content": "...", "timestamp": "ISO8601"}
Instance ID derived from CC parent PID + project path (per-SESSION isolation).
Two CC instances in the same project get separate chat files.

How messages get in:
  - Claude Code: UserPromptSubmit hook captures user messages directly,
    syncs assistant messages from CC's JSONL (guaranteed flushed at hook time).
  - Other adapters: call log_message() or write the same format directly.

Why not just read CC's JSONL?
  1. Timing: CC may not flush user messages before PreToolUse fires
  2. Lock-in: ties CRE to CC's internal format
  3. Control: we own the data, we control the format
"""

import hashlib
import json
import os
import sys
import re
from datetime import datetime

from . import config

# Max messages to keep in the file (trim on write to prevent unbounded growth)
# Only recent context matters for L2. PIN override scans last 10 user messages.
MAX_MESSAGES = 5


def _cc_parent_pid():
    """Walk up process tree to find the Claude Code node process PID.

    Each CC instance is a separate node process. The hook is spawned as:
      CC node (PID X) -> bash -> python (us)
    Walking up finds X, which is unique per CC session.
    Returns PID as string, or None if not on Linux or not found.
    """
    try:
        pid = os.getppid()
        for _ in range(10):
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cmdline = f.read().decode("utf-8", errors="replace")
            if any(sig in cmdline for sig in ("claude-code", "@anthropic-ai", "cursor", "Cursor", "codex", "windsurf")):
                return str(pid)
            with open(f"/proc/{pid}/stat") as f:
                ppid = int(f.read().split()[3])
            if ppid <= 1:
                break
            pid = ppid
    except Exception:
        pass
    return None


def _instance_id():
    """Derive a unique instance ID for this CC session's chat files.

    Priority:
    1. CRE_INSTANCE_ID env var (explicit override for testing/other adapters)
    2. CC parent PID + project path (per-SESSION isolation)
    3. npm_config_local_prefix only (per-project fallback for non-Linux)
    4. "default" (single-session mode)

    The CC PID is key: two CC instances in the same project get different PIDs,
    so they get different chat files. L2 never sees the other session's context.
    """
    explicit = os.environ.get("CRE_INSTANCE_ID")
    if explicit:
        return explicit

    project_root = os.environ.get("npm_config_local_prefix", "")
    cc_pid = _cc_parent_pid() or os.environ.get("VSCODE_PID")

    if cc_pid and project_root:
        key = f"{project_root}:{cc_pid}"
        return hashlib.md5(key.encode()).hexdigest()[:8]

    # Fallback: use project root + our own PID to avoid cross-session collisions
    if project_root:
        key = f"{project_root}:{os.getpid()}"
        return hashlib.md5(key.encode()).hexdigest()[:8]
    return hashlib.md5(str(os.getpid()).encode()).hexdigest()[:8]


def _chat_file():
    """Get the instance-specific chat file path."""
    explicit = os.environ.get("CRE_CHAT_FILE")
    if explicit:
        return explicit
    return f"/tmp/cre_chat_{_instance_id()}.jsonl"


def _sync_state_file():
    """Get the instance-specific sync state file path."""
    return f"/tmp/cre_chat_sync_{_instance_id()}.json"


def log_message(role, content, timestamp=None):
    """Log a single message to CRE's chat history.

    This is the adapter-agnostic API. Any adapter calls this to record
    user or assistant messages.

    Args:
        role: "user" or "assistant"
        content: message text (will be stripped of system reminders, capped at 500 chars)
        timestamp: ISO8601 string, defaults to now
    """
    if not content or not content.strip():
        return

    clean = _strip_system_reminders(content) if role == "user" else content
    if not clean or not clean.strip():
        return

    # Skip session continuations
    if clean.startswith('This session is being continued'):
        return

    msg = {
        "role": role,
        "content": clean[:500],
        "timestamp": timestamp or datetime.now().isoformat()
    }

    try:
        with open(_chat_file(), "a") as f:
            f.write(json.dumps(msg) + "\n")
    except Exception as e:
        config.log(f"Chat log write error: {e}")


def log_messages(messages):
    """Log multiple messages at once. Each is a dict with role, content, timestamp."""
    try:
        with open(_chat_file(), "a") as f:
            for msg in messages:
                content = msg.get("content", "")
                if not content or not content.strip():
                    continue
                role = msg.get("role", "user")
                clean = _strip_system_reminders(content) if role == "user" else content
                if not clean or not clean.strip():
                    continue
                if clean.startswith('This session is being continued'):
                    continue
                entry = {
                    "role": role,
                    "content": clean[:500],
                    "timestamp": msg.get("timestamp", datetime.now().isoformat())
                }
                f.write(json.dumps(entry) + "\n")
    except Exception as e:
        config.log(f"Chat log batch write error: {e}")


def read_chat(limit=30):
    """Read the last N messages from CRE's chat history.

    This is what L2a/L2b calls. Returns list of dicts with role, content, timestamp.
    """
    try:
        cf = _chat_file()
        if not os.path.exists(cf):
            return []
        with open(cf, "r") as f:
            lines = f.readlines()
        messages = []
        for line in lines[-limit:]:
            try:
                messages.append(json.loads(line))
            except Exception:
                continue
        return messages
    except Exception:
        return []


def trim_chat():
    """Trim chat file to MAX_MESSAGES. Called periodically to prevent growth."""
    try:
        cf = _chat_file()
        if not os.path.exists(cf):
            return
        with open(cf, "r") as f:
            lines = f.readlines()
        if len(lines) > MAX_MESSAGES:
            with open(cf, "w") as f:
                f.writelines(lines[-MAX_MESSAGES:])
            config.log(f"Chat trimmed: {len(lines)} -> {MAX_MESSAGES}")
    except Exception as e:
        config.log(f"Chat trim error: {e}")


def clear_chat():
    """Clear the chat file (new session)."""
    try:
        cf = _chat_file()
        if os.path.exists(cf):
            os.remove(cf)
    except Exception:
        pass


def _strip_system_reminders(text):
    """Remove <system-reminder> blocks from user messages."""
    cleaned = re.sub(r'<system-reminder>.*?</system-reminder>', '', text, flags=re.DOTALL)
    # Also strip common hook injections
    cleaned = re.sub(r'<local-command-caveat>.*?</local-command-caveat>', '', cleaned, flags=re.DOTALL)
    cleaned = re.sub(r'<command-name>.*?</command-name>', '', cleaned, flags=re.DOTALL)
    cleaned = re.sub(r'<command-message>.*?</command-message>', '', cleaned, flags=re.DOTALL)
    cleaned = re.sub(r'<command-args>.*?</command-args>', '', cleaned, flags=re.DOTALL)
    cleaned = re.sub(r'<local-command-stdout>.*?</local-command-stdout>', '', cleaned, flags=re.DOTALL)
    return cleaned.strip()


# --- Claude Code adapter: JSONL sync ---
# This is CC-specific. Other adapters don't need this.

def _extract_text(msg_content):
    """Extract text from a Claude Code message content field."""
    if isinstance(msg_content, str):
        return msg_content
    if isinstance(msg_content, list):
        for c in msg_content:
            if isinstance(c, dict) and c.get('type') == 'text':
                return c.get('text', '')
    return ''


def _detect_cc_jsonl():
    """Find this CC session's JSONL by checking parent process file descriptors.

    Primary: walk up to the CC process, check /proc/{pid}/fd for open .jsonl files.
    Fallback: most recent .jsonl by mtime in the project directory.
    """
    import glob

    # Primary: find JSONL via CC process open file descriptors (Linux only)
    cc_pid = _cc_parent_pid()
    if cc_pid:
        fd_dir = f"/proc/{cc_pid}/fd"
        try:
            for fd in os.listdir(fd_dir):
                try:
                    target = os.readlink(os.path.join(fd_dir, fd))
                    if target.endswith(".jsonl") and "/.claude/projects/" in target:
                        return target
                except Exception:
                    continue
        except Exception:
            pass

    # Fallback: mtime-based (original behavior, for non-Linux or if /proc fails)
    claude_dir = os.path.expanduser("~/.claude/projects")
    if not os.path.isdir(claude_dir):
        return None

    project_root = os.environ.get("npm_config_local_prefix", "")
    if project_root:
        project_name = project_root.replace("/", "-")
        project_dir = os.path.join(claude_dir, project_name)
        if os.path.isdir(project_dir):
            jsonl_files = glob.glob(os.path.join(project_dir, "*.jsonl"))
            if jsonl_files:
                return max(jsonl_files, key=os.path.getmtime)

    best_file = None
    best_mtime = 0
    try:
        for d in os.listdir(claude_dir):
            full = os.path.join(claude_dir, d)
            if not os.path.isdir(full):
                continue
            for jf in glob.glob(os.path.join(full, "*.jsonl")):
                mt = os.path.getmtime(jf)
                if mt > best_mtime:
                    best_mtime = mt
                    best_file = jf
    except Exception:
        pass
    return best_file


def _load_sync_state():
    try:
        with open(_sync_state_file(), "r") as f:
            return json.load(f)
    except Exception:
        return {"offset": 0, "file": ""}


def _save_sync_state(state):
    try:
        with open(_sync_state_file(), "w") as f:
            json.dump(state, f)
    except Exception:
        pass


def sync_from_cc_jsonl():
    """Sync new messages from Claude Code's JSONL into CRE's chat file.

    Called by the UserPromptSubmit hook. At hook time, the previous
    assistant response is guaranteed to be flushed to the JSONL.
    """
    jsonl_path = _detect_cc_jsonl()
    if not jsonl_path:
        return 0

    state = _load_sync_state()
    if state.get("file") != jsonl_path:
        state = {"offset": 0, "file": jsonl_path}

    file_size = os.path.getsize(jsonl_path)
    if file_size <= state["offset"]:
        return 0

    new_messages = []
    try:
        with open(jsonl_path, "r") as f:
            f.seek(state["offset"])
            if state["offset"] > 0:
                f.readline()  # skip partial line

            for line in f:
                try:
                    d = json.loads(line)
                    entry_type = d.get('type', '')
                    ts = d.get('timestamp', datetime.now().isoformat())

                    if entry_type == 'assistant':
                        msg = d.get('message', {})
                        if not isinstance(msg, dict):
                            continue
                        text = _extract_text(msg.get('content', ''))
                        if text and len(text.strip()) > 10:
                            new_messages.append({
                                "role": "assistant",
                                "content": text[:500],
                                "timestamp": ts
                            })

                    elif entry_type == 'user':
                        msg = d.get('message', {})
                        if not isinstance(msg, dict):
                            continue
                        text = _extract_text(msg.get('content', ''))
                        if text:
                            clean = _strip_system_reminders(text)
                            if clean and len(clean.strip()) > 0:
                                if clean.startswith('This session is being continued'):
                                    continue
                                new_messages.append({
                                    "role": "user",
                                    "content": clean[:500],
                                    "timestamp": ts
                                })

                except Exception:
                    continue

            state["offset"] = f.tell()
            state["file"] = jsonl_path

    except Exception as e:
        config.log(f"CC JSONL sync error: {e}")

    _save_sync_state(state)

    if new_messages:
        log_messages(new_messages)

    return len(new_messages)


# --- Hook entry point ---

def main():
    """UserPromptSubmit hook entry point for Claude Code.

    1. Syncs any new assistant messages from CC's JSONL
    2. Logs the current user message
    3. Trims chat file if needed
    """
    try:
        input_data = sys.stdin.read()
        if not input_data.strip():
            return

        data = json.loads(input_data)
        user_prompt = data.get('prompt', '')

        if not user_prompt or not user_prompt.strip():
            return

        # 1. Sync assistant messages from CC's JSONL
        count = sync_from_cc_jsonl()
        if count:
            config.log(f"Chat sync: {count} messages from CC JSONL")

        # 2. Log current user message
        log_message("user", user_prompt)

        # 3. Periodic trim
        trim_chat()

    except Exception as e:
        config.log(f"Chat logger error: {e}")


if __name__ == "__main__":
    main()
