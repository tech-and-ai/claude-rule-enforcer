"""
Learning Engine — The core IP of CRE.

Scans conversation history, detects patterns, suggests rules with evidence.
Two modes:
  1. Batch scan (`cre scan`) — reads all/recent session files
  2. Continuous (hourly cron) — reads last hour only

Suggestions are written to rules.json under `suggested_rules[]` with evidence chains.
The dashboard shows suggestions with Approve/Dismiss/Edit buttons.

v0.3.0: Suggestions can target any rule type (block/review/allow/preference).
         L2 pattern promotion and L1 refinement suggestions.
"""

import json
import uuid
from datetime import datetime

import requests

from . import config
from .session import scan_session_files


LEARNING_SCAN_PROMPT = """You are analysing conversation history between a developer and an AI coding assistant.
Find STANDING PREFERENCES — things the user consistently wants, corrections they've made
repeatedly, frustrations they've expressed about the AI's behaviour.

DO NOT flag one-time instructions. Only flag patterns that appear across multiple
conversations or are expressed as permanent rules.

For each pattern found, provide:
1. Evidence: exact quotes with timestamps
2. Interpretation: what this means for the AI's behaviour
3. Proposed rule: how to enforce this going forward
4. Confidence: HIGH (explicit rule stated), MEDIUM (pattern across conversations), LOW (single strong reaction)

CONVERSATION MESSAGES:
{messages}

Respond with a JSON array of suggestions. Each suggestion:
{{
  "evidence": [
    {{"timestamp": "ISO timestamp", "content": "exact quote from conversation"}}
  ],
  "interpretation": "what this means for the AI's behaviour",
  "proposed_rule": "how to enforce this going forward",
  "category": "preference" or "restriction" or "workflow" or "correction",
  "confidence": "high" or "medium" or "low"
}}

If no standing preferences found, return an empty array: []
Return ONLY the JSON array, no markdown wrapping."""


REFINEMENT_PROMPT = """You are analysing enforcement history to suggest refinements to existing rules.

EXISTING RULES:
{existing_rules}

OVERRIDE LOG (user overrode L2 decisions):
{override_log}

ACTIVE PATTERNS (recent L2 decisions):
{active_patterns}

Analyse the patterns and suggest refinements:

1. RULE TOO BROAD — if a rule is overridden >3 times, suggest narrowing its pattern
2. RULE NOT NEEDED — if a review rule always gets allowed, suggest removing it or moving to always_allow
3. PATTERN GAP — if similar commands are manually denied repeatedly, suggest a new block/review rule

For each refinement, respond with:
{{
  "suggestion_type": "narrow" or "broaden" or "remove",
  "target_category": "always_block" or "needs_llm_review" or "always_allow",
  "old_pattern": "the existing pattern to modify (if applicable)",
  "new_pattern": "the replacement pattern (for narrow/broaden)",
  "reason": "why this refinement is needed",
  "evidence_count": number of events supporting this
}}

If no refinements needed, return an empty array: []
Return ONLY the JSON array, no markdown wrapping."""


def scan_sessions(hours=None):
    """Read JSONL session files and return user messages.

    Args:
        hours: Scan files modified in last N hours. None = all files.

    Returns:
        List of message dicts.
    """
    return scan_session_files(hours=hours)


def extract_preferences(messages, batch_size=50):
    """Send message batches to LLM, extract preference suggestions.

    Args:
        messages: List of message dicts from scan_sessions().
        batch_size: How many messages per LLM call.

    Returns:
        List of suggestion dicts ready for rules.json.
    """
    if not config.LLM_API_KEY:
        return []

    all_suggestions = []

    # Process in batches
    for i in range(0, len(messages), batch_size):
        batch = messages[i:i + batch_size]
        msg_text = "\n".join(
            f"[{m.get('timestamp', '?')[:19]}] {m['content']}"
            for m in batch
        )

        prompt = LEARNING_SCAN_PROMPT.format(messages=msg_text)

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
                        {"role": "user", "content": "Extract standing preferences from these conversations."}
                    ],
                    "temperature": 0.1,
                    "max_tokens": 4096,
                    "enable_thinking": False,
                },
                timeout=config.LLM_TIMEOUT
            )
            resp.raise_for_status()
            content = resp.json()['choices'][0]['message']['content'].strip()
            content = content.replace("```json", "").replace("```", "").strip()

            if not content or content == "[]":
                continue

            suggestions = json.loads(content)
            if not isinstance(suggestions, list):
                continue

            for s in suggestions:
                s["id"] = f"sug_{uuid.uuid4().hex[:8]}"
                s["status"] = "pending"
                s["created_at"] = datetime.now().isoformat()
                s["source"] = s.get("source", "scan")
                s["suggestion_type"] = s.get("suggestion_type", "new_rule")
                s["target_category"] = s.get("target_category", "preference")

            all_suggestions.extend(suggestions)

        except json.JSONDecodeError as e:
            config.log(f"Learning scan: JSON parse error in batch {i}: {e}")
            continue
        except Exception as e:
            config.log(f"Learning scan error in batch {i}: {e}")
            continue

    return _deduplicate(all_suggestions)


