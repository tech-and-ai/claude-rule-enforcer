"""Knowledge base engine — pattern→context injection for CRE PA mode.

Maps commands and tool inputs to contextual knowledge (server IPs, credentials,
workflows) so CRE helps Claude instead of just blocking/warning.

L1.5 in the gate pipeline: after L1 regex allows, before L2 LLM advisory.
"""

import json
import os
import re

from . import config

# Default KB path: next to rules.json in project root
KB_PATH = os.environ.get(
    "CRE_KB_PATH",
    os.path.join(config.PROJECT_ROOT, "knowledge_base.json")
)


def load_kb(path=None):
    """Load knowledge base from JSON file. Returns dict or empty structure."""
    p = path or KB_PATH
    try:
        with open(p) as f:
            return json.load(f)
    except FileNotFoundError:
        config.log(f"KB not found at {p}, using empty KB")
        return {"context_patterns": [], "version": "1.0", "last_synced": None}
    except Exception as e:
        config.log(f"KB load error: {e}")
        return {"context_patterns": [], "version": "1.0", "last_synced": None}


def save_kb(kb, path=None):
    """Write knowledge base back to JSON file."""
    p = path or KB_PATH
    with open(p, "w") as f:
        json.dump(kb, f, indent=2)
        f.write("\n")


def match_context(command, kb):
    """Match a command string against KB patterns. Returns context string or None.

    If multiple patterns match, their contexts are joined with newlines.
    """
    if not command or not kb:
        return None

    patterns = kb.get("context_patterns", [])
    matches = []

    for entry in patterns:
        pattern = entry.get("pattern", "")
        if not pattern:
            continue
        try:
            if re.search(pattern, command, re.IGNORECASE):
                ctx = entry.get("context", "")
                if ctx:
                    matches.append(ctx)
        except re.error:
            # Fallback to plain substring match
            if pattern.lower() in command.lower():
                ctx = entry.get("context", "")
                if ctx:
                    matches.append(ctx)

    if not matches:
        return None
    return "\n".join(matches)


def match_context_for_tool(normalized, kb):
    """Match any tool type against KB patterns. Checks command, file_path, content.

    Args:
        normalized: CRE normalized dict with tool_type, command, file_path, content, raw
        kb: knowledge base dict

    Returns context string or None.
    """
    if not kb:
        return None

    # Build a combined string to match against
    parts = []
    if normalized.get("command"):
        parts.append(normalized["command"])
    if normalized.get("file_path"):
        parts.append(normalized["file_path"])
    if normalized.get("content"):
        # Only check first 500 chars of content for performance
        parts.append(normalized["content"][:500])

    # Also check raw tool_input for things like URLs, queries, etc.
    tool_input = normalized.get("tool_input", {})
    for key in ("query", "url", "pattern", "prompt"):
        val = tool_input.get(key, "")
        if val:
            parts.append(str(val)[:300])

    if not parts:
        return None

    combined = " ".join(parts)
    return match_context(combined, kb)


