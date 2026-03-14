"""
Claude Rule Enforcer — Gate Engine (tool-agnostic)

Layer 1: Fast regex matching (<10ms) — blocks/allows known patterns instantly.
Layer 2: LLM reasoning (2-5s) — reviews ambiguous commands against conversation context.
Multi-tool: Handles bash (command check), write/edit (intent check).

Protocol-agnostic: uses adapters to translate between AI coding tools
(Claude Code, Codex, Cursor, etc.) and CRE's internal decision engine.

When called as `cre gate`: reads JSON from stdin, auto-detects format, outputs decision.
"""

import hashlib
import json
import os
import re
import signal
import sys
import time
from datetime import datetime, timedelta

import requests

from . import config
from .adapters import get_adapter
from .session import search_recent_conversation, read_live_session
from .advice_tracker import log_advice_outcome

# --- CRE.md Management ---

CRE_MD_MAX_CHARS = 8000
CRE_MD_MAX_AGE_DAYS = 14


def _load_cre_context():
    """Read CRE.md and return its content (capped at 8K chars)."""
    try:
        if os.path.exists(config.CRE_MD_PATH):
            with open(config.CRE_MD_PATH, 'r') as f:
                content = f.read()
            return content[:CRE_MD_MAX_CHARS]
    except Exception as e:
        config.log(f"CRE.md read error: {e}")
    return ""


def _log_enforcement_event(command, decision, reason, tool_type="bash"):
    """Append an enforcement event to CRE.md Active Patterns section."""
    try:
        update_cre_md("enforcement", command, decision, reason, tool_type=tool_type)
    except Exception as e:
        config.log(f"CRE.md log error: {e}")


def update_cre_md(action, command, decision, reason, tool_type="bash"):
    """Manage CRE.md sections: append entries, trim old ones.

    Sections:
    - Active Patterns — timestamped L2 decisions
    - Override Log — when user overrides L2
    - Emerging Patterns — repeated denials not yet promoted
    - Rule Rationale — why key rules exist
    """
    path = config.CRE_MD_PATH
    now = datetime.now()
    timestamp = now.strftime("%Y-%m-%d %H:%M")

    # Read existing content
    content = ""
    if os.path.exists(path):
        try:
            with open(path, 'r') as f:
                content = f.read()
        except Exception:
            pass

    # Initialize sections if missing
    if "## Active Patterns" not in content:
        content = "# CRE Memory\n\n## Active Patterns\n\n## Override Log\n\n## Emerging Patterns\n\n## Rule Rationale\n"

    # Build the new entry
    cmd_short = command[:120] if command else "?"
    entry = f"- [{timestamp}] {decision.upper()} ({tool_type}): `{cmd_short}` — {reason}\n"

    if action == "enforcement":
        # Insert after "## Active Patterns" header
        content = _insert_after_section(content, "## Active Patterns", entry)
    elif action == "override":
        content = _insert_after_section(content, "## Override Log", entry)
    elif action == "rationale":
        content = _insert_after_section(content, "## Rule Rationale", entry)

    # Trim entries older than 14 days
    content = _trim_old_entries(content, now)

    # Cap total size
    if len(content) > CRE_MD_MAX_CHARS:
        content = _trim_to_size(content, CRE_MD_MAX_CHARS)

    # Write back
    try:
        os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)
        with open(path, 'w') as f:
            f.write(content)
    except Exception as e:
        config.log(f"CRE.md write error: {e}")


def _insert_after_section(content, section_header, entry):
    """Insert entry right after a section header line."""
    lines = content.split('\n')
    result = []
    inserted = False
    for line in lines:
        result.append(line)
        if not inserted and line.strip() == section_header:
            result.append(entry.rstrip())
            inserted = True
    if not inserted:
        result.append(section_header)
        result.append(entry.rstrip())
    return '\n'.join(result)


def _trim_old_entries(content, now):
    """Remove entries older than CRE_MD_MAX_AGE_DAYS."""
    cutoff = now - timedelta(days=CRE_MD_MAX_AGE_DAYS)
    lines = content.split('\n')
    result = []
    for line in lines:
        # Match timestamped entries: - [YYYY-MM-DD HH:MM]
        if line.strip().startswith('- [') and ']' in line:
            try:
                ts_str = line.split('[')[1].split(']')[0]
                entry_time = datetime.strptime(ts_str, "%Y-%m-%d %H:%M")
                if entry_time < cutoff:
                    continue  # Skip old entry
            except (ValueError, IndexError):
                pass  # Keep entries we can't parse
        result.append(line)
    return '\n'.join(result)


def _trim_to_size(content, max_chars):
    """Trim content to max size by removing oldest entries first."""
    lines = content.split('\n')
    # Remove from the end of each section (oldest entries are at bottom after inserts)
    while len('\n'.join(lines)) > max_chars and len(lines) > 10:
        # Find last timestamped entry and remove it
        for i in range(len(lines) - 1, -1, -1):
            if lines[i].strip().startswith('- ['):
                lines.pop(i)
                break
        else:
            break  # No more entries to remove
    return '\n'.join(lines)


def get_cre_md_stats():
    """Return stats about CRE.md for CLI/dashboard."""
    path = config.CRE_MD_PATH
    if not os.path.exists(path):
        return {"exists": False, "size": 0, "entries": 0, "path": path}

    try:
        with open(path, 'r') as f:
            content = f.read()
        entries = sum(1 for line in content.split('\n') if line.strip().startswith('- ['))
        return {
            "exists": True,
            "size": len(content),
            "entries": entries,
            "path": path,
        }
    except Exception:
        return {"exists": False, "size": 0, "entries": 0, "path": path}


def clear_cre_md():
    """Clear CRE.md content."""
    path = config.CRE_MD_PATH
    try:
        with open(path, 'w') as f:
            f.write("# CRE Memory\n\n## Active Patterns\n\n## Override Log\n\n## Emerging Patterns\n\n## Rule Rationale\n")
        return True
    except Exception as e:
        config.log(f"CRE.md clear error: {e}")
        return False


# --- Prompts ---

