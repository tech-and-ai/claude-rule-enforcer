"""Advice Tracker — logs L2 ADVISE outcomes (PROCEED/STOP) per pattern.

When a pattern accumulates CRE_ADVISE_THRESHOLD ignored advices,
it surfaces for the user to decide: promote to L1, remove, or keep.
"""

import json
import os
from datetime import datetime

from . import config


def _load_tracker():
    """Load advice tracker state from JSON file."""
    try:
        if os.path.exists(config.ADVICE_LOG_PATH):
            with open(config.ADVICE_LOG_PATH) as f:
                return json.load(f)
    except Exception as e:
        config.log(f"Advice tracker load error: {e}")
    return {"patterns": {}, "suggestions": []}


def _save_tracker(data):
    """Save advice tracker state to JSON file."""
    try:
        with open(config.ADVICE_LOG_PATH, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
    except Exception as e:
        config.log(f"Advice tracker save error: {e}")


def _pattern_key(advice_reason):
    """Extract a stable key from the advice reason.

    Uses first 80 chars of the reason, lowercased and stripped.
    Simple but effective — exact same advice = same key.
    """
    return advice_reason[:80].lower().strip()


def log_advice_outcome(command, advice_reason, outcome):
    """Log an ADVISE outcome (PROCEED or STOP).

    Args:
        command: The command that was advised on.
        advice_reason: The L2 advice reason string.
        outcome: "proceed" or "stop".
    """
    tracker = _load_tracker()
    key = _pattern_key(advice_reason)
    now = datetime.now().isoformat()

    if key not in tracker["patterns"]:
        tracker["patterns"][key] = {
            "advice": advice_reason,
            "first_seen": now,
            "proceed_count": 0,
            "stop_count": 0,
            "last_seen": now,
            "recent_commands": [],
        }

    entry = tracker["patterns"][key]
    entry["last_seen"] = now

    if outcome == "proceed":
        entry["proceed_count"] += 1
    else:
        entry["stop_count"] += 1

    # Keep last 5 commands for context
    entry["recent_commands"].append({"command": command[:120], "outcome": outcome, "time": now})
    entry["recent_commands"] = entry["recent_commands"][-5:]

    # Check if threshold reached for suggestion
    threshold = config.ADVISE_THRESHOLD
    if entry["proceed_count"] >= threshold:
        _maybe_create_suggestion(tracker, key, entry)

    _save_tracker(tracker)

    # Also log to SQLite
    try:
        from . import db
        db.init_db()
        db.log_advice(key, command[:120], advice_reason, outcome)
    except Exception:
        pass
    config.log(f"Advice tracked: {outcome} on '{key[:40]}' (proceed={entry['proceed_count']}, stop={entry['stop_count']})")


def _maybe_create_suggestion(tracker, key, entry):
    """Create a suggestion if this pattern has been ignored enough times."""
    # Don't duplicate existing suggestions for same pattern
    for s in tracker.get("suggestions", []):
        if s.get("pattern_key") == key and s.get("status") == "pending":
            return

    tracker.setdefault("suggestions", []).append({
        "pattern_key": key,
        "advice": entry["advice"],
        "proceed_count": entry["proceed_count"],
        "stop_count": entry["stop_count"],
        "recent_commands": entry["recent_commands"],
        "created": datetime.now().isoformat(),
        "status": "pending",  # pending, promoted, removed, kept
    })
    config.log(f"Suggestion created: '{key[:40]}' ignored {entry['proceed_count']}x")


def get_pending_suggestions():
    """Get all pending suggestions for the UI."""
    tracker = _load_tracker()
    return [s for s in tracker.get("suggestions", []) if s.get("status") == "pending"]


def resolve_suggestion(pattern_key, action):
    """Resolve a suggestion: promote, remove, or keep.

    Args:
        pattern_key: The pattern key to resolve.
        action: "promote" (move to L1), "remove" (delete from L2), "keep" (keep advising).
    """
    tracker = _load_tracker()

    for s in tracker.get("suggestions", []):
        if s.get("pattern_key") == pattern_key and s.get("status") == "pending":
            s["status"] = action
            s["resolved"] = datetime.now().isoformat()
            break

    if action == "remove":
        # Reset the counter so it doesn't resurface
        if pattern_key in tracker["patterns"]:
            tracker["patterns"][pattern_key]["proceed_count"] = 0
            tracker["patterns"][pattern_key]["stop_count"] = 0

    _save_tracker(tracker)
    config.log(f"Suggestion resolved: '{pattern_key[:40]}' -> {action}")
    return True


def get_stats():
    """Get advice tracker stats for CLI/dashboard."""
    tracker = _load_tracker()
    patterns = tracker.get("patterns", {})
    suggestions = tracker.get("suggestions", [])

    total_proceed = sum(p.get("proceed_count", 0) for p in patterns.values())
    total_stop = sum(p.get("stop_count", 0) for p in patterns.values())
    pending = len([s for s in suggestions if s.get("status") == "pending"])

    return {
        "patterns_tracked": len(patterns),
        "total_proceed": total_proceed,
        "total_stop": total_stop,
        "pending_suggestions": pending,
    }