def sync_from_servers_md(servers_md_path=None, kb_path=None):
    """Parse ~/.claude/servers.md and update knowledge_base.json server patterns.

    Reads the Quick Reference Table and creates context_patterns with
    category: "server" and source: "servers_md".

    Returns number of patterns added/updated.
    """
    from datetime import datetime

    md_path = servers_md_path or os.path.expanduser("~/.claude/servers.md")
    if not os.path.exists(md_path):
        config.log(f"servers.md not found at {md_path}")
        return 0

    with open(md_path) as f:
        content = f.read()

    kb = load_kb(kb_path)
    existing = kb.get("context_patterns", [])

    # Remove old synced entries (source: servers_md)
    existing = [e for e in existing if e.get("source") != "servers_md"]

    # Parse the Quick Reference Table
    # Format: | IP | Name | SSH Port | User | Password | Purpose |
    new_patterns = []
    in_table = False
    header_seen = 0

    for line in content.split("\n"):
        stripped = line.strip()
        if not stripped.startswith("|"):
            in_table = False
            header_seen = 0
            continue

        cells = [c.strip() for c in stripped.split("|")[1:-1]]
        if len(cells) < 4:
            continue

        # Skip header rows
        if "IP" in cells[0] and "Name" in cells[1]:
            in_table = True
            header_seen = 0
            continue
        if all(c.replace("-", "").strip() == "" for c in cells):
            header_seen += 1
            continue

        if not in_table:
            continue

        ip = cells[0].strip()
        name = cells[1].strip() if len(cells) > 1 else ""
        port = cells[2].strip() if len(cells) > 2 else ""
        user = cells[3].strip() if len(cells) > 3 else ""
        password = cells[4].strip() if len(cells) > 4 else ""
        purpose = cells[5].strip() if len(cells) > 5 else ""

        # Skip if no IP
        if not ip or not re.match(r"\d+\.\d+\.\d+\.\d+", ip):
            continue

        # Build pattern: match SSH to this IP
        escaped_ip = re.escape(ip)
        pattern = f"ssh.*{escaped_ip}|{escaped_ip}"

        # Build context string
        ctx_parts = [f"{name} ({ip})"]
        if port and port != "-":
            ctx_parts.append(f"SSH port: {port}")
        if user and user != "-":
            ctx_parts.append(f"User: {user}")
        if password and password != "-":
            ctx_parts.append(f"Password: {password}")
        if purpose and purpose != "-":
            ctx_parts.append(f"Purpose: {purpose}")

        context = ". ".join(ctx_parts)

        new_patterns.append({
            "pattern": pattern,
            "context": context,
            "category": "server",
            "source": "servers_md",
        })

    # Also extract email correction patterns from the file
    # Look for "NEVER use <email>" patterns in the content
    email_nevers = re.findall(r'NEVER\s+use\s+(\S+@\S+)', content, re.IGNORECASE)
    email_correct = re.findall(r'(?:ONLY|ALWAYS)\s+use\s+(\S+@\S+)', content, re.IGNORECASE)
    if email_nevers and email_correct:
        wrong = [e.rstrip('.,;)') for e in email_nevers]
        right = [e.rstrip('.,;)') for e in email_correct]
        new_patterns.append({
            "pattern": "|".join(re.escape(e) for e in wrong),
            "context": f"WRONG EMAIL. Correct: {', '.join(right)}. NEVER use: {', '.join(wrong)}",
            "category": "email",
            "source": "servers_md",
        })

    existing.extend(new_patterns)
    kb["context_patterns"] = existing
    kb["last_synced"] = datetime.now().isoformat()
    save_kb(kb, kb_path)

    config.log(f"KB sync: {len(new_patterns)} patterns from servers.md")
    return len(new_patterns)


def sync_from_claude_md(claude_md_path=None, kb_path=None):
    """Parse CLAUDE.md and extract context patterns for emails, workflows, tools.

    Extracts:
    - Email addresses with NEVER/ALWAYS context → email category
    - NEVER/ALWAYS rules → workflow category
    - Tool-specific rules → tool category

    Returns number of patterns added/updated.
    """
    from datetime import datetime

    md_path = claude_md_path or os.path.expanduser("~/.claude/CLAUDE.md")
    if not os.path.exists(md_path):
        config.log(f"CLAUDE.md not found at {md_path}")
        return 0

    with open(md_path) as f:
        content = f.read()

    kb = load_kb(kb_path)
    existing = kb.get("context_patterns", [])

    # Remove old synced entries
    existing = [e for e in existing if e.get("source") != "sync_claude_md"]

    new_patterns = []

    # Extract NEVER use email patterns
    # Look for lines like "NEVER use email@example.com" or "NEVER: email@example.com"
    never_emails = re.findall(
        r'(?:NEVER|never|Never)\s+(?:use\s+)?(\S+@\S+\.\S+)',
        content
    )
    correct_emails = re.findall(
        r'(?:ALWAYS|ONLY|always|only)\s+(?:use\s+)?(\S+@\S+\.\S+)',
        content
    )
    if never_emails and correct_emails:
        wrong = [e.rstrip('.,;)') for e in never_emails]
        right = [e.rstrip('.,;)') for e in correct_emails]
        pattern_parts = [re.escape(e) for e in wrong]
        new_patterns.append({
            "pattern": "|".join(pattern_parts),
            "context": f"WRONG EMAIL. Correct email: {', '.join(right)}. NEVER use: {', '.join(wrong)}",
            "category": "email",
            "source": "sync_claude_md",
        })

    # Extract NEVER/ALWAYS workflow rules (line-level)
    for line in content.split("\n"):
        stripped = line.strip().lstrip("-* ")
        # Match "NEVER do X" or "ALWAYS do X" rules
        m = re.match(r'(NEVER|ALWAYS)\s+(.{10,80})', stripped)
        if m:
            keyword = m.group(1)
            rule_text = m.group(2).rstrip('.,;')
            # Extract key terms for pattern matching
            words = re.findall(r'\b[a-z_]{4,}\b', rule_text.lower())
            if words:
                pattern = "|".join(words[:3])
                new_patterns.append({
                    "pattern": pattern,
                    "context": f"{keyword}: {rule_text}",
                    "category": "workflow",
                    "source": "sync_claude_md",
                })

    existing.extend(new_patterns)
    kb["context_patterns"] = existing
    kb["last_synced"] = datetime.now().isoformat()
    save_kb(kb, kb_path)

    config.log(f"KB sync: {len(new_patterns)} patterns from CLAUDE.md")
    return len(new_patterns)