# L2a: Permission + recent approval check.
# Standing rules + last 3 messages (catches both preferences AND direct approvals).
PERMISSION_CHECK_PROMPT = """You are a permission checker. You check TWO things:
1. Does a STANDING RULE allow or deny this command?
2. Did the USER explicitly approve this action in the last few messages?

STANDING RULES:
{preferences}

LAST FEW MESSAGES (most recent exchange):
{recent_exchange}

COMMAND:
{command}

APPLICABLE RULE:
{rule_context}

Instructions:
- If a standing rule grants permission for this type of command → ALLOW.
- If a standing rule explicitly restricts this command → DENY.
- ONLY user messages count as approval. The assistant proposing, discussing, or planning an action is NEVER approval.
- The user must have explicitly said something like "yes", "do it", "go ahead", "push it", "yup", "crack on" in response to a proposal.
- The assistant saying "shall I push?" or "ready to push" is NOT the user approving.
- For IRREVERSIBLE operations (git push, deploy, delete, drop, rm -rf, production SSH), require EXPLICIT user approval in the last 2-3 messages. Task-level approval ("fix the code") does NOT cover pushing or deploying.
- Read ONLY the literal text of standing rules. Do not infer or hallucinate details not written.
- If a rule says "any servers" or "all servers", it means ANY server.
- If NEITHER a standing rule NOR explicit user approval covers this command → NONE.
- When in doubt, prefer NONE over ALLOW. False denials are recoverable. False allows are not.

Respond with EXACTLY one JSON object:
{{"decision": "ALLOW" or "DENY" or "NONE", "reason": "one sentence explanation"}}"""

# L2b: Intent check — did the user ask for this?
# Conversation context only. No preferences (L2a handles those).
INTENT_CHECK_BASH_PROMPT = """You are an intent checker for Claude Code. Your ONLY job: did the USER (not the assistant) ask for this command?

Read the conversation below. Only USER messages count as approval. The assistant proposing, planning, or discussing an action is NEVER approval.

RULES:
- Direct user commands are approval: "ssh to 100", "push it", "restart nginx" = YES.
- Short user replies after a proposal are approval: "yes", "do it", "go", "yup", "crack on" = YES for the proposed action.
- Task-level approval: "update the skill on 249" approves SSH, rsync, etc. needed for that task.
- EXCEPT for irreversible operations: git push, deploy, delete, drop, rm -rf, production access. These require EXPLICIT user approval specific to that action. Task-level approval does NOT cover them.
- Questions are NOT approval: "can you SSH?" or "could we push?" = NO.
- The assistant saying it will do something is NOT the user approving it. Only the user's words count.
- Vague complaints are NOT approval for destructive ops: "the repo is a mess" does NOT approve rm -rf.
- If no user request exists for this action anywhere in the conversation = NO.
- RECENCY DOMINATES: the most recent 2-3 user messages define the current task. Older messages are background only.
- Users change topics freely. A new instruction COMPLETELY overrides whatever came before.
- When the most recent user messages clearly indicate a new task, IGNORE older unrelated context entirely.
- When in doubt, DENY. False denials are recoverable. False allows are not.

CONVERSATION (most recent messages):
{memory_results}

COMMAND BEING ATTEMPTED:
{command}

Did the USER ask for this? Respond with EXACTLY one JSON object:
{{"decision": "ALLOW" or "DENY", "reason": "one sentence explanation"}}"""

INTENT_CHECK_WRITE_PROMPT = """You are an intent checker for Claude Code. Claude is attempting to {action_type} a file.
Your ONLY job: did the user's task require this file action, or is Claude acting without instruction?

RULES:
- If the user asked to create/modify/write/implement something that needs this file → ALLOW.
- The user does NOT need to name the exact filename. "Build it", "fix it", "create a plan" covers files Claude needs.
- Task-level approval: "yes build it" approves all files needed for the build.
- If Claude said "I'll create X" and the user didn't object → tacit approval.
- Questions are NOT permission: "can you create a file?" = NO.
- If the user was only DISCUSSING something and Claude started building without being asked → DENY.
- If there is NO user request for file creation/editing in the conversation → DENY.
- RECENCY: most recent user messages take priority. Older context can be superseded.

CONVERSATION (most recent messages):
{memory_results}

FILE ACTION:
  Tool: {tool_name}
  Path: {file_path}
  Content preview: {content_preview}

Did the user ask for this? Respond with EXACTLY one JSON object:
{{"decision": "ALLOW" or "DENY", "reason": "one sentence explanation"}}"""

ALIGNMENT_CHECK_PROMPT = """You are an instruction alignment checker for Claude Code. The user gave instructions and the AI is attempting to use a tool. Your job: determine if the AI's tool choice ALIGNS with the user's instructions.

RULES:
1. If the user NAMED a specific tool or method (e.g. "use grounded", "run the research agent", "via the email skill") → the AI MUST use that tool, not a substitute. DENY substitutions.
2. If the user did NOT name a specific tool → any reasonable tool choice is fine → ALLOW.
3. Prerequisite steps are fine. E.g. if the user said "use the research skill", the AI reading the skill file first is ALLOWED.
4. Reading files (Read, Glob, Grep) as preparation is always ALLOWED.
5. If in doubt, ALLOW. This check is advisory, not security.
6. RECENCY PRIORITY: The MOST RECENT user instruction is what matters. If earlier messages say one thing but the latest user message says "go" or approves a task list, the recent instruction takes precedence. Never block based on stale context.

CONVERSATION CONTEXT (recent messages):
{memory_results}

TOOL BEING CALLED:
  Tool: {tool_name}
  Input: {tool_input_preview}

USER'S INSTRUCTION (detected):
  "{instruction}"

Respond with EXACTLY one JSON object:
{{"decision": "ALLOW" or "DENY", "reason": "one sentence explanation"}}"""


LEARNING_PROMPT = """You are a rule-learning agent. The user just said something while a command was being reviewed.
Your job: determine if the user's message grants a STANDING PERMISSION (not a one-time approval).

STANDING PERMISSION examples (learn these):
- "you always have permission to SSH to the dev server" → standing rule
- "don't ever push without asking" → standing restriction
- "you can always access the NAS" → standing rule
- "never touch production" → standing restriction

ONE-TIME APPROVAL examples (do NOT learn these):
- "yes go ahead" → one-time, don't save
- "ok push it" → one-time for this push
- "ssh in and check" → one-time task

USER'S RECENT MESSAGES:
{recent_messages}

COMMAND THAT WAS REVIEWED:
{command}

If you detect a STANDING rule, respond with EXACTLY one JSON object:
{{"learned": true, "type": "always_allow" or "needs_llm_review" or "always_block", "pattern": "regex pattern to match", "context": "what the user said and why", "reason": "human-readable block reason (for always_block only)"}}

If this is just a one-time approval or no rule change needed:
{{"learned": false}}"""


def make_output(decision, reason):
    """Format output per Claude Code hook spec (legacy — use adapter instead)."""
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason
        }
    }


