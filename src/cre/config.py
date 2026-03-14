"""Configuration loading — env vars, rules.json, paths."""

import json
import os
from datetime import datetime

# --- Paths ---
PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(PACKAGE_DIR))

# --- Load .env file (if present, OVERRIDES existing env vars) ---
_env_file = os.path.join(PROJECT_ROOT, ".env")
if os.path.exists(_env_file):
    with open(_env_file) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _key, _, _val = _line.partition("=")
                _key, _val = _key.strip(), _val.strip()
                os.environ[_key] = _val

# --- Environment-based config (all overridable) ---
LLM_API_URL = os.environ.get("CRE_LLM_API_URL", "https://api.openai.com/v1/chat/completions")
LLM_API_KEY = os.environ.get("CRE_LLM_API_KEY", "")
LLM_MODEL = os.environ.get("CRE_LLM_MODEL", "gpt-4o-mini")
LLM_FALLBACK_MODEL = os.environ.get("CRE_LLM_FALLBACK_MODEL", "gpt-4o-mini")
LLM_TIMEOUT = int(os.environ.get("CRE_LLM_TIMEOUT", "30"))
LOG_PATH = os.environ.get("CRE_LOG_PATH", "/tmp/cre.log")
SELF_LEARNING = os.environ.get("CRE_SELF_LEARNING", "false").lower() in ("true", "1", "yes")
SESSIONS_DIR = os.environ.get("CRE_SESSIONS_DIR", "")
CRE_ENABLED_PATH = os.environ.get("CRE_ENABLED_PATH", os.path.expanduser("~/.claude/cre_enabled"))
CRE_MD_PATH = os.environ.get("CRE_MD_PATH", os.path.join(PROJECT_ROOT, "CRE.md"))

# PIN override for L1 blocks
OVERRIDE_PIN = os.environ.get("CRE_OVERRIDE_PIN", "")
OVERRIDE_TTL = int(os.environ.get("CRE_OVERRIDE_TTL", "60"))

# SQLite database
DB_PATH = os.environ.get("CRE_DB_PATH", os.path.join(os.path.expanduser("~"), ".cre", "cre.db"))

# L2 advice tracking
ADVISE_THRESHOLD = int(os.environ.get("CRE_ADVISE_THRESHOLD", "5"))
ADVICE_LOG_PATH = os.environ.get("CRE_ADVICE_LOG", os.path.join(PROJECT_ROOT, "advice_tracker.json"))

# Rules path: check env, then project root, then package dir
_rules_env = os.environ.get("CRE_RULES_PATH", "")
if _rules_env:
    RULES_PATH = _rules_env
elif os.path.exists(os.path.join(PROJECT_ROOT, "rules.json")):
    RULES_PATH = os.path.join(PROJECT_ROOT, "rules.json")
else:
    RULES_PATH = os.path.join(PACKAGE_DIR, "rules.json")


# Knowledge base path
KB_PATH = os.environ.get("CRE_KB_PATH", os.path.join(PROJECT_ROOT, "knowledge_base.json"))


def log(msg):
    """Append to log file for debugging."""
    try:
        with open(LOG_PATH, "a") as f:
            f.write(f"[{datetime.now().isoformat()}] {msg}\n")
    except Exception:
        pass


def load_rules(path=None):
    """Load policy rules from JSON file."""
    p = path or RULES_PATH
    try:
        with open(p) as f:
            return json.load(f)
    except Exception as e:
        log(f"ERROR loading rules from {p}: {e}")
        return None


AUDIT_LOG_PATH = os.environ.get("CRE_AUDIT_LOG", os.path.join(PROJECT_ROOT, "audit.log"))


def save_rules(rules, path=None):
    """Write rules back to JSON file. Logs deletions to audit.log."""
    p = path or RULES_PATH

    # Load previous state to detect deletions
    try:
        with open(p) as f:
            old = json.load(f)
    except Exception:
        old = {}

    # Log removals to audit file
    now = datetime.now().isoformat()
    for cat in ("always_block", "always_allow", "needs_llm_review", "preferences"):
        old_items = old.get(cat, [])
        new_items = rules.get(cat, [])
        old_strs = {json.dumps(x, sort_keys=True) for x in old_items}
        new_strs = {json.dumps(x, sort_keys=True) for x in new_items}
        for removed_str in old_strs - new_strs:
            _audit(f"DELETED from {cat}: {removed_str}")

    with open(p, "w") as f:
        json.dump(rules, f, indent=2)
        f.write("\n")

    # Auto-sync preferences to KB so L1.5 picks them up immediately
    try:
        from .knowledge import sync_from_preferences
        sync_from_preferences()
    except Exception:
        pass  # KB sync is best-effort, don't break rule saves


def _audit(msg):
    """Append a line to the audit log."""
    try:
        with open(AUDIT_LOG_PATH, "a") as f:
            f.write(f"[{datetime.now().isoformat()}] {msg}\n")
    except Exception:
        pass


def is_enabled():
    """Check if the gate is enabled (toggle file exists)."""
    return os.path.exists(CRE_ENABLED_PATH)


def enable():
    """Enable the gate by creating toggle file."""
    os.makedirs(os.path.dirname(CRE_ENABLED_PATH), exist_ok=True)
    with open(CRE_ENABLED_PATH, "w") as f:
        pass


def disable():
    """Disable the gate by removing toggle file."""
    try:
        os.remove(CRE_ENABLED_PATH)
    except FileNotFoundError:
        pass


def get_env_display():
    """Return env var status for display (dashboard/CLI)."""
    api_key = LLM_API_KEY
    masked_key = (api_key[:8] + "..." + api_key[-4:]) if len(api_key) > 12 else ("***" if api_key else "")

    return {
        "CRE_LLM_API_KEY": {"value": masked_key, "default": "(not set)", "is_set": bool(api_key)},
        "CRE_LLM_API_URL": {"value": os.environ.get("CRE_LLM_API_URL", ""), "default": "https://api.openai.com/v1/chat/completions", "is_set": "CRE_LLM_API_URL" in os.environ},
        "CRE_LLM_MODEL": {"value": os.environ.get("CRE_LLM_MODEL", ""), "default": "gpt-4o-mini", "is_set": "CRE_LLM_MODEL" in os.environ},
        "CRE_LLM_TIMEOUT": {"value": os.environ.get("CRE_LLM_TIMEOUT", ""), "default": "30", "is_set": "CRE_LLM_TIMEOUT" in os.environ},
        "CRE_LOG_PATH": {"value": os.environ.get("CRE_LOG_PATH", ""), "default": "/tmp/cre.log", "is_set": "CRE_LOG_PATH" in os.environ},
        "CRE_RULES_PATH": {"value": os.environ.get("CRE_RULES_PATH", ""), "default": "./rules.json", "is_set": "CRE_RULES_PATH" in os.environ},
        "CRE_SELF_LEARNING": {"value": os.environ.get("CRE_SELF_LEARNING", ""), "default": "false", "is_set": "CRE_SELF_LEARNING" in os.environ},
    }