def sync_from_memory_md(memory_md_path=None, kb_path=None):
    """Parse MEMORY.md and extract context patterns for project rules.

    Extracts:
    - Email rules → email category
    - WhatsApp/tool rules → tool category
    - NEVER/ALWAYS rules → workflow category
    - Server IPs mentioned → server category

    Returns number of patterns added/updated.
    """
    from datetime import datetime

    md_path = memory_md_path or os.path.expanduser(
        "~/.claude/projects/-home-admin-projects/memory/MEMORY.md"
    )
    if not os.path.exists(md_path):
        config.log(f"MEMORY.md not found at {md_path}")
        return 0

    with open(md_path) as f:
        content = f.read()

    kb = load_kb(kb_path)
    existing = kb.get("context_patterns", [])

    # Remove old synced entries
    existing = [e for e in existing if e.get("source") != "sync_memory_md"]

    new_patterns = []

    # Extract section-level rules (## headers followed by bullet points)
    sections = re.split(r'^## ', content, flags=re.MULTILINE)
    for section in sections:
        lines = section.strip().split("\n")
        if not lines:
            continue
        header = lines[0].strip()

        # Extract NEVER/ALWAYS rules from bullet points
        for line in lines[1:]:
            stripped = line.strip().lstrip("-* ")
            m = re.match(r'\*\*(NEVER|ALWAYS)\b[^*]*\*\*\s*[:\-—]?\s*(.{5,})', stripped)
            if not m:
                m = re.match(r'(NEVER|ALWAYS)\s+(.{10,80})', stripped)
            if m:
                keyword = m.group(1)
                rule_text = m.group(2).rstrip('.,;*')
                words = re.findall(r'\b[a-z_]{4,}\b', rule_text.lower())
                if words:
                    pattern = "|".join(words[:3])
                    new_patterns.append({
                        "pattern": pattern,
                        "context": f"[{header[:30]}] {keyword}: {rule_text[:100]}",
                        "category": "workflow",
                        "source": "sync_memory_md",
                    })

    existing.extend(new_patterns)
    kb["context_patterns"] = existing
    kb["last_synced"] = datetime.now().isoformat()
    save_kb(kb, kb_path)

    config.log(f"KB sync: {len(new_patterns)} patterns from MEMORY.md")
    return len(new_patterns)


def sync_from_preferences(rules_path=None, kb_path=None):
    """Sync manual preferences from rules.json into KB context patterns.

    Preferences are user-defined rules like "when asked X, do Y" that should
    surface as context on relevant tool calls. Extracts key terms from each
    preference rule and creates patterns so L1.5 KB injection can match them.

    Returns number of patterns added/updated.
    """
    from datetime import datetime

    r_path = rules_path or config.RULES_PATH
    if not os.path.exists(r_path):
        config.log(f"rules.json not found at {r_path}")
        return 0

    with open(r_path) as f:
        rules = json.load(f)

    prefs = rules.get("preferences", [])
    if not prefs:
        return 0

    kb = load_kb(kb_path)
    existing = kb.get("context_patterns", [])

    # Remove old synced entries
    existing = [e for e in existing if e.get("source") != "sync_preferences"]

    new_patterns = []

    for p in prefs:
        rule_text = p.get("rule", "")
        if not rule_text or len(rule_text) < 10:
            continue

        # Extract meaningful words for pattern matching (4+ chars, lowercase)
        words = re.findall(r'\b[a-z_]{4,}\b', rule_text.lower())
        # Remove common stop words
        stops = {"when", "always", "never", "must", "should", "this", "that",
                 "with", "from", "have", "been", "will", "about", "into",
                 "first", "before", "after", "without", "asked", "told",
                 "check", "make", "sure", "also", "only", "just"}
        words = [w for w in words if w not in stops]

        if not words:
            continue

        # Use up to 4 key terms for matching
        pattern = "|".join(words[:4])

        new_patterns.append({
            "pattern": pattern,
            "context": f"[Preference] {rule_text}",
            "category": "preference",
            "source": "sync_preferences",
        })

    existing.extend(new_patterns)
    kb["context_patterns"] = existing
    kb["last_synced"] = datetime.now().isoformat()
    save_kb(kb, kb_path)

    config.log(f"KB sync: {len(new_patterns)} patterns from preferences")
    return len(new_patterns)