def _normalize_command(command):
    """Normalise a command string before L1 matching.

    Defeats trivial evasion techniques:
    - Shell string concatenation: "cre_""enabled" → "cre_enabled"
    - Empty quotes: rm""rf → rmrf (caught), cre_''enabled → cre_enabled
    - Variable-style obfuscation: $'cre_enabled' → cre_enabled

    This raises the bar significantly. An LLM can still evade with eval/base64/python,
    but those are complex enough that L2 (intent review) should catch them.
    """
    normalized = command
    # Remove empty string concatenation: "" and '' in the middle of tokens
    normalized = re.sub(r'""', '', normalized)
    normalized = re.sub(r"''", '', normalized)
    # Remove $'' ANSI-C quoting wrappers (simple case)
    normalized = re.sub(r"\$'([^']*)'", r'\1', normalized)
    # Remove shell variable assignment used as indirection: f="path" && rm "$f" → keep as-is
    # but resolve simple cases: "$HOME/.claude/cre_" "enabled" → normalise quotes
    normalized = re.sub(r'"\s*"', '', normalized)  # "foo" "bar" → foobar (adjacent strings)
    return normalized


def _split_command_segments(command):
    """Split a compound command into segments on |, ;, &&, ||.
    Returns list of stripped segments. Preserves the full command as first check."""
    # Split on shell operators (outside quotes — simplified, good enough for L1)
    segments = re.split(r'\s*(?:\|\||&&|[|;])\s*', command)
    return [s.strip() for s in segments if s.strip()]


def _check_single_segment(segment, rules):
    """Check a single command segment against rules.
    Returns: ("allow", reason), ("deny", reason), ("review", reason), or None if no match.
    """
    for rule in rules.get("always_block", []):
        pattern = rule.get("pattern", "")
        try:
            if re.search(pattern, segment):
                return ("deny", f"Blocked: {rule.get('reason', 'matches block rule')}")
        except re.error:
            if pattern in segment:
                return ("deny", f"Blocked: {rule.get('reason', 'matches block rule')}")

    for rule in rules.get("always_allow", []):
        pattern = rule.get("pattern", "")
        if not pattern:
            continue
        try:
            if re.search(pattern, segment):
                return ("allow", "Safe command pattern")
        except re.error:
            pass

    for rule in rules.get("needs_llm_review", []):
        pattern = rule.get("pattern", "")
        try:
            if re.search(pattern, segment):
                return ("review", rule.get("context", "Needs review"))
        except re.error:
            if pattern in segment:
                return ("review", rule.get("context", "Needs review"))

    return None  # no match


def regex_check(command, rules):
    """Layer 1: Fast regex matching with pipe-aware scanning.
    Splits compound commands (|, ;, &&, ||) and checks each segment.
    Block wins over review wins over allow. Any blocked segment = deny.
    Returns: ("allow", reason), ("deny", reason), or ("review", matched_rule).
    """
    # Normalise command to defeat trivial evasion (string concat, empty quotes)
    normalized_cmd = _normalize_command(command)

    # Check BOTH raw and normalised against block patterns
    for rule in rules.get("always_block", []):
        pattern = rule.get("pattern", "")
        try:
            if re.search(pattern, command) or re.search(pattern, normalized_cmd):
                return ("deny", f"Blocked: {rule.get('reason', 'matches block rule')}")
        except re.error:
            if pattern in command or pattern in normalized_cmd:
                return ("deny", f"Blocked: {rule.get('reason', 'matches block rule')}")

    # Split into segments and check each independently
    segments = _split_command_segments(command)

    # If single segment (no pipes/chains), use direct check
    if len(segments) <= 1:
        result = _check_single_segment(command, rules)
        if result:
            return result
        return ("allow", "No policy rule matched")

    # Multi-segment: check each. Block > Review > Allow
    has_review = None
    has_allow = None
    for seg in segments:
        result = _check_single_segment(seg, rules)
        if result is None:
            continue
        if result[0] == "deny":
            return result  # any block = instant deny
        if result[0] == "review" and not has_review:
            has_review = result
        if result[0] == "allow" and not has_allow:
            has_allow = result

    # Review beats allow in compound commands
    if has_review:
        return has_review
    if has_allow:
        return has_allow
    return ("allow", "No policy rule matched")


def _get_preferences_text(rules):
    """Build preferences text from approved rules for L2 prompt injection."""
    prefs = rules.get("preferences", [])
    if not prefs:
        return "No learned preferences yet."
    lines = []
    for p in prefs:
        lines.append(f"- {p.get('rule', '')} (confidence: {p.get('confidence', 'unknown')})")
    return "\n".join(lines)


def _get_extra_llm_params():
    """Return model-specific extra params for the LLM API call.
    MiniMax M2.5 needs reasoning_split to separate thinking from content.
    Other OpenAI-compatible APIs get no extra params."""
    model = config.LLM_MODEL.lower()
    if "minimax" in model:
        return {"reasoning_split": True}
    if "glm" in model:
        return {"enable_thinking": False}
    return {}