def _deduplicate(suggestions):
    """Remove duplicate suggestions based on proposed_rule similarity."""
    seen = set()
    unique = []
    for s in suggestions:
        key = s.get("proposed_rule", "").lower().strip()
        if key and key not in seen:
            seen.add(key)
            unique.append(s)
    return unique


def save_suggestions(suggestions, merge=True):
    """Write suggestions to rules.json under suggested_rules[].

    Args:
        suggestions: List of suggestion dicts.
        merge: If True, append to existing. If False, replace.
    """
    rules = config.load_rules()
    if rules is None:
        config.log("Cannot save suggestions — rules.json not loadable")
        return

    existing = rules.get("suggested_rules", []) if merge else []
    existing_rules = {s.get("proposed_rule", "").lower().strip() for s in existing}

    new_count = 0
    for s in suggestions:
        key = s.get("proposed_rule", "").lower().strip()
        if key and key not in existing_rules:
            existing.append(s)
            existing_rules.add(key)
            new_count += 1

    rules["suggested_rules"] = existing
    config.save_rules(rules)
    config.log(f"Learning scan: saved {new_count} new suggestions ({len(existing)} total)")
    return new_count


def approve_suggestion(suggestion_id, target_category=None):
    """Approve a suggestion — route it to the appropriate rule category.

    Args:
        suggestion_id: The suggestion ID to approve.
        target_category: Override target. One of:
            'always_block', 'needs_llm_review', 'always_allow', 'preference'
            If None, uses the suggestion's target_category (defaults to 'preference').

    Returns:
        True if approved successfully.
    """
    rules = config.load_rules()
    if rules is None:
        return False

    suggested = rules.get("suggested_rules", [])
    target = None
    for s in suggested:
        if s.get("id") == suggestion_id:
            target = s
            break

    if not target:
        return False

    target["status"] = "approved"
    target["approved_at"] = datetime.now().isoformat()

    # Determine target category
    category = target_category or target.get("target_category", "preference")
    sug_type = target.get("suggestion_type", "new_rule")

    if sug_type == "remove":
        # Remove matching rule from specified category
        _remove_matching_rule(rules, category, target.get("pattern", ""), target.get("old_pattern", ""))
        config.save_rules(rules)
        config.log(f"Approved removal {suggestion_id}: removed from {category}")
        return True

    if sug_type in ("narrow", "broaden"):
        # Replace old pattern with new one
        old_pat = target.get("old_pattern", "")
        new_pat = target.get("pattern", target.get("new_pattern", ""))
        _replace_pattern(rules, category, old_pat, new_pat)
        config.save_rules(rules)
        config.log(f"Approved {sug_type} {suggestion_id}: {old_pat} -> {new_pat} in {category}")
        return True

    # new_rule: add to target category
    if category == "always_block":
        pattern = target.get("pattern", "")
        if not pattern:
            return False
        reason = target.get("reason", target.get("proposed_rule", "Approved suggestion"))
        entry = {"pattern": pattern, "reason": reason, "source": target.get("source", "suggestion")}
        rules.setdefault("always_block", []).append(entry)

    elif category == "needs_llm_review":
        pattern = target.get("pattern", "")
        if not pattern:
            return False
        context = target.get("context", target.get("proposed_rule", "Approved suggestion"))
        entry = {"pattern": pattern, "context": context, "source": target.get("source", "suggestion")}
        rules.setdefault("needs_llm_review", []).append(entry)

    elif category == "always_allow":
        pattern = target.get("pattern", "")
        if not pattern:
            return False
        entry = {"pattern": pattern, "source": target.get("source", "suggestion")}
        rules.setdefault("always_allow", []).append(entry)

    else:
        # Default: preference (backward compatible)
        pref = {
            "rule": target.get("proposed_rule", ""),
            "source": target.get("source", "learned"),
            "confidence": target.get("confidence", "medium"),
            "category": target.get("category", "preference"),
            "approved_at": target["approved_at"],
            "evidence_count": len(target.get("evidence", []))
        }
        rules.setdefault("preferences", []).append(pref)

    config.save_rules(rules)
    config.log(f"Approved suggestion {suggestion_id} -> {category}")
    return True


