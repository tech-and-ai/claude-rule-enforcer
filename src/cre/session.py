"""Session reader — reads Claude Code JSONL session files for conversation context."""

import glob
import json
import os

from . import config


def detect_sessions_dir():
    """Auto-detect the Claude Code sessions directory.

    Priority:
    1. CRE_SESSIONS_DIR env var (explicit override)
    2. npm_config_local_prefix → derive project dir name (deterministic, multi-instance safe)
    3. Scan for most recently modified JSONL file (fallback for non-Claude-Code platforms)
    """
    if config.SESSIONS_DIR:
        return config.SESSIONS_DIR

    claude_dir = os.path.expanduser("~/.claude/projects")
    if not os.path.isdir(claude_dir):
        return None

    # Method 1: Use npm_config_local_prefix to find exact project dir
    # Claude Code sets this via Node.js runtime — unique per instance
    project_root = os.environ.get("npm_config_local_prefix", "")
    if project_root:
        project_name = project_root.replace("/", "-")
        project_dir = os.path.join(claude_dir, project_name)
        if os.path.isdir(project_dir):
            config.log(f"Session dir (from env): {project_name}")
            return project_dir

    # Method 2: Scan for most recently modified JSONL file
    # Fallback when env var unavailable (non-CC platforms, tests, CLI usage)
    try:
        subdirs = [os.path.join(claude_dir, d) for d in os.listdir(claude_dir)
                   if os.path.isdir(os.path.join(claude_dir, d))]
        if not subdirs:
            return None

        best_dir = None
        best_mtime = 0
        for d in subdirs:
            jsonl_files = glob.glob(os.path.join(d, "*.jsonl"))
            for jf in jsonl_files:
                mt = os.path.getmtime(jf)
                if mt > best_mtime:
                    best_mtime = mt
                    best_dir = d

        if best_dir:
            config.log(f"Session dir (from scan): {os.path.basename(best_dir)} (JSONL mtime {best_mtime:.0f})")
            return best_dir

        return max(subdirs, key=os.path.getmtime)
    except Exception:
        return None


def _read_amp_thread(json_files, limit=30):
    """Read Amp thread JSON files. Amp stores one JSON file per thread with a
    messages array containing {role, content[{type, text}]} objects."""
    try:
        latest = max(json_files, key=os.path.getmtime)
        with open(latest, 'r') as f:
            thread = json.load(f)

        messages = []
        for msg in thread.get("messages", []):
            role = msg.get("role", "")
            if role not in ("user", "assistant"):
                continue

            content = msg.get("content", [])
            text = ""
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                parts = []
                for c in content:
                    if isinstance(c, dict) and c.get("type") == "text":
                        parts.append(c.get("text", ""))
                text = " ".join(parts)

            if not text or len(text.strip()) < 2:
                continue

            if role == "user":
                clean = _strip_system_reminders(text)
                if clean and len(clean.strip()) > 0:
                    messages.append({"role": "user", "content": clean[:500], "timestamp": ""})
            else:
                messages.append({"role": "assistant", "content": text[:500], "timestamp": ""})

        return messages[-limit:]
    except Exception as e:
        config.log(f"Amp thread read error: {e}")
        return []