def _call_llm(prompt, user_msg, retries=3):
    """Send a prompt to the configured LLM. Retries on 429 with backoff (follows QP pattern). Returns parsed JSON or None."""
    if not config.LLM_API_KEY:
        return None

    for attempt in range(retries):
        try:
            resp = requests.post(
                config.LLM_API_URL,
                headers={
                    "Authorization": f"Bearer {config.LLM_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": config.LLM_MODEL,
                    "messages": [
                        {"role": "system", "content": prompt},
                        {"role": "user", "content": user_msg}
                    ],
                    "temperature": 0.1,
                    "max_tokens": 1024,
                    **(_get_extra_llm_params()),
                },
                timeout=config.LLM_TIMEOUT
            )
            # Some providers return 429 for both rate limits AND content filters
            if resp.status_code == 429 and attempt < retries - 1:
                body_text = resp.text.lower()
                filter_keywords = ["content_filter", "sensitive", "safety", "moderation", "prohibited", "filtered"]
                if any(kw in body_text for kw in filter_keywords):
                    config.log(f"LLM content filtered — not retrying")
                    return None
                wait = 10 * (attempt + 1)  # 10s, 20s (matches QP backoff)
                config.log(f"LLM 429 rate limited, retry {attempt + 1}/{retries} in {wait}s")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            content = resp.json()['choices'][0]['message']['content'].strip()
            content = content.replace("```json", "").replace("```", "").strip()
            # Strip MiniMax M2.5 thinking tags
            content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()
            return json.loads(content)

        except requests.Timeout:
            if attempt < retries - 1:
                wait = 10 * (attempt + 1)
                config.log(f"LLM timeout, retry {attempt + 1}/{retries} in {wait}s")
                time.sleep(wait)
                continue
            config.log("LLM timeout after all retries")
            return None
        except json.JSONDecodeError as e:
            config.log(f"LLM JSON parse error: {e} | raw content: {content[:200] if 'content' in dir() else 'N/A'}")
            break  # fall through to fallback model
        except Exception as e:
            err_str = str(e)
            is_rate_limit = "429" in err_str and not any(
                kw in err_str.lower() for kw in ["content_filter", "sensitive", "safety", "filtered"]
            )
            if (is_rate_limit or "timeout" in err_str.lower()) and attempt < retries - 1:
                wait = 10 * (attempt + 1)
                config.log(f"LLM {'rate limited' if is_rate_limit else 'timeout'}, retry {attempt + 1}/{retries} in {wait}s")
                time.sleep(wait)
                continue
            config.log(f"LLM error: {e}")
            return None

    # --- Fallback to secondary model if primary failed ---
    if config.LLM_FALLBACK_MODEL and config.LLM_FALLBACK_MODEL != config.LLM_MODEL:
        config.log(f"Primary model {config.LLM_MODEL} failed, trying fallback {config.LLM_FALLBACK_MODEL}")
        try:
            resp = requests.post(
                config.LLM_API_URL,
                headers={
                    "Authorization": f"Bearer {config.LLM_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": config.LLM_FALLBACK_MODEL,
                    "messages": [
                        {"role": "system", "content": prompt},
                        {"role": "user", "content": user_msg}
                    ],
                    "temperature": 0.1,
                    "max_tokens": 1024,
                    **(_get_extra_llm_params()),
                },
                timeout=config.LLM_TIMEOUT
            )
            resp.raise_for_status()
            content = resp.json()['choices'][0]['message']['content'].strip()
            content = content.replace("```json", "").replace("```", "").strip()
            content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()
            config.log(f"Fallback {config.LLM_FALLBACK_MODEL} succeeded")
            return json.loads(content)
        except Exception as e:
            config.log(f"Fallback {config.LLM_FALLBACK_MODEL} also failed: {e}")
            return None

    return None


def call_permission_check(command, rule_context, rules):
    """L2a: Check standing preferences + last 3 messages for direct approvals."""
    if not config.LLM_API_KEY:
        return "NONE", "No API key"

    prefs_text = _get_preferences_text(rules)

    # Fetch last 3 messages for direct approval detection
    recent_msgs = search_recent_conversation(command)
    recent_exchange = "No recent messages."
    if recent_msgs:
        lines = []
        for r in recent_msgs[-3:]:
            ts = r.get('timestamp', 'unknown')[:19]
            content = r['content']
            if r['role'] == 'assistant' and len(content) > 200:
                content = content[:200] + '...'
            lines.append(f"[{ts}] {r['role']}: {content}")
        recent_exchange = "\n".join(lines)

    prompt = PERMISSION_CHECK_PROMPT.format(
        preferences=prefs_text,
        recent_exchange=recent_exchange,
        command=command,
        rule_context=rule_context
    )

    result = _call_llm(prompt, f"Does a standing rule or recent approval cover this? Command: {command}")
    if result is None:
        return "NONE", "Permission check failed"

    decision = result.get("decision", "NONE").upper()
    reason = result.get("reason", "No reason given")
    return decision, reason


def call_intent_check(command, memory_results):
    """L2b: Check if the user asked for this command. Conversation context only, no preferences."""
    if not config.LLM_API_KEY:
        return "DENY", "CRE_LLM_API_KEY not set"

    if not memory_results:
        return "DENY", "No recent conversation found. No permission has been granted."

    # Build conversation text — last 5 exchanges as dialogue (fewer = less topic confusion)
    lines = []
    recent = memory_results[-5:] if memory_results else []
    for r in recent:
        ts = r.get('timestamp', 'unknown')[:19]
        content = r['content']
        if r['role'] == 'assistant' and len(content) > 300:
            content = content[:300] + '...'
        lines.append(f"[{ts}] {r['role']}: {content}")
    memory_text = "\n".join(lines)

    prompt = INTENT_CHECK_BASH_PROMPT.format(
        memory_results=memory_text,
        command=command
    )

    result = _call_llm(prompt, f"Did the user ask for this? Command: {command}")
    if result is None:
        return "DENY", "Intent check could not get LLM response"

    return result.get("decision", "DENY").upper(), result.get("reason", "No reason given")


def call_intent_review(tool_name, file_path, content_preview, memory_results, rules=None):
    """L2b for Write/Edit: Check if the user asked for this file action."""
    if not config.LLM_API_KEY:
        return "DENY", "CRE_LLM_API_KEY not set"

    if not memory_results:
        return "DENY", "No recent conversation found."

    action_type = "write" if tool_name == "Write" else "edit"

    # Build conversation text — last 5 exchanges (fewer = less topic confusion)
    lines = []
    recent = memory_results[-5:] if memory_results else []
    for r in recent:
        ts = r.get('timestamp', 'unknown')[:19]
        content = r['content']
        if r['role'] == 'assistant' and len(content) > 300:
            content = content[:300] + '...'
        lines.append(f"[{ts}] {r['role']}: {content}")
    memory_text = "\n".join(lines)

    prompt = INTENT_CHECK_WRITE_PROMPT.format(
        action_type=action_type,
        memory_results=memory_text,
        tool_name=tool_name,
        file_path=file_path,
        content_preview=content_preview[:500]
    )

    result = _call_llm(prompt, f"Did the user ask Claude to {action_type} {file_path}?")
    if result is None:
        return "DENY", "Intent check could not get LLM response"

    return result.get("decision", "DENY").upper(), result.get("reason", "No reason given")


def learn_from_conversation(command, memory_results, rules):
    """Ask the LLM if recent conversation implies a standing rule change."""
    if not config.SELF_LEARNING:
        return
    if not memory_results or not config.LLM_API_KEY:
        return

    recent_text = "\n".join(
        f"[{r.get('timestamp', '?')[:19]}] {r['content']}"
        for r in memory_results[:5]
    )

    prompt = LEARNING_PROMPT.format(
        recent_messages=recent_text,
        command=command
    )

    result = _call_llm(prompt, "Did the user grant a standing permission or restriction?")
    if result and result.get("learned"):
        _apply_learned_rule(result, rules)


