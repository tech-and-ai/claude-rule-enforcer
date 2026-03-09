"""
CRE Importer — extract enforceable rules from instruction files.

Parses CLAUDE.md, agent.md, rules.md (or any markdown/text instruction file)
and converts actionable rules into CRE rules.json entries.

v0.3.0: Default flow creates suggestions (not direct rules). Use --direct to bypass.

Usage:
    cre import CLAUDE.md                # Creates suggestions for review
    cre import CLAUDE.md --direct       # Writes directly to rules.json (old behaviour)
    cre import --dry-run CLAUDE.md      # Preview without writing
"""

import json
import os
import re
import time
import uuid
from datetime import datetime

import requests

from . import config

IMPORT_PROMPT = """You are analysing an AI instruction file (like CLAUDE.md, agent.md, or rules.md).
Your job: extract ENFORCEABLE rules that a policy gate can mechanically enforce.

The instruction file may use compressed notation. Common patterns:
- R{...} = Rules block. Pipe-separated items are individual rules.
- W{...} = Workflow rules. ">=2files->engineering-workflow" means "use engineering workflow when modifying 2+ files".
- PA{...} = Policy gate handles these. Items listed here REQUIRE REVIEW, not hard blocking.
- GR{...} = Memory-grounded rules.
- Q!=do_it = "a question is not permission to act"
- D!=act = "discussion is not permission to act"
- Arrows (->|→) mean "then do" or "triggers"

ENFORCEABLE means the rule can be checked BEFORE an action happens:
- "Never push to main" -> can block `git push` commands
- "Always ask before SSH" -> can flag SSH commands for review
- "Don't create files without asking" -> can intercept file writes
- "Never use email X" -> can check content for that email
- "Always use X for Y" -> can be injected as a preference into reviews
- "Discussion is not permission" -> can check intent before acting

NOT ENFORCEABLE (style/tone/guidance -- skip these):
- "Use British English"
- "Keep code simple"
- "Follow existing patterns"
- "Be concise"
- "Use descriptive variable names"

For each enforceable rule found, categorise it:

1. "always_block" -- hard block with regex pattern. INSTANT DENY, no review, no context.
   Use ONLY for: things that must NEVER happen under ANY circumstances
   Examples: fork bombs, rm -rf /, using a banned email address, dangerous destructive commands
   DO NOT USE for: things that need approval/review. "Requires approval" != "never allowed".
   Provide: pattern (regex), reason (why blocked)

2. "needs_llm_review" -- flag for context-aware review before allowing
   Use for: "ask before X", "approval required", "check with user", conditional rules,
   "policy gate handles X", anything where the action MIGHT be OK with permission
   This is the RIGHT category for SSH, git push, production access, file creation,
   deploy commands -- anything that needs human approval but is not universally banned.
   Provide: pattern (regex), context (what to check)

3. "preference" -- inject into L2 review prompt as soft guidance
   Use for: "always use X", "prefer Y over Z", workflow preferences, behavioural rules
   like "question != permission", "verify before marking done", "read skills before use"
   Provide: rule (human text), confidence ("high")

4. "always_allow" -- explicitly safe, skip all checks
   Use for: "X is always safe", "allow X without asking"
   Provide: pattern (regex)

CRITICAL DISTINCTION:
- "approval required" / "must be approved" / "needs permission" -> needs_llm_review (NOT always_block)
- "NEVER do X" / "banned" / "prohibited under all circumstances" -> always_block
- When in doubt between always_block and needs_llm_review, choose needs_llm_review.
  A false block is worse than a review prompt.

IMPORTANT:
- Only extract rules that are STANDING (permanent), not one-time instructions
- Include the source line number and text as evidence
- If a rule is ambiguous, categorise as "preference" (safest)
- Regex patterns should be practical -- match the command/action, not prose
- Do NOT generate overly broad patterns like /python3/ or /node/ -- be specific

Respond with EXACTLY one JSON array:
[
  {{
    "category": "always_block" | "needs_llm_review" | "preference" | "always_allow",
    "pattern": "regex pattern (for block/review/allow)",
    "reason": "why (for always_block)",
    "context": "what to check (for needs_llm_review)",
    "rule": "human-readable rule (for preference)",
    "confidence": "high" | "medium",
    "source_line": 42,
    "source_text": "the original line from the file",
    "enforceable": true
  }}
]

If no enforceable rules found, return: []"""