def _remove_matching_rule(rules, category, pattern, old_pattern):
    """Remove a rule matching the pattern from the given category."""
    pat = pattern or old_pattern
    if not pat or category not in rules:
        return
    if category == "preferences":
        rules[category] = [r for r in rules.get(category, []) if r.get("rule", "") != pat]
    else:
        rules[category] = [r for r in rules.get(category, []) if r.get("pattern", "") != pat]


def _replace_pattern(rules, category, old_pattern, new_pattern):
    """Replace an old pattern with a new one in the given category."""
    if not old_pattern or not new_pattern or category not in rules:
        return
    for rule in rules.get(category, []):
        if rule.get("pattern", "") == old_pattern:
            rule["pattern"] = new_pattern
            return


def dismiss_suggestion(suggestion_id):
    """Dismiss a suggestion — mark it dismissed so it won't reappear."""
    rules = config.load_rules()
    if rules is None:
        return False

    suggested = rules.get("suggested_rules", [])
    for s in suggested:
        if s.get("id") == suggestion_id:
            s["status"] = "dismissed"
            s["dismissed_at"] = datetime.now().isoformat()
            config.save_rules(rules)
            config.log(f"Dismissed suggestion {suggestion_id}")
            return True
    return False


def create_promotion_suggestion(pattern, command_examples, deny_count, suggested_category="always_block"):
    """Create a suggestion from L2 pattern promotion (repeated denials).

    Args:
        pattern: Regex pattern for the rule.
        command_examples: List of example commands that were denied.
        deny_count: Number of denials detected.
        suggested_category: Target category for the rule.

    Returns:
        The created suggestion dict, or None if dedup catches it.
    """
    rules = config.load_rules()
    if rules is None:
        return None

    suggestion = {
        "id": f"sug_{uuid.uuid4().hex[:8]}",
        "status": "pending",
        "created_at": datetime.now().isoformat(),
        "source": "l2_promotion",
        "suggestion_type": "new_rule",
        "target_category": suggested_category,
        "pattern": pattern,
        "proposed_rule": f"Auto-block pattern: {pattern} (denied {deny_count}x in 7 days)",
        "reason": f"L2 denied this pattern {deny_count} times — promote to L1",
        "interpretation": f"Commands matching /{pattern}/ are consistently denied by L2",
        "confidence": "high",
        "evidence": [{"timestamp": datetime.now().isoformat(), "content": cmd} for cmd in command_examples[:5]],
        "category": "restriction",
    }

    # Dedup check
    existing = rules.get("suggested_rules", [])
    for s in existing:
        if s.get("pattern") == pattern and s.get("source") == "l2_promotion" and s.get("status") == "pending":
            config.log(f"Promotion dedup: pattern '{pattern}' already suggested")
            return None

    existing.append(suggestion)
    rules["suggested_rules"] = existing
    config.save_rules(rules)
    config.log(f"Promotion suggestion created: {pattern} ({deny_count} denials)")
    return suggestion