def _apply_learned_rule(learned, rules):
    """Write a learned rule into rules.json."""
    from datetime import datetime

    rule_type = learned.get("type", "")
    pattern = learned.get("pattern", "")
    if not pattern or not rule_type:
        return

    existing_patterns = [r.get("pattern", "") for r in rules.get(rule_type, [])]
    if pattern in existing_patterns:
        config.log(f"LEARN: duplicate pattern '{pattern}' in {rule_type}, skipping")
        return

    new_rule = {"pattern": pattern}
    if rule_type == "needs_llm_review":
        new_rule["context"] = learned.get("context", "Learned from conversation")
    if rule_type == "always_block":
        new_rule["reason"] = learned.get("reason", "Learned restriction")

    if rule_type in rules:
        rules[rule_type].append(new_rule)

    learned_entry = {
        "timestamp": datetime.now().isoformat(),
        "type": rule_type,
        "pattern": pattern,
        "context": learned.get("context", ""),
        "source": "llm_learning"
    }
    rules.setdefault("learned_rules", []).append(learned_entry)
    config.save_rules(rules)
    config.log(f"LEARNED: Added '{pattern}' to {rule_type} — {learned.get('context', '')}")


# --- Instruction Alignment (L1 triage + L2 check) ---

INSTRUCTION_PATTERNS = [
    re.compile(r'\buse\s+(?:the\s+)?(\w[\w\-]+)', re.IGNORECASE),
    re.compile(r'\bvia\s+(?:the\s+)?(\w[\w\-]+)', re.IGNORECASE),
    re.compile(r'\b(?:run|execute|call)\s+(?:the\s+)?(\w[\w\-]+)', re.IGNORECASE),
    re.compile(r'\b(?:search|ask|query)\s+(?:the\s+)?(?:with\s+)?(\w[\w\-]+)', re.IGNORECASE),
    re.compile(r'\bcheck\s+(?:the\s+)?(?:database|db|sqlite|memory|grounded)', re.IGNORECASE),
]


def _has_tool_instruction(messages):
    """L1 triage: scan recent user messages for tool/method instructions.
    Returns the matched instruction string, or None if no instruction detected.
    """
    for msg in reversed(messages):
        if msg.get("role") != "user":
            continue
        text = msg.get("content", "")
        for pattern in INSTRUCTION_PATTERNS:
            m = pattern.search(text)
            if m:
                return m.group(0)
    return None


def call_alignment_review(tool_name, tool_input, instruction, memory_results):
    """L2 alignment check: ask LLM if the tool call matches the user's instruction."""
    if not config.LLM_API_KEY:
        return "ALLOW", "No LLM key — allowing (alignment is advisory)"

    if not memory_results:
        memory_text = "No recent conversation found."
    else:
        lines = []
        for r in memory_results:
            ts = r.get('timestamp', 'unknown')[:19]
            lines.append(f"[{ts}] {r['role']}: {r['content']}")
        memory_text = "\n".join(lines)

    tool_input_str = json.dumps(tool_input, default=str)[:500] if tool_input else "{}"

    prompt = ALIGNMENT_CHECK_PROMPT.format(
        memory_results=memory_text,
        tool_name=tool_name,
        tool_input_preview=tool_input_str,
        instruction=instruction
    )

    result = _call_llm(prompt, f"Is {tool_name} aligned with the user's instruction: \"{instruction}\"?")
    if result is None:
        # Default to ALLOW on LLM failure — alignment is advisory, not security
        return "ALLOW", "Alignment check could not get LLM response — allowing by default"

    return result.get("decision", "ALLOW").upper(), result.get("reason", "No reason given")


def _evaluate_alignment(normalized, rules):
    """Evaluate a non-bash/non-write tool through alignment check. Returns (decision, reason)."""
    tool_name = normalized.get("tool_name", normalized["tool_type"])
    tool_input = normalized.get("tool_input", {})

    config.log(f"Alignment check: {tool_name}")

    # Check if alignment is enabled
    if not rules.get("alignment_check_enabled", True):
        config.log(f"Alignment SKIP (disabled), allowing {tool_name}")
        return "allow", "Alignment check disabled"

    # L1 triage: scan conversation for tool instructions
    memory_results = search_recent_conversation(tool_name)
    instruction = _has_tool_instruction(memory_results)

    if not instruction:
        config.log(f"Alignment L1: no instruction detected, allowing {tool_name}")
        return "allow", "No specific tool instruction from user"

    # L2: LLM alignment review
    if not rules.get("llm_review_enabled", True):
        config.log(f"Alignment L2 SKIP (LLM disabled), allowing {tool_name}")
        return "allow", "LLM review disabled"

    config.log(f"Alignment L2: instruction='{instruction}', tool={tool_name}")
    llm_decision, llm_reason = call_alignment_review(
        tool_name, tool_input, instruction, memory_results
    )
    config.log(f"Alignment L2: {llm_decision} — {llm_reason}")

    # Log L2 decision to CRE.md
    tool_desc = f"{tool_name}:{json.dumps(tool_input, default=str)[:80]}" if tool_input else tool_name
    _log_enforcement_event(tool_desc, llm_decision.lower(), llm_reason, tool_type="alignment")

    if llm_decision == "ALLOW":
        return "allow", llm_reason
    # Alignment deny is advisory — warn but allow
    config.log(f"Alignment WARN (advisory): {llm_reason}")
    return "allow", f"⚠ Alignment warning: {llm_reason}"


def process_bash(hook_input, rules):
    """Legacy wrapper — delegates to _evaluate_bash. Kept for test compatibility."""
    from .adapters.claude_code import ClaudeCodeAdapter
    adapter = ClaudeCodeAdapter()
    normalized = adapter.parse_input(hook_input)
    decision, reason = _evaluate_bash(normalized, rules)
    if decision == "allow":
        return json.dumps({})
    return json.dumps(make_output("deny", reason))


def process_write_edit(hook_input, rules):
    """Legacy wrapper — delegates to _evaluate_write_edit. Kept for test compatibility."""
    from .adapters.claude_code import ClaudeCodeAdapter
    adapter = ClaudeCodeAdapter()
    normalized = adapter.parse_input(hook_input)
    decision, reason = _evaluate_write_edit(normalized, rules)
    if decision == "allow":
        return json.dumps({})
    return json.dumps(make_output("deny", reason))


def _get_kb_context(normalized):
    """Load knowledge base and match against the current tool call. Returns context string or None."""
    try:
        from .knowledge import load_kb, match_context_for_tool
        kb = load_kb()
        return match_context_for_tool(normalized, kb)
    except Exception as e:
        config.log(f"KB match error: {e}")
        return None