def read_instruction_file(path):
    """Read an instruction file and return its contents with line numbers."""
    if not os.path.exists(path):
        return None, f"File not found: {path}"

    try:
        with open(path, 'r') as f:
            content = f.read()
        return content, None
    except Exception as e:
        return None, f"Error reading {path}: {e}"


def extract_rules(content, filename="unknown"):
    """Send instruction file content to LLM for rule extraction.

    Returns: (rules_list, error_string)
    """
    if not config.LLM_API_KEY:
        return None, "CRE_LLM_API_KEY not set — cannot parse instruction file"

    retries = 3
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
                        {"role": "system", "content": IMPORT_PROMPT},
                        {"role": "user", "content": f"FILE: {filename}\n\n{content[:15000]}"}
                    ],
                    "temperature": 0.1,
                    "max_tokens": 4096,
                    "enable_thinking": False,
                },
                timeout=90
            )
            # Some providers return 429 for both rate limits AND content filters
            if resp.status_code == 429 and attempt < retries - 1:
                body_text = resp.text.lower()
                filter_keywords = ["content_filter", "sensitive", "safety", "moderation", "prohibited", "filtered"]
                if any(kw in body_text for kw in filter_keywords):
                    config.log(f"Import content filtered — not retrying")
                    return None, "Content filtered by model provider"
                wait = 10 * (attempt + 1)  # 10s, 20s (matches QP backoff)
                config.log(f"Import 429 rate limited, retry {attempt + 1}/{retries} in {wait}s")
                print(f"  Rate limited, retrying in {wait}s...")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            raw = resp.json()['choices'][0]['message']['content'].strip()

            # Extract JSON from response (handle markdown code blocks)
            raw = re.sub(r'^```(?:json)?\s*', '', raw)
            raw = re.sub(r'\s*```$', '', raw)

            rules = json.loads(raw)
            if not isinstance(rules, list):
                return None, "LLM returned non-array response"

            return rules, None

        except requests.Timeout:
            if attempt < retries - 1:
                wait = 10 * (attempt + 1)
                config.log(f"Import timeout, retry {attempt + 1}/{retries} in {wait}s")
                print(f"  Timeout, retrying in {wait}s...")
                time.sleep(wait)
                continue
            return None, "LLM request timed out after all retries"
        except json.JSONDecodeError as e:
            return None, f"Could not parse LLM response as JSON: {e}"
        except Exception as e:
            err_str = str(e)
            is_rate_limit = "429" in err_str and not any(
                kw in err_str.lower() for kw in ["content_filter", "sensitive", "safety", "filtered"]
            )
            if (is_rate_limit or "timeout" in err_str.lower()) and attempt < retries - 1:
                wait = 10 * (attempt + 1)
                reason = "Rate limited" if is_rate_limit else "Timeout"
                config.log(f"Import {reason}, retry {attempt + 1}/{retries} in {wait}s")
                print(f"  {reason}, retrying in {wait}s...")
                time.sleep(wait)
                continue
            return None, f"LLM error: {e}"

    return None, "Rate limited after 3 retries"


def cross_reference_with_existing(extracted, rules):
    """Check extracted rules against existing rules for duplicates/conflicts.

    Returns: (clean_list, conflicts_list)
        clean_list: extracted rules with no duplicates
        conflicts_list: extracted rules that overlap with existing
    """
    clean = []
    conflicts = []

    for rule in extracted:
        if not rule.get("enforceable", True):
            continue

        category = rule.get("category", "")
        pattern = rule.get("pattern", "")
        rule_text = rule.get("rule", "")

        is_dup = False

        if category in ("always_block", "needs_llm_review", "always_allow") and pattern:
            existing_patterns = [r.get("pattern", "") for r in rules.get(category, [])]
            if pattern in existing_patterns:
                is_dup = True
                rule["_conflict"] = f"Duplicate pattern in {category}"

        elif category == "preference" and rule_text:
            existing_rules = [p.get("rule", "").lower() for p in rules.get("preferences", [])]
            if rule_text.lower() in existing_rules:
                is_dup = True
                rule["_conflict"] = "Duplicate preference"

        if is_dup:
            conflicts.append(rule)
        else:
            clean.append(rule)

    return clean, conflicts