def suggest_refinements(rules):
    """Analyse CRE.md override/pattern history and suggest L1 refinements.

    Called from run_scan() when --refine flag is set.

    Returns:
        List of refinement suggestion dicts.
    """
    if not config.LLM_API_KEY:
        return []

    # Load CRE.md sections
    from .gate import _load_cre_context
    cre_content = _load_cre_context()
    if not cre_content:
        config.log("Refinement: no CRE.md content to analyse")
        return []

    # Extract sections
    override_log = _extract_section(cre_content, "## Override Log")
    active_patterns = _extract_section(cre_content, "## Active Patterns")

    if not override_log and not active_patterns:
        config.log("Refinement: no enforcement history to analyse")
        return []

    # Format existing rules for the prompt
    existing_text = ""
    for cat in ["always_block", "needs_llm_review", "always_allow"]:
        items = rules.get(cat, [])
        if items:
            existing_text += f"\n{cat}:\n"
            for r in items:
                existing_text += f"  /{r.get('pattern', '')}/ — {r.get('reason', r.get('context', ''))}\n"

    prompt = REFINEMENT_PROMPT.format(
        existing_rules=existing_text or "No rules configured.",
        override_log=override_log or "No overrides recorded.",
        active_patterns=active_patterns or "No patterns recorded.",
    )

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
                    {"role": "user", "content": "Suggest refinements based on the enforcement history."}
                ],
                "temperature": 0.1,
                "max_tokens": 4096,
                "enable_thinking": False,
            },
            timeout=config.LLM_TIMEOUT
        )
        resp.raise_for_status()
        content = resp.json()['choices'][0]['message']['content'].strip()
        content = content.replace("```json", "").replace("```", "").strip()

        if not content or content == "[]":
            return []

        refinements = json.loads(content)
        if not isinstance(refinements, list):
            return []

        # Convert to suggestion format
        suggestions = []
        for r in refinements:
            suggestion = {
                "id": f"sug_{uuid.uuid4().hex[:8]}",
                "status": "pending",
                "created_at": datetime.now().isoformat(),
                "source": "l2_refinement",
                "suggestion_type": r.get("suggestion_type", "narrow"),
                "target_category": r.get("target_category", "needs_llm_review"),
                "pattern": r.get("new_pattern", ""),
                "old_pattern": r.get("old_pattern", ""),
                "proposed_rule": f"{r.get('suggestion_type', 'refine').title()}: {r.get('old_pattern', '?')} — {r.get('reason', '')}",
                "reason": r.get("reason", ""),
                "interpretation": r.get("reason", ""),
                "confidence": "medium",
                "evidence": [],
                "category": "refinement",
            }
            suggestions.append(suggestion)

        return suggestions

    except Exception as e:
        config.log(f"Refinement analysis error: {e}")
        return []


def detect_overlapping_rules(rules):
    """Find overlapping/duplicate patterns within each rule category.

    Only compares patterns that share core command terms (ssh, git, deploy etc).
    Uses targeted probe commands derived from each pattern's own vocabulary.
    Flags imported rules as redundant when an existing rule already covers them.

    Returns:
        List of suggestion dicts (type: "remove") for redundant rules.
    """
    import re as _re
    suggestions = []

    for category in ("always_block", "needs_llm_review", "always_allow"):
        items = rules.get(category, [])
        if len(items) < 2:
            continue

        # Build compiled rule tuples
        compiled = []
        for i, rule in enumerate(items):
            pat = rule.get("pattern", "")
            if not pat:
                continue
            try:
                rx = _re.compile(pat)
            except _re.error:
                continue
            # Extract core command terms (2+ letter words, lowercased)
            terms = set(w.lower() for w in _re.findall(r'[a-zA-Z]{2,}', pat))
            compiled.append((i, pat, rx, rule.get("source", "manual"), rule, terms))

        seen_redundant = set()
        for ai, (idx_a, pat_a, rx_a, src_a, rule_a, terms_a) in enumerate(compiled):
            for bi, (idx_b, pat_b, rx_b, src_b, rule_b, terms_b) in enumerate(compiled):
                if ai >= bi or idx_b in seen_redundant or idx_a in seen_redundant:
                    continue

                # 1. Exact duplicate pattern
                if pat_a == pat_b:
                    redundant = (idx_b, rule_b) if src_b == "import" else (idx_a, rule_a)
                    keeper = rule_a if src_b == "import" else rule_b
                    seen_redundant.add(redundant[0])
                    suggestions.append(_make_overlap_suggestion(
                        category, redundant[1],
                        f"Exact duplicate of /{keeper.get('pattern', '')}/",
                    ))
                    continue

                # 2. Must share at least one core command term to be related
                shared_terms = terms_a & terms_b
                if not shared_terms:
                    continue

                # 3. Generate targeted probes from SHARED terms only
                probes = set()
                for t in shared_terms:
                    probes.add(t)
                    probes.add(f"{t} ")  # with trailing space
                    probes.add(f"{t} root@prod")
                    probes.add(f"{t} -f /tmp/test")
                    probes.add(f"sudo {t} something")
                # Also add realistic commands for well-known terms
                _cmd_probes = {
                    "ssh": ["ssh root@prod", "ssh admin@server", "ssh -p 22 user@host"],
                    "scp": ["scp file.txt user@host:/tmp/", "scp -r dir/ user@host:"],
                    "rsync": ["rsync -avz src/ user@host:dst/"],
                    "sftp": ["sftp user@host"],
                    "git": ["git push origin main", "git push --force", "git push"],
                    "push": ["git push origin main", "git push"],
                    "deploy": ["deploy prod", "deploy staging"],
                    "production": ["ssh production-server", "deploy production"],
                    "prod": ["ssh prod-server", "deploy prod", "production database"],
                    "rm": ["rm -rf /tmp/build", "rm -rf /"],
                    "kill": ["kill -9 1234", "killall node"],
                    "systemctl": ["systemctl restart nginx", "systemctl stop docker"],
                    "chmod": ["chmod -R 777 /var/www"],
                    "crontab": ["crontab -e", "crontab -l"],
                    "node": ["node server.js", "node -e 'code'"],
                    "python": ["python3 script.py", "python3 -c 'code'"],
                }
                for t in shared_terms:
                    if t in _cmd_probes:
                        probes.update(_cmd_probes[t])

                if len(probes) < 3:
                    continue

                # 4. Test both patterns against targeted probes
                a_matches = {p for p in probes if rx_a.search(p)}
                b_matches = {p for p in probes if rx_b.search(p)}

                if not b_matches or not a_matches:
                    continue

                # 5. B is redundant if A catches ALL of B's matches
                if b_matches <= a_matches:
                    if src_b == "import":
                        seen_redundant.add(idx_b)
                        suggestions.append(_make_overlap_suggestion(
                            category, rule_b,
                            f"Redundant — already covered by /{pat_a}/ (shared: {', '.join(sorted(shared_terms))})",
                        ))
                    continue

                # 6. A is redundant if B catches ALL of A's matches
                if a_matches <= b_matches:
                    if src_a == "import":
                        seen_redundant.add(idx_a)
                        suggestions.append(_make_overlap_suggestion(
                            category, rule_a,
                            f"Redundant — already covered by /{pat_b}/ (shared: {', '.join(sorted(shared_terms))})",
                        ))

    return suggestions