def gate_main(adapter_name=None):
    """Main entry point for `cre gate` — reads JSON from stdin, auto-detects format, outputs decision.

    Args:
        adapter_name: Force a specific adapter ("claude-code", "generic").
                      If None, auto-detects from input format.
    """
    # Absolute timeout safety
    if hasattr(signal, 'SIGALRM'):
        signal.signal(signal.SIGALRM, lambda s, f: sys.exit(2))
        signal.alarm(20)

    try:
        raw = sys.stdin.read()
        hook_input = json.loads(raw)
    except (json.JSONDecodeError, Exception) as e:
        config.log(f"Invalid input: {e}")
        # Can't auto-detect adapter from bad input — fall back to Claude Code format
        from .adapters.claude_code import ClaudeCodeAdapter
        adapter = ClaudeCodeAdapter()
        print(adapter.format_deny("Policy gate received invalid input"))
        sys.exit(adapter.exit_deny)

    # Get adapter (explicit or auto-detect)
    adapter = get_adapter(name=adapter_name, raw_input=hook_input)
    config.log(f"Adapter: {adapter.name}")

    # Initialize SQLite database
    try:
        from . import db
        db.init_db()
    except Exception:
        pass

    # Self-integrity check: verify CRE hooks haven't been removed
    _check_hook_integrity()

    # Check toggle file first (cre enable/disable)
    if not config.is_enabled():
        config.log("Gate disabled via toggle — allowing")
        print(adapter.format_allow())
        sys.exit(adapter.exit_allow)

    # Force-sync chat file before L2 reads it.
    # Without this, there's a timing race: UserPromptSubmit writes the user message
    # to the CC JSONL, but the CRE chat file may not have it yet when PreToolUse fires.
    try:
        from .chat_logger import sync_from_cc_jsonl
        sync_count = sync_from_cc_jsonl()
        if sync_count:
            config.log(f"Pre-gate sync: {sync_count} new messages")
    except Exception as e:
        config.log(f"Pre-gate sync failed: {e}")

    # Normalize input
    normalized = adapter.parse_input(hook_input)
    tool_type = normalized["tool_type"]

    # Load rules
    rules = config.load_rules()
    if not rules:
        config.log("No rules loaded — denying for safety")
        print(adapter.format_deny("Policy gate could not load rules"))
        sys.exit(adapter.exit_deny)

    # Check master enable flag in rules.json
    if not rules.get("enabled", True):
        print(adapter.format_allow())
        sys.exit(adapter.exit_allow)

    # Route by tool type
    if tool_type == "bash":
        decision, reason = _evaluate_bash(normalized, rules)
    elif tool_type in ("write", "edit"):
        decision, reason = _evaluate_write_edit(normalized, rules)
    else:
        decision, reason = _evaluate_alignment(normalized, rules)

    config.log(f"Final: {decision} — {reason}")

    # Log decision to SQLite
    try:
        db.log_event(
            session_id=os.environ.get("CRE_INSTANCE_ID", "unknown"),
            command=normalized.get("command", normalized.get("file_path", ""))[:200],
            decision=decision,
            reason=reason[:500],
            layer="gate",
            tool_name=normalized.get("tool_name", ""),
        )
    except Exception:
        pass

    if decision == "ask" and hasattr(adapter, 'exit_ask'):
        # PIN override in non-interactive mode: exit 1 (ask) with context on stderr
        print(adapter.format_ask(reason))
        sys.exit(adapter.exit_ask)
    elif decision == "allow":
        # L1.5: Knowledge base context injection
        context = _get_kb_context(normalized)
        if context and hasattr(adapter, 'format_allow_with_context'):
            # Append any advisory warning to context
            if reason.startswith("⚠"):
                context = f"{context}\n\n{reason}"
            config.log(f"KB inject: {context[:120]}")
            print(adapter.format_allow_with_context(context))
        else:
            print(adapter.format_allow())
        sys.exit(adapter.exit_allow)
    else:
        print(adapter.format_deny(reason))
        sys.exit(adapter.exit_deny)


def _check_pattern_promotion(command, decision, rules):
    """Legacy promotion check. Now handled by advice_tracker.

    The advice_tracker logs every PROCEED/STOP outcome.
    When a pattern hits CRE_ADVISE_THRESHOLD PROCEEDs, it creates
    a suggestion for the user to: promote to L1, remove, or keep.
    See advice_tracker.py for the new flow.
    """
    pass


def _check_pin_override(command):
    """Check if the USER (not the AI) has provided a PIN override for an L1 block.

    Reads recent user messages from the chat log. Only accepts PINs typed
    by the user in chat (role: "user"), never from assistant messages.
    This prevents the AI from approving its own overrides.

    Accepted formats in user messages:
        "PIN 1234", "pin 1234", "override 1234"

    One-time use: once consumed, the message won't trigger again.
    TTL applies based on message timestamp.
    """
    import re
    from datetime import datetime

    pin = config.OVERRIDE_PIN
    if not pin:
        return False

    messages = []

    # In delegate mode, use the adapter to read the active conversation
    if _is_non_interactive():
        try:
            from .adapters.delegate import DelegateAdapter
            delegate = DelegateAdapter()
            messages = delegate.read_user_messages(limit=10)
            if messages:
                config.log(f"PIN check: using delegate thread ({len(messages)} messages)")
        except Exception as e:
            config.log(f"PIN check: cannot read Amp thread: {e}")

    # Fallback to CRE's own chat log (Claude Code mode)
    if not messages:
        try:
            from .chat_logger import read_chat
            messages = read_chat(limit=10)
        except Exception as e:
            config.log(f"PIN check: cannot read CRE chat: {e}")

    if not messages:
        config.log(f"PIN check: no messages available")
        return False

    # Scan user messages (newest first) for PIN pattern
    pin_pattern = re.compile(r'\b(?:pin|override)\s+(\S+)', re.IGNORECASE)

    for msg in reversed(messages):
        if msg.get("role") != "user":
            continue

        content = msg.get("content", "")
        match = pin_pattern.search(content)
        if not match:
            continue

        user_pin = match.group(1).strip()

        # Check TTL from message timestamp
        try:
            ts = datetime.fromisoformat(msg.get("timestamp", ""))
            age = (datetime.now(ts.tzinfo) - ts).total_seconds() if ts.tzinfo else (datetime.now() - ts).total_seconds()
        except Exception:
            age = 0  # If no timestamp, treat as fresh

        if age > config.OVERRIDE_TTL:
            config.log(f"PIN override expired ({age:.0f}s > {config.OVERRIDE_TTL}s)")
            return False

        if user_pin == pin:
            config.log(f"PIN override from user message for: {command[:80]}")
            config._audit(f"PIN OVERRIDE (user-verified): `{command[:120]}`")
            return True
        else:
            config.log(f"PIN mismatch from user message")
            return False

    return False