def save_as_suggestions(extracted, filename="unknown"):
    """Convert extracted rules to suggestion format and save to suggested_rules[].

    Args:
        extracted: List of rule dicts from extract_rules()
        filename: Source filename for attribution

    Returns: number of new suggestions saved
    """
    rules = config.load_rules()
    if rules is None:
        config.log("Cannot save import suggestions — rules.json not loadable")
        return 0

    suggestions = []
    for rule in extracted:
        if not rule.get("enforceable", True):
            continue

        category = rule.get("category", "preference")

        # Map import category to target_category
        target_category = category if category != "preference" else "preference"

        suggestion = {
            "id": f"sug_{uuid.uuid4().hex[:8]}",
            "status": "pending",
            "created_at": datetime.now().isoformat(),
            "source": "import",
            "suggestion_type": "new_rule",
            "target_category": target_category,
            "proposed_rule": rule.get("rule", "") or rule.get("reason", "") or rule.get("context", "") or f"Import from {filename}",
            "interpretation": f"Extracted from {filename}, line {rule.get('source_line', '?')}",
            "confidence": rule.get("confidence", "high"),
            "category": rule.get("category", "preference"),
            "evidence": [{"timestamp": datetime.now().isoformat(), "content": rule.get("source_text", "")}],
        }

        # Carry forward pattern/reason/context for approval routing
        if rule.get("pattern"):
            suggestion["pattern"] = rule["pattern"]
        if rule.get("reason"):
            suggestion["reason"] = rule["reason"]
        if rule.get("context"):
            suggestion["context"] = rule["context"]
        if rule.get("_conflict"):
            suggestion["conflict_note"] = rule["_conflict"]

        suggestions.append(suggestion)

    if not suggestions:
        return 0

    # Dedup against existing suggestions
    existing = rules.get("suggested_rules", [])
    existing_keys = set()
    for s in existing:
        key = (s.get("pattern", "") + s.get("proposed_rule", "")).lower().strip()
        existing_keys.add(key)

    new_count = 0
    for s in suggestions:
        key = (s.get("pattern", "") + s.get("proposed_rule", "")).lower().strip()
        if key and key not in existing_keys:
            existing.append(s)
            existing_keys.add(key)
            new_count += 1

    rules["suggested_rules"] = existing
    config.save_rules(rules)
    config.log(f"Import: saved {new_count} new suggestions from {filename}")
    return new_count


def apply_rules(extracted, rules, merge=True):
    """Apply extracted rules directly to rules.json (--direct mode).

    Args:
        extracted: List of rule dicts from extract_rules()
        rules: Current rules.json dict
        merge: If True, append to existing. If False, replace categories.

    Returns: (num_added, summary_dict)
    """
    added = 0
    summary = {
        "always_block": [],
        "needs_llm_review": [],
        "always_allow": [],
        "preference": [],
        "skipped": [],
    }

    for rule in extracted:
        if not rule.get("enforceable", True):
            summary["skipped"].append(rule)
            continue

        category = rule.get("category", "")

        if category == "always_block":
            pattern = rule.get("pattern", "")
            reason = rule.get("reason", rule.get("source_text", "Imported rule"))
            if not pattern:
                continue
            # Check for duplicate
            existing = [r.get("pattern") for r in rules.get("always_block", [])]
            if pattern not in existing:
                entry = {"pattern": pattern, "reason": reason, "source": "import"}
                rules.setdefault("always_block", []).append(entry)
                summary["always_block"].append(entry)
                added += 1

        elif category == "needs_llm_review":
            pattern = rule.get("pattern", "")
            context = rule.get("context", rule.get("source_text", "Imported review rule"))
            if not pattern:
                continue
            existing = [r.get("pattern") for r in rules.get("needs_llm_review", [])]
            if pattern not in existing:
                entry = {"pattern": pattern, "context": context, "source": "import"}
                rules.setdefault("needs_llm_review", []).append(entry)
                summary["needs_llm_review"].append(entry)
                added += 1

        elif category == "always_allow":
            pattern = rule.get("pattern", "")
            if not pattern:
                continue
            existing = [r.get("pattern") for r in rules.get("always_allow", [])]
            if pattern not in existing:
                entry = {"pattern": pattern, "source": "import"}
                rules.setdefault("always_allow", []).append(entry)
                summary["always_allow"].append(entry)
                added += 1

        elif category == "preference":
            rule_text = rule.get("rule", rule.get("source_text", ""))
            confidence = rule.get("confidence", "medium")
            if not rule_text:
                continue
            existing = [p.get("rule", "").lower() for p in rules.get("preferences", [])]
            if rule_text.lower() not in existing:
                entry = {"rule": rule_text, "confidence": confidence, "source": "import"}
                rules.setdefault("preferences", []).append(entry)
                summary["preference"].append(entry)
                added += 1

        else:
            summary["skipped"].append(rule)

    return added, summary