def read_live_session(limit=30):
    """Read the most recent JSONL session file for current conversation messages.
    Includes BOTH user and assistant messages so the LLM can see Q&A context.
    """
    try:
        sessions_dir = detect_sessions_dir()
        if not sessions_dir:
            return []

        jsonl_files = glob.glob(os.path.join(sessions_dir, "*.jsonl"))

        # Also check for Amp-style JSON thread files
        json_files = glob.glob(os.path.join(sessions_dir, "*.json"))
        if json_files and not jsonl_files:
            return _read_amp_thread(json_files, limit)

        if not jsonl_files:
            return []

        latest = max(jsonl_files, key=os.path.getmtime)
        file_size = os.path.getsize(latest)
        read_from = max(0, file_size - 1500000)

        messages = []
        with open(latest, 'r') as f:
            if read_from > 0:
                f.seek(read_from)
                f.readline()  # skip partial line
            for line in f:
                try:
                    d = json.loads(line)
                    entry_type = d.get('type', '')

                    if entry_type == 'user':
                        msg = d.get('message', {})
                        if not isinstance(msg, dict):
                            continue
                        content = msg.get('content', '')
                        ts = d.get('timestamp', '')

                        text = ''
                        if isinstance(content, str):
                            text = content
                        elif isinstance(content, list):
                            for c in content:
                                if isinstance(c, dict) and c.get('type') == 'text':
                                    text = c.get('text', '')
                                    break

                        if text and len(text.strip()) > 0 and not text.startswith('[Request interrupted'):
                            # Skip session continuation summaries — they contain stale context
                            if text.startswith('This session is being continued from a previous conversation'):
                                continue
                            # Strip system-reminder tags — they pollute L2 context
                            clean = _strip_system_reminders(text)
                            if clean and len(clean.strip()) > 0:
                                messages.append({
                                    "role": "user",
                                    "content": clean[:500],
                                    "timestamp": ts
                                })

                    elif entry_type == 'assistant':
                        msg = d.get('message', {})
                        if not isinstance(msg, dict):
                            continue
                        content = msg.get('content', '')
                        ts = d.get('timestamp', '')

                        text_parts = []
                        if isinstance(content, str):
                            text_parts.append(content)
                        elif isinstance(content, list):
                            for c in content:
                                if isinstance(c, dict) and c.get('type') == 'text':
                                    t = c.get('text', '').strip()
                                    if t:
                                        text_parts.append(t)

                        combined = ' '.join(text_parts)
                        if combined and len(combined) > 10:
                            messages.append({
                                "role": "assistant",
                                "content": combined[:500],
                                "timestamp": ts
                            })

                except Exception:
                    continue

        # Split by role — ensure user messages are always well-represented
        user_msgs = [m for m in messages if m['role'] == 'user']
        assistant_msgs = [m for m in messages if m['role'] == 'assistant']

        # Always include the last 15 user messages (the ones that matter for approval)
        # Fill remaining slots with assistant messages for Q&A context
        user_limit = min(len(user_msgs), max(15, limit // 2))
        assistant_limit = limit - user_limit

        selected = user_msgs[-user_limit:] + assistant_msgs[-assistant_limit:]
        selected.sort(key=lambda m: m.get('timestamp', ''))

        config.log(f"Session: {len(messages)} total, {len(user_msgs)} user, {len(assistant_msgs)} assistant, selected {len(selected)} ({user_limit}u + {min(len(assistant_msgs), assistant_limit)}a)")
        return selected[-limit:]

    except Exception as e:
        config.log(f"Live session read error: {e}")
        return []


def _strip_system_reminders(text):
    """Remove <system-reminder>...</system-reminder> blocks from user message text.

    System reminders (preprompt hooks, skill lists, task notifications) are injected
    into user messages by Claude Code but are NOT the user's actual words.
    If stripped text is empty, returns None (skip this message entirely).
    """
    import re
    cleaned = re.sub(r'<system-reminder>.*?</system-reminder>', '', text, flags=re.DOTALL)
    cleaned = cleaned.strip()
    return cleaned if cleaned else None


def search_recent_conversation(command, limit=5):
    """Search recent conversation for permission/approval context.
    Prefers CRE's own chat log (timing-safe) over JSONL (may lag).
    Only returns last 5 messages to prevent stale context poisoning L2.

    In delegate mode (non-interactive), reads the delegate tool's conversation
    instead of CRE's chat log to prevent session bleed between tools.
    """
    all_results = []
    seen_content = set()

    # Delegate mode: read the calling tool's conversation, not CRE's chat log
    if os.environ.get("AGENT_TOOL_NAME"):
        try:
            from .adapters.delegate import DelegateAdapter
            delegate = DelegateAdapter()
            delegate_msgs = delegate.read_user_messages(limit=10)
            if delegate_msgs:
                for msg in delegate_msgs:
                    content_hash = hash(msg['content'][:200])
                    if content_hash not in seen_content:
                        seen_content.add(content_hash)
                        all_results.append(msg)
                config.log(f"Delegate conversation: {len(delegate_msgs)} messages, {len(all_results)} unique")
                return all_results[-limit:]
        except Exception as e:
            config.log(f"Delegate conversation fallback: {e}")

    # Claude Code mode: CRE's own chat file (written by UserPromptSubmit hook)
    try:
        from .chat_logger import read_chat
        cre_msgs = read_chat(limit=10)
        if cre_msgs and len(cre_msgs) >= 2:
            for msg in cre_msgs:
                content_hash = hash(msg['content'][:200])
                if content_hash not in seen_content:
                    seen_content.add(content_hash)
                    all_results.append(msg)
            config.log(f"CRE chat: {len(cre_msgs)} messages, {len(all_results)} unique")
            return all_results[-limit:]
    except Exception as e:
        config.log(f"CRE chat fallback: {e}")

    # Fallback to JSONL reader
    live_msgs = read_live_session(limit=10)
    for msg in live_msgs:
        content_hash = hash(msg['content'][:200])
        if content_hash not in seen_content:
            seen_content.add(content_hash)
            all_results.append(msg)

    config.log(f"Live session: {len(live_msgs)} messages, {len(all_results)} unique")
    return all_results[-limit:]


def scan_session_files(hours=None):
    """Scan JSONL session files for learning engine.

    Args:
        hours: If set, only scan files modified in the last N hours. None = all files.

    Returns:
        List of message dicts with role, content, timestamp, session_file.
    """
    import time

    sessions_base = config.SESSIONS_DIR or os.path.expanduser("~/.claude/projects")
    if not os.path.isdir(sessions_base):
        return []

    cutoff = time.time() - (hours * 3600) if hours else 0
    all_messages = []

    for root, dirs, files in os.walk(sessions_base):
        for fname in files:
            if not fname.endswith(".jsonl"):
                continue
            fpath = os.path.join(root, fname)
            if hours and os.path.getmtime(fpath) < cutoff:
                continue

            try:
                with open(fpath, 'r') as f:
                    for line in f:
                        try:
                            d = json.loads(line)
                            if d.get('type') != 'user':
                                continue
                            msg = d.get('message', {})
                            if not isinstance(msg, dict):
                                continue
                            content = msg.get('content', '')
                            text = ''
                            if isinstance(content, str):
                                text = content
                            elif isinstance(content, list):
                                for c in content:
                                    if isinstance(c, dict) and c.get('type') == 'text':
                                        text = c.get('text', '')
                                        break
                            if text and len(text.strip()) > 5:
                                all_messages.append({
                                    "role": "user",
                                    "content": text[:1000],
                                    "timestamp": d.get('timestamp', ''),
                                    "session_file": fname
                                })
                        except Exception:
                            continue
            except Exception:
                continue

    all_messages.sort(key=lambda m: m.get('timestamp', ''))
    return all_messages
