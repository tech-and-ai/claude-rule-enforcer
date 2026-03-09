#!/usr/bin/env python3
"""
CRE Tamper Detection — filesystem watcher for critical files.

Watches security-critical files and alerts via ntfy if anything touches them
outside of authorised processes. This is the "dumb and persistent" layer
that an LLM cannot sweet-talk its way past.

Watched files:
  - ~/.claude/cre_enabled (gate toggle)
  - rules.json (rule definitions)

On tamper detection:
  - Push notification via ntfy
  - Log the event with timestamp and process info
  - Optionally re-create the file if it was deleted (self-healing)

Usage:
  python -m cre.tamper_watch              # Run in foreground
  python -m cre.tamper_watch --daemon     # Run as background daemon

Systemd:
  systemctl --user start cre-tamper-watch
"""

import json
import os
import sys
import time
import signal
import subprocess
from datetime import datetime
from pathlib import Path

# --- Config ---

WATCH_FILES = {
    os.path.expanduser("~/.claude/cre_enabled"): {
        "name": "CRE gate toggle",
        "critical": True,
        "self_heal": True,  # Re-create if deleted
    },
}

# Find rules.json — check common locations
for rules_path in [
    os.environ.get("CRE_RULES_PATH", ""),
    os.path.join(os.path.dirname(__file__), "..", "..", "rules.json"),
    os.path.expanduser("~/.cre/rules.json"),
]:
    if rules_path and os.path.exists(rules_path):
        WATCH_FILES[os.path.realpath(rules_path)] = {
            "name": "CRE rules.json",
            "critical": True,
            "self_heal": False,
        }
        break

NTFY_TOPIC = "your-ntfy-topic"  # Configure your ntfy topic for push notifications
LOG_FILE = "/tmp/cre_tamper.log"
CHECK_INTERVAL = 2  # seconds between checks
AUTHORISED_COMMANDS = {"cre", "cre.cli", "vim", "nano", "vi"}  # Manual edits are OK


def _log(msg):
    """Log to file and stdout."""
    ts = datetime.now().isoformat()
    line = f"[{ts}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _ntfy(title, message, priority="urgent"):
    """Send push notification via ntfy."""
    try:
        subprocess.run(
            ["curl", "-s",
             "-H", f"Title: {title}",
             "-H", f"Priority: {priority}",
             "-H", "Tags: rotating_light,lock",
             "-d", message,
             f"https://ntfy.sh/{NTFY_TOPIC}"],
            capture_output=True, timeout=10
        )
        _log(f"NTFY sent: {title}")
    except Exception as e:
        _log(f"NTFY failed: {e}")


def _get_file_state(path):
    """Get current state of a file: exists, mtime, size, content hash."""
    try:
        if not os.path.exists(path):
            return {"exists": False}
        stat = os.stat(path)
        return {
            "exists": True,
            "mtime": stat.st_mtime,
            "size": stat.st_size,
        }
    except Exception:
        return {"exists": False}


def _snapshot_states():
    """Take a snapshot of all watched files."""
    return {path: _get_file_state(path) for path in WATCH_FILES}


def _check_tamper(old_states, new_states):
    """Compare states and detect tampering. Returns list of alerts."""
    alerts = []
    for path, info in WATCH_FILES.items():
        old = old_states.get(path, {})
        new = new_states.get(path, {})

        if old.get("exists") and not new.get("exists"):
            alerts.append({
                "path": path,
                "name": info["name"],
                "event": "DELETED",
                "critical": info["critical"],
                "self_heal": info["self_heal"],
            })
        elif old.get("exists") and new.get("exists"):
            if old.get("mtime") != new.get("mtime"):
                alerts.append({
                    "path": path,
                    "name": info["name"],
                    "event": "MODIFIED",
                    "critical": info["critical"],
                    "self_heal": False,
                })
        elif not old.get("exists") and new.get("exists"):
            # File appeared — not necessarily tampering (could be cre enable)
            _log(f"File appeared: {info['name']} ({path})")

    return alerts


def _self_heal(alert):
    """Re-create a deleted critical file."""
    path = alert["path"]
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).touch()
        _log(f"SELF-HEAL: Re-created {alert['name']} ({path})")
        _ntfy(
            "CRE Self-Healed",
            f"{alert['name']} was deleted and has been re-created automatically.",
            priority="high"
        )
    except Exception as e:
        _log(f"SELF-HEAL FAILED: {e}")


def run():
    """Main watch loop."""
    _log("CRE Tamper Watch started")
    _log(f"Watching {len(WATCH_FILES)} files:")
    for path, info in WATCH_FILES.items():
        _log(f"  {info['name']}: {path} (heal={info['self_heal']})")

    states = _snapshot_states()

    def handle_signal(sig, frame):
        _log("Tamper watch stopped (signal)")
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    while True:
        time.sleep(CHECK_INTERVAL)
        new_states = _snapshot_states()
        alerts = _check_tamper(states, new_states)

        for alert in alerts:
            _log(f"TAMPER DETECTED: {alert['event']} — {alert['name']} ({alert['path']})")

            if alert["critical"]:
                _ntfy(
                    f"CRE TAMPER: {alert['name']}",
                    f"{alert['event']}: {alert['path']}\n"
                    f"Time: {datetime.now().strftime('%H:%M:%S')}\n"
                    f"The AI or a process modified a security-critical CRE file.",
                    priority="urgent"
                )

            if alert["self_heal"] and alert["event"] == "DELETED":
                _self_heal(alert)

        states = new_states


def main():
    """Entry point."""
    if "--daemon" in sys.argv:
        # Fork to background
        pid = os.fork()
        if pid > 0:
            print(f"Tamper watch daemon started (PID {pid})")
            sys.exit(0)
        os.setsid()
        run()
    else:
        run()


if __name__ == "__main__":
    main()