def format_preview(extracted):
    """Format extracted rules for terminal preview."""
    lines = []

    categories = {
        "always_block": [],
        "needs_llm_review": [],
        "always_allow": [],
        "preference": [],
    }

    for rule in extracted:
        cat = rule.get("category", "unknown")
        if cat in categories:
            categories[cat].append(rule)

    colors = {
        "always_block": "\033[31m",      # red
        "needs_llm_review": "\033[33m",  # yellow
        "always_allow": "\033[32m",      # green
        "preference": "\033[36m",        # cyan
    }
    reset = "\033[0m"

    for cat, rules in categories.items():
        if not rules:
            continue
        color = colors.get(cat, "")
        lines.append(f"\n{color}{cat}{reset} ({len(rules)}):")
        for r in rules:
            source = r.get("source_text", "")[:80]
            if cat == "always_block":
                lines.append(f"  /{r.get('pattern', '')}/ — {r.get('reason', '')}")
            elif cat == "needs_llm_review":
                lines.append(f"  /{r.get('pattern', '')}/ — {r.get('context', '')}")
            elif cat == "always_allow":
                lines.append(f"  /{r.get('pattern', '')}/")
            elif cat == "preference":
                lines.append(f"  {r.get('rule', '')}")
            if source:
                lines.append(f"    └─ line {r.get('source_line', '?')}: \"{source}\"")

    total = sum(len(v) for v in categories.values())
    lines.insert(0, f"Found {total} enforceable rules:")

    return "\n".join(lines)


def run_import(files, dry_run=False, direct=False):
    """Full import pipeline: read files → extract → preview → apply/suggest.

    Args:
        files: List of file paths to import
        dry_run: If True, preview only, don't write
        direct: If True, write directly to rules.json (old behaviour).
                If False (default), create suggestions for review.

    Returns: (total_added, total_extracted)
    """
    all_extracted = []

    for fpath in files:
        fname = os.path.basename(fpath)
        print(f"Parsing {fname}...")

        content, err = read_instruction_file(fpath)
        if err:
            print(f"  ERROR: {err}")
            continue

        extracted, err = extract_rules(content, fname)
        if err:
            print(f"  ERROR: {err}")
            continue

        print(f"  Found {len(extracted)} enforceable rules")
        all_extracted.extend(extracted)

    if not all_extracted:
        print("\nNo enforceable rules found.")
        return 0, 0

    # Preview
    print(format_preview(all_extracted))

    if dry_run:
        print(f"\n(dry run — nothing written)")
        return 0, len(all_extracted)

    if direct:
        # Old behaviour: write directly to rules.json
        rules = config.load_rules()
        if not rules:
            print("ERROR: Could not load rules.json")
            return 0, len(all_extracted)

        num_added, summary = apply_rules(all_extracted, rules)
        config.save_rules(rules)

        skipped = len(summary.get("skipped", []))
        print(f"\nApplied {num_added} rules directly to {config.RULES_PATH}")
        if skipped:
            print(f"Skipped {skipped} (duplicates or non-enforceable)")

        return num_added, len(all_extracted)
    else:
        # New default: create suggestions for review
        rules = config.load_rules()
        if not rules:
            print("ERROR: Could not load rules.json")
            return 0, len(all_extracted)

        # Cross-reference with existing rules
        clean, conflicts = cross_reference_with_existing(all_extracted, rules)

        if conflicts:
            print(f"\n{len(conflicts)} rules already exist (skipped):")
            for c in conflicts:
                print(f"  {c.get('pattern', c.get('rule', '?'))} — {c.get('_conflict', 'duplicate')}")

        if not clean:
            print("\nAll rules already exist. Nothing to suggest.")
            return 0, len(all_extracted)

        fname = os.path.basename(files[0]) if files else "unknown"
        num_saved = save_as_suggestions(clean, filename=fname)

        # Auto-detect overlaps with existing rules after import
        from .learner import detect_overlapping_rules
        rules = config.load_rules()  # Reload after suggestions saved
        if rules:
            overlaps = detect_overlapping_rules(rules)
            if overlaps:
                from .learner import save_suggestions
                num_overlap = save_suggestions(overlaps) or 0
                if num_overlap:
                    print(f"\nDetected {num_overlap} overlapping rules — review cleanup suggestions in dashboard")

        print(f"\nCreated {num_saved} suggestions — review in dashboard or 'cre rules list'")
        return num_saved, len(all_extracted)