_HOOK_BACKUP = None  # Cache of known-good hooks


def _check_hook_integrity():
    """Verify CRE hooks haven't been removed from config files.
    Runs on every gate call. If hooks are missing, auto-restores from
    the last known-good state. This is the last line of defence against
    AI tools editing config files to bypass CRE."""
    global _HOOK_BACKUP
    settings_path = os.path.expanduser("~/.claude/settings.json")
    if not os.path.exists(settings_path):
        return
    try:
        with open(settings_path) as f:
            data = json.load(f)
        hooks = data.get("hooks", {}).get("PreToolUse", [])

        # Check if CRE gate is present
        has_cre = any("cre" in json.dumps(h).lower() for h in hooks)

        if has_cre and hooks:
            # Save known-good state
            _HOOK_BACKUP = hooks
        elif _HOOK_BACKUP and not has_cre:
            # Hooks removed, restore from backup
            config.log("INTEGRITY: CRE hooks removed, auto-restoring")
            data["hooks"]["PreToolUse"] = _HOOK_BACKUP
            with open(settings_path, "w") as f:
                json.dump(data, f, indent=2)
            config.log("INTEGRITY: CRE hooks restored")
            config._audit("TAMPER DETECTED: CRE hooks were removed and auto-restored")
    except Exception as e:
        config.log(f"Integrity check error: {e}")


def _is_non_interactive():
    """Check if running in a non-interactive adapter (e.g. Amp delegate).
    Non-interactive adapters have no retry loop, so ADVISE = hard deny."""
    return bool(os.environ.get("AGENT_TOOL_NAME"))


def _check_advise_acknowledgement(command, advice_reason):
    """Check if AI has acknowledged an ADVISE by looking for a retry.

    On first ADVISE: block with message, AI must decide.
    On retry (same command within 60s): check conversation for PROCEED/STOP.
    Returns: ("proceed", reason) or ("stop", reason) or None (first time, block it).
    """
    cmd_hash = hashlib.md5(command.encode()).hexdigest()[:12]
    advise_path = f"/tmp/cre_advise_{cmd_hash}"

    # Check if this is a retry (advise file exists)
    if os.path.exists(advise_path):
        try:
            with open(advise_path) as f:
                data = json.load(f)
            age = time.time() - data.get("time", 0)
            if age > 60:
                os.remove(advise_path)
                return None  # Expired, treat as first time

            # It's a retry — the AI is proceeding despite advice
            os.remove(advise_path)
            log_advice_outcome(command, advice_reason, "proceed")
            return "proceed", advice_reason
        except Exception:
            pass

    # First time: create advise file and block
    try:
        with open(advise_path, "w") as f:
            json.dump({"time": time.time(), "advice": advice_reason[:200]}, f)
    except Exception:
        pass

    return None  # Signal: block with ADVISE message


def _evaluate_bash(normalized, rules):
    """Evaluate a bash command through L1 + L2. Returns (decision, reason)."""
    command = normalized["command"]
    if not command:
        return "allow", "Empty command"

    config.log(f"Checking: {command[:200]}")

    # Fast path: if this is a retry after ADVISE, skip L2 entirely
    # (Non-interactive adapters like Amp skip this — no retry loop exists)
    if not _is_non_interactive():
        ack = _check_advise_acknowledgement(command, "")
        if ack is not None:
            config.log(f"ADVISE acknowledged (PROCEED) — skipping L2: {command[:100]}")
            return "allow", f"⚠ Proceeded despite advice: {ack[1] or 'previous advisory'}"

    # L1: Regex (flat rules — allow or block, no escalation)
    decision, reason = regex_check(command, rules)

    if decision == "allow":
        config.log(f"L1 ALLOW: {reason}")
        # Fall through to L2 for advisory/context
    elif decision == "deny":
        # L1 hard block — check PIN override
        if _check_pin_override(command):
            config.log(f"L1 DENY overridden by PIN: {reason}")
            _log_enforcement_event(command, "allow", f"L1 DENY overridden by PIN: {reason}", tool_type="bash")
            # Non-interactive: return "ask" so context reaches the AI via stderr
            if _is_non_interactive():
                kb = _get_kb_context(normalized)
                context = f"PIN accepted. {kb}" if kb else "PIN accepted."
                config.log(f"PIN override (non-interactive ask): {context[:100]}")
                return "ask", context
            return "allow", f"PIN override: {reason}"
        config.log(f"L1 DENY: {reason}")
        return "deny", reason
    elif decision == "review":
        # Legacy needs_llm_review — treat as L1 allow, let L2 advise
        config.log(f"L1 no block (legacy review pattern), passing to L2")
        decision = "allow"

    # L2: LLM review (advisory — CONTEXT, ADVISE, or ALLOW)
    if not rules.get("llm_review_enabled", True):
        config.log(f"L2 SKIP (disabled)")
        return "allow", reason if decision == "allow" else f"L2 disabled: {reason}"

    if not config.LLM_API_KEY:
        config.log(f"L2 SKIP (no API key)")
        return "allow", reason if decision == "allow" else "L2 skipped (no API key)"

    # L2a: Permission check — does a standing rule cover this command?
    config.log(f"L2a permission check: {command[:120]}")
    perm_decision, perm_reason = call_permission_check(command, reason, rules)
    config.log(f"L2a: {perm_decision} — {perm_reason}")

    if perm_decision == "ALLOW":
        _log_enforcement_event(command, "allow", f"L2a: {perm_reason}", tool_type="bash")
        return "allow", f"Standing permission: {perm_reason}"

    if perm_decision == "DENY":
        # L2 DENY = ADVISE (L2 never blocks, it advises)
        _log_enforcement_event(command, "advise", f"L2a ADVISE: {perm_reason}", tool_type="bash")
        config.log(f"L2a ADVISE: {perm_reason}")

        # Forced acknowledgement — block until AI decides
        # Non-interactive adapters (Amp delegate) can't retry, so hard deny
        if _is_non_interactive():
            config.log(f"L2a ADVISE → hard deny (non-interactive adapter)")
            return "deny", perm_reason
        ack = _check_advise_acknowledgement(command, perm_reason)
        if ack is None:
            # First time: hard stop, AI must acknowledge
            return "deny", f"ADVISE: {perm_reason}. Acknowledge: retry to PROCEED, or do not retry to STOP."
        else:
            # AI retried = PROCEED (logged by _check_advise_acknowledgement)
            config.log(f"L2a ADVISE acknowledged (PROCEED): {perm_reason}")
            return "allow", f"⚠ Proceeded despite advice: {perm_reason}"

    # L2a returned NONE — no standing rule. Fall through to L2b.
    config.log(f"L2b intent check (L2a had no opinion)")
    memory_results = search_recent_conversation(command)
    config.log(f"L2b context: {len(memory_results)} messages")

    llm_decision, llm_reason = call_intent_check(command, memory_results)
    config.log(f"L2b: {llm_decision} — {llm_reason}")

    learn_from_conversation(command, memory_results, rules)

    _log_enforcement_event(command, llm_decision.lower(), f"L2b: {llm_reason}", tool_type="bash")

    if llm_decision == "ALLOW":
        return "allow", llm_reason

    # L2b DENY = ADVISE with forced acknowledgement
    config.log(f"L2b ADVISE: {llm_reason}")
    if _is_non_interactive():
        config.log(f"L2b ADVISE → hard deny (non-interactive adapter)")
        return "deny", llm_reason
    ack = _check_advise_acknowledgement(command, llm_reason)
    if ack is None:
        return "deny", f"ADVISE: {llm_reason}. Acknowledge: retry to PROCEED, or do not retry to STOP."
    else:
        config.log(f"L2b ADVISE acknowledged (PROCEED): {llm_reason}")
        return "allow", f"⚠ Proceeded despite advice: {llm_reason}"