def sync_from_skills(skills_dir=None, kb_path=None):
    """Scan ~/.claude/skills/*/SKILL.md and create KB patterns for each skill.

    Extracts skill name, description, triggers from YAML frontmatter.
    When Claude uses a command matching a skill's triggers, CRE injects
    the skill's key rules so Claude doesn't need to remember to read SKILL.md.

    Returns number of patterns added/updated.
    """
    from datetime import datetime
    import glob as glob_mod

    skills_path = skills_dir or os.path.expanduser("~/.claude/skills")
    if not os.path.isdir(skills_path):
        config.log(f"Skills dir not found at {skills_path}")
        return 0

    kb = load_kb(kb_path)
    existing = kb.get("context_patterns", [])

    # Remove old synced entries
    existing = [e for e in existing if e.get("source") != "sync_skills"]

    new_patterns = []
    skill_files = glob_mod.glob(os.path.join(skills_path, "*/SKILL.md"))

    for skill_file in skill_files:
        try:
            with open(skill_file) as f:
                raw = f.read()
        except Exception:
            continue

        # Parse YAML frontmatter manually (no yaml dependency)
        fm_match = re.match(r'^---\s*\n(.*?)\n---', raw, re.DOTALL)
        if not fm_match:
            continue

        fm = {}
        triggers = []
        in_triggers = False
        for line in fm_match.group(1).split("\n"):
            stripped = line.strip()
            if stripped.startswith("- ") and in_triggers:
                triggers.append(stripped[2:].strip())
                continue
            in_triggers = False
            if ":" in stripped:
                key, val = stripped.split(":", 1)
                key = key.strip()
                val = val.strip()
                if key == "triggers" and not val:
                    in_triggers = True
                else:
                    fm[key] = val

        name = fm.get("name", "")
        description = fm.get("description", "")
        if not triggers and fm.get("triggers"):
            triggers = [fm["triggers"]]

        if not name:
            continue

        # Build pattern from name + triggers
        pattern_parts = [re.escape(name)]
        if triggers:
            for t in triggers:
                if isinstance(t, str) and t.strip():
                    pattern_parts.append(re.escape(t.strip()))

        # Also match the skill's run.sh path
        skill_dir_name = os.path.basename(os.path.dirname(skill_file))
        pattern_parts.append(re.escape(skill_dir_name))

        pattern = "|".join(pattern_parts)

        # Extract key rules from body (first 5 bullet points with NEVER/ALWAYS/MUST)
        body = raw[fm_match.end():]
        key_rules = []
        for line in body.split("\n"):
            stripped = line.strip().lstrip("-* ")
            if re.search(r'\b(NEVER|ALWAYS|MUST|BAN|WARNING)\b', stripped):
                key_rules.append(stripped[:120])
                if len(key_rules) >= 5:
                    break

        # Build context
        ctx_parts = [f"Skill: {name} — {description[:100]}"]
        ctx_parts.append(f"SKILL.md: {skill_file}")
        if key_rules:
            ctx_parts.append("Key rules: " + "; ".join(key_rules))

        new_patterns.append({
            "pattern": pattern,
            "context": "\n".join(ctx_parts),
            "category": "skill",
            "source": "sync_skills",
        })

    existing.extend(new_patterns)
    kb["context_patterns"] = existing
    kb["last_synced"] = datetime.now().isoformat()
    save_kb(kb, kb_path)

    config.log(f"KB sync: {len(new_patterns)} skill patterns from {skills_path}")
    return len(new_patterns)