def _make_overlap_suggestion(category, rule, reason):
    """Create a 'remove' suggestion for a redundant rule."""
    import uuid
    from datetime import datetime

    return {
        "id": f"sug_{uuid.uuid4().hex[:12]}",
        "status": "pending",
        "created_at": datetime.now().isoformat(),
        "source": "l2_refinement",
        "suggestion_type": "remove",
        "target_category": category,
        "pattern": rule.get("pattern", ""),
        "old_pattern": rule.get("pattern", ""),
        "proposed_rule": f"Remove redundant: /{rule.get('pattern', '')}/",
        "reason": reason,
        "interpretation": f"Pattern overlap detected in {category}",
        "confidence": "high",
        "category": "refinement",
        "evidence": [{
            "timestamp": datetime.now().isoformat(),
            "content": f"Original reason: {rule.get('reason', rule.get('context', 'N/A'))}",
        }],
    }


def _extract_section(content, header):
    """Extract content between a section header and the next ## header."""
    lines = content.split('\n')
    capturing = False
    section_lines = []
    for line in lines:
        if line.strip() == header:
            capturing = True
            continue
        if capturing:
            if line.strip().startswith('## ') and line.strip() != header:
                break
            section_lines.append(line)
    return '\n'.join(section_lines).strip()


def run_scan(hours=None, refine=False):
    """Full scan pipeline: read sessions → extract → save.

    Args:
        hours: Scan last N hours only. None = all.
        refine: Also run refinement analysis on CRE.md history.

    Returns:
        (num_messages, num_suggestions) tuple.
    """
    config.log(f"Learning scan started (hours={hours}, refine={refine})")

    messages = scan_sessions(hours=hours)
    if not messages:
        config.log("Learning scan: no messages found")
        total_suggestions = 0

        # Still run refinements even with no new messages
        if refine:
            rules = config.load_rules()
            if rules:
                refinements = suggest_refinements(rules)
                overlaps = detect_overlapping_rules(rules)
                all_refine = refinements + overlaps
                if all_refine:
                    total_suggestions = save_suggestions(all_refine) or 0
                    config.log(f"Refinement scan: {total_suggestions} suggestions ({len(overlaps)} overlap removals)")

        return 0, total_suggestions

    config.log(f"Learning scan: {len(messages)} messages to process")
    suggestions = extract_preferences(messages)

    # Add refinement suggestions if requested
    if refine:
        rules = config.load_rules()
        if rules:
            refinements = suggest_refinements(rules)
            overlaps = detect_overlapping_rules(rules)
            suggestions.extend(refinements)
            suggestions.extend(overlaps)

    if not suggestions:
        config.log("Learning scan: no preferences detected")
        return len(messages), 0

    new_count = save_suggestions(suggestions)
    config.log(f"Learning scan complete: {len(messages)} messages → {new_count} new suggestions")
    return len(messages), new_count or 0
