"""
CRE Watchdog - monitors config files for tampering.

Detects when CRE hooks are removed from settings files and auto-restores
them. Runs as a background process or cron job. This is the last line of
defence: even if an AI tool bypasses the delegate and MCP layers to edit
the config directly, the watchdog catches it and repairs it.

Usage:
    python -m cre.watchdog              # Run once (for cron)
    python -m cre.watchdog --daemon     # Run continuously (check every 30s)

Monitors:
    ~/.claude/settings.json     (Claude Code hooks)
    ~/.config/amp/settings.json (Amp delegate permissions)
"""

import hashlib
import json
import os
import sys
import time

# Files to monitor and what to look for
MONITORS = [
    {
        "path": os.path.expanduser("~/.claude/settings.json"),
        "check": "cre gate",
        "section": "hooks.PreToolUse",
        "name": "Claude Code hooks",
    },
    {
        "path": os.path.expanduser("~/.config/amp/settings.json"),
        "check": "cre-amp-gate",
        "section": "amp.permissions",
        "name": "Amp delegate permissions",
    },
]

BACKUP_DIR = os.path.expanduser("~/.cre/backups")
STATE_FILE = os.path.expanduser("~/.cre/watchdog_state.json")
LOG_FILE = "/tmp/cre_watchdog.log"


def log(msg):
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def notify(msg):
    """Send alert via ntfy if available."""
    try:
        import urllib.request
        urllib.request.urlopen(
            urllib.request.Request(
                f"https://ntfy.sh/{os.environ.get('CRE_NTFY_TOPIC', 'cre-watchdog')}",
                data=msg.encode(),
                headers={"Title": "CRE Watchdog Alert"},
            ),
            timeout=5,
        )
    except Exception:
        pass


def file_hash(path):
    try:
        with open(path, "rb") as f:
            return hashlib.md5(f.read()).hexdigest()
    except Exception:
        return None


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def backup_file(path):
    """Save a known-good backup of the config file."""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    name = os.path.basename(path)
    backup_path = os.path.join(BACKUP_DIR, f"{name}.good")
    try:
        with open(path) as f:
            data = f.read()
        with open(backup_path, "w") as f:
            f.write(data)
        return backup_path
    except Exception:
        return None


def restore_from_backup(path):
    """Restore config from last known-good backup."""
    name = os.path.basename(path)
    backup_path = os.path.join(BACKUP_DIR, f"{name}.good")
    if not os.path.exists(backup_path):
        return False
    try:
        with open(backup_path) as f:
            good_data = json.load(f)
        with open(path) as f:
            current_data = json.load(f)

        # For Claude Code: restore PreToolUse hooks
        if "hooks" in good_data and "hooks" in current_data:
            if "PreToolUse" in good_data["hooks"]:
                current_data["hooks"]["PreToolUse"] = good_data["hooks"]["PreToolUse"]

        # For Amp: restore permissions
        if "amp.permissions" in good_data and "amp.permissions" not in current_data:
            current_data["amp.permissions"] = good_data["amp.permissions"]

        with open(path, "w") as f:
            json.dump(current_data, f, indent=2)
        return True
    except Exception as e:
        log(f"Restore failed for {path}: {e}")
        return False


def check_file(monitor):
    """Check a single config file for CRE tampering."""
    path = monitor["path"]
    check_string = monitor["check"]
    name = monitor["name"]

    if not os.path.exists(path):
        return

    try:
        with open(path) as f:
            content = f.read()
    except Exception:
        return

    if check_string in content:
        # CRE hooks present, save as known-good backup
        backup_file(path)
        return

    # CRE hooks missing
    log(f"ALERT: {name} - CRE hooks removed from {path}")
    notify(f"CRE hooks removed from {name}. Attempting auto-restore.")

    if restore_from_backup(path):
        log(f"RESTORED: {name} - CRE hooks restored from backup")
        notify(f"CRE hooks restored in {name}.")
    else:
        log(f"FAILED: No backup available for {name}. Manual intervention needed.")
        notify(f"CRE hooks missing from {name} and no backup available. Fix manually.")


def run_once():
    """Check all monitored files once."""
    for monitor in MONITORS:
        check_file(monitor)


def run_daemon(interval=30):
    """Run continuously, checking every interval seconds."""
    log(f"Watchdog daemon started (interval: {interval}s)")
    state = load_state()
    state["started"] = time.strftime("%Y-%m-%d %H:%M:%S")
    save_state(state)

    while True:
        run_once()
        time.sleep(interval)


def main():
    if "--daemon" in sys.argv:
        run_daemon()
    else:
        run_once()


if __name__ == "__main__":
    main()