def _evaluate_write_edit(normalized, rules):
    """Evaluate a write/edit through L2a permission + L2b intent check. Returns (decision, reason)."""
    tool_type = normalized["tool_type"]
    file_path = normalized["file_path"]
    content = normalized["content"]

    if not file_path:
        return "allow", "No file path"

    config.log(f"Checking {tool_type}: {file_path}")

    # Self-protection: block edits to CRE's own configuration
    protected_patterns = [
        "settings.json",
        "cre_enabled",
        "policy_gate_enabled",
        "rules.json",
        "hooks.json",
    ]
    for pattern in protected_patterns:
        if pattern in file_path:
            # Check if the content modifies CRE hooks
            content_lower = (content or "").lower()
            if any(kw in content_lower for kw in ["cre", "gate", "pretooluse", "hook", "delegate"]):
                config.log(f"Self-protection: blocked {tool_type} to {file_path} (CRE config)")
                return "deny", f"Cannot modify CRE configuration in {os.path.basename(file_path)}. CRE protects its own hooks."
            if "pretooluse" in file_path.lower() or pattern in ("cre_enabled", "policy_gate_enabled"):
                config.log(f"Self-protection: blocked {tool_type} to {file_path} (protected file)")
                return "deny", f"Cannot modify {os.path.basename(file_path)}. This file is protected by CRE."

    # Fast path: if this is a retry after ADVISE, skip the LLM call entirely
    # (Non-interactive adapters like Amp skip this — no retry loop exists)
    cmd_key = f"{tool_type} {file_path}"
    if not _is_non_interactive():
        ack = _check_advise_acknowledgement(cmd_key, "")
        if ack is not None:
            config.log(f"ADVISE acknowledged (PROCEED) — skipping L2: {file_path}")
            return "allow", f"⚠ Proceeded despite advice: {ack[1] or 'previous advisory'}"

    if not rules.get("llm_review_enabled", True):
        config.log(f"L2 SKIP (disabled), allowing {tool_type}")
        return "allow", "LLM review disabled"

    # L2a: Permission check — does a standing rule cover this file path?
    config.log(f"L2a permission check: {tool_type} {file_path}")
    perm_decision, perm_reason = call_permission_check(
        f"{tool_type} {file_path}", f"File {tool_type} operation", rules
    )
    config.log(f"L2a: {perm_decision} — {perm_reason}")

    if perm_decision == "ALLOW":
        _log_enforcement_event(file_path, "allow", f"L2a: {perm_reason}", tool_type=tool_type)
        return "allow", f"Standing permission: {perm_reason}"

    if perm_decision == "DENY":
        # L2 DENY = ADVISE with forced acknowledgement
        _log_enforcement_event(file_path, "advise", f"L2a ADVISE: {perm_reason}", tool_type=tool_type)
        config.log(f"L2a ADVISE: {perm_reason}")
        if _is_non_interactive():
            config.log(f"L2a ADVISE → hard deny (non-interactive adapter)")
            return "deny", perm_reason
        ack = _check_advise_acknowledgement(f"{tool_type} {file_path}", perm_reason)
        if ack is None:
            return "deny", f"ADVISE: {perm_reason}. Acknowledge: retry to PROCEED, or do not retry to STOP."
        else:
            config.log(f"L2a ADVISE acknowledged (PROCEED): {perm_reason}")
            return "allow", f"⚠ Proceeded despite advice: {perm_reason}"

    # L2a returned NONE — fall through to L2b intent check
    config.log(f"L2b intent check (L2a had no opinion)")
    memory_results = search_recent_conversation(file_path)
    config.log(f"L2b context: {len(memory_results)} messages")

    # Map back to Claude Code tool names for the prompt (works generically)
    tool_name = "Write" if tool_type == "write" else "Edit"
    llm_decision, llm_reason = call_intent_review(
        tool_name, file_path, content[:500], memory_results, rules
    )
    config.log(f"L2b intent: {llm_decision} — {llm_reason}")

    _log_enforcement_event(file_path, llm_decision.lower(), f"L2b: {llm_reason}", tool_type=tool_type)

    if llm_decision == "ALLOW":
        return "allow", llm_reason

    # L2b DENY = ADVISE with forced acknowledgement
    config.log(f"L2b ADVISE: {llm_reason}")
    if _is_non_interactive():
        config.log(f"L2b ADVISE → hard deny (non-interactive adapter)")
        return "deny", llm_reason
    ack = _check_advise_acknowledgement(f"{tool_type} {file_path}", llm_reason)
    if ack is None:
        return "deny", f"ADVISE: {llm_reason}. Acknowledge: retry to PROCEED, or do not retry to STOP."
    else:
        config.log(f"L2b ADVISE acknowledged (PROCEED): {llm_reason}")
        return "allow", f"⚠ Proceeded despite advice: {llm_reason}"
