"""Tests for knowledge base engine — pattern matching, context injection, sync."""

import json
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from cre.knowledge import (
    load_kb, save_kb, match_context, match_context_for_tool,
    sync_from_servers_md, sync_from_claude_md, sync_from_memory_md, sync_from_skills,
)


@pytest.fixture
def sample_kb():
    """Sample knowledge base for testing."""
    return {
        "context_patterns": [
            {
                "pattern": "ssh.*10\\.0\\.0\\.1|10\\.0\\.0\\.1",
                "context": "GPU server. SSH port 2222, user: admin.",
                "category": "server",
            },
            {
                "pattern": "ssh.*203\\.0\\.113\\.50|ssh.*prod",
                "context": "Production server. API-only email.",
                "category": "server",
            },
            {
                "pattern": "user@old\\.example\\.com|user@legacy\\.example\\.com",
                "context": "WRONG EMAIL. Use user@correct.example.com",
                "category": "email",
            },
            {
                "pattern": "git push",
                "context": "Repo is PRIVATE.",
                "category": "workflow",
            },
        ],
        "version": "1.0",
        "last_synced": None,
    }


class TestLoadKB:
    """Loading knowledge base."""

    def test_load_from_file(self, tmp_path, sample_kb):
        kb_file = tmp_path / "kb.json"
        kb_file.write_text(json.dumps(sample_kb))
        kb = load_kb(str(kb_file))
        assert len(kb["context_patterns"]) == 4
        assert kb["version"] == "1.0"

    def test_load_missing_file_returns_empty(self, tmp_path):
        kb = load_kb(str(tmp_path / "nonexistent.json"))
        assert kb["context_patterns"] == []
        assert kb["version"] == "1.0"

    def test_load_invalid_json_returns_empty(self, tmp_path):
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("not json{{{")
        kb = load_kb(str(bad_file))
        assert kb["context_patterns"] == []


class TestMatchContext:
    """Pattern matching against commands."""

    def test_match_ssh_server(self, sample_kb):
        result = match_context("ssh admin@10.0.0.1", sample_kb)
        assert result is not None
        assert "GPU" in result

    def test_match_prod_server(self, sample_kb):
        result = match_context("ssh root@203.0.113.50", sample_kb)
        assert result is not None
        assert "Production" in result

    def test_match_prod_keyword(self, sample_kb):
        result = match_context("ssh root@prod", sample_kb)
        assert result is not None
        assert "Production" in result

    def test_match_wrong_email(self, sample_kb):
        result = match_context("send to user@old.example.com", sample_kb)
        assert result is not None
        assert "WRONG EMAIL" in result
        assert "correct.example.com" in result

    def test_match_git_push(self, sample_kb):
        result = match_context("git push origin main", sample_kb)
        assert result is not None
        assert "PRIVATE" in result

    def test_case_insensitive(self, sample_kb):
        result = match_context("SSH admin@10.0.0.1", sample_kb)
        assert result is not None
        assert "GPU" in result

    def test_no_match_returns_none(self, sample_kb):
        result = match_context("ls -la", sample_kb)
        assert result is None

    def test_empty_command_returns_none(self, sample_kb):
        assert match_context("", sample_kb) is None

    def test_empty_kb_returns_none(self):
        assert match_context("ssh root@prod", {}) is None

    def test_multiple_matches_joined(self, sample_kb):
        """Command matching multiple patterns gets all contexts."""
        sample_kb["context_patterns"].append({
            "pattern": "10\\.0\\.0\\.1",
            "context": "Also has dedicated GPU.",
            "category": "server",
        })
        result = match_context("ssh admin@10.0.0.1", sample_kb)
        assert "GPU" in result
        assert "dedicated" in result


class TestMatchContextForTool:
    """Matching against normalized tool input."""

    def test_bash_command_matches(self, sample_kb):
        normalized = {
            "tool_type": "bash",
            "command": "ssh admin@10.0.0.1",
            "file_path": "",
            "content": "",
            "tool_input": {},
        }
        result = match_context_for_tool(normalized, sample_kb)
        assert result is not None
        assert "GPU" in result

    def test_write_file_path_matches(self, sample_kb):
        """Email patterns in content should match."""
        normalized = {
            "tool_type": "write",
            "command": "",
            "file_path": "/tmp/email.py",
            "content": "send to user@old.example.com",
            "tool_input": {},
        }
        result = match_context_for_tool(normalized, sample_kb)
        assert result is not None
        assert "WRONG EMAIL" in result

    def test_tool_input_query_matches(self, sample_kb):
        """WebSearch query containing server IPs should match."""
        normalized = {
            "tool_type": "websearch",
            "command": "",
            "file_path": "",
            "content": "",
            "tool_input": {"query": "how to connect to 10.0.0.1"},
        }
        result = match_context_for_tool(normalized, sample_kb)
        assert result is not None
        assert "GPU" in result

    def test_no_match_returns_none(self, sample_kb):
        normalized = {
            "tool_type": "bash",
            "command": "ls -la",
            "file_path": "",
            "content": "",
            "tool_input": {},
        }
        result = match_context_for_tool(normalized, sample_kb)
        assert result is None

    def test_empty_normalized_returns_none(self, sample_kb):
        normalized = {
            "tool_type": "bash",
            "command": "",
            "file_path": "",
            "content": "",
            "tool_input": {},
        }
        result = match_context_for_tool(normalized, sample_kb)
        assert result is None


class TestSyncFromServersMd:
    """Syncing knowledge base from servers.md."""

    def test_sync_creates_patterns(self, tmp_path):
        servers_md = tmp_path / "servers.md"
        servers_md.write_text("""# Servers

## Quick Reference Table

| IP | Name | SSH Port | User | Password | Purpose |
|----|------|----------|------|----------|---------|
| 10.0.0.1 | GPU Box | 2222 | admin | s3cret | GPU server |
| 10.0.0.2 | Dev Box | 2201 | admin | s3cret | Main dev |

admin@example.com
""")
        kb_file = tmp_path / "kb.json"
        kb_file.write_text(json.dumps({"context_patterns": [], "version": "1.0", "last_synced": None}))

        count = sync_from_servers_md(str(servers_md), str(kb_file))
        assert count >= 2  # At least the 2 server rows

        kb = load_kb(str(kb_file))
        patterns = kb["context_patterns"]
        assert len(patterns) >= 2

        # Check server entries
        server_patterns = [p for p in patterns if p.get("category") == "server"]
        assert len(server_patterns) >= 2
        assert all(p.get("source") == "servers_md" for p in server_patterns)

        # Check last_synced was set
        assert kb["last_synced"] is not None

    def test_sync_replaces_old_entries(self, tmp_path):
        """Re-sync should replace old servers_md entries, not duplicate."""
        servers_md = tmp_path / "servers.md"
        servers_md.write_text("""# Servers
## Quick Reference Table
| IP | Name | SSH Port | User | Password | Purpose |
|----|------|----------|------|----------|---------|
| 10.0.0.1 | TestBox | 22 | root | pass | Test |
""")
        kb_file = tmp_path / "kb.json"
        kb_file.write_text(json.dumps({
            "context_patterns": [
                {"pattern": "old_pattern", "context": "old", "category": "server", "source": "servers_md"},
                {"pattern": "manual", "context": "keep this", "category": "workflow"},
            ],
            "version": "1.0",
            "last_synced": None,
        }))

        sync_from_servers_md(str(servers_md), str(kb_file))
        kb = load_kb(str(kb_file))

        # Old servers_md entry should be gone
        sources = [p.get("source") for p in kb["context_patterns"]]
        assert "servers_md" in sources  # new entry
        # Manual entry preserved
        manual = [p for p in kb["context_patterns"] if p.get("context") == "keep this"]
        assert len(manual) == 1

    def test_sync_missing_file(self, tmp_path):
        count = sync_from_servers_md(str(tmp_path / "nope.md"), str(tmp_path / "kb.json"))
        assert count == 0


class TestSaveKB:
    """Saving knowledge base."""

    def test_save_and_reload(self, tmp_path, sample_kb):
        kb_file = tmp_path / "kb.json"
        save_kb(sample_kb, str(kb_file))

        loaded = load_kb(str(kb_file))
        assert len(loaded["context_patterns"]) == len(sample_kb["context_patterns"])
        assert loaded["version"] == "1.0"


class TestSyncFromClaudeMd:
    """Syncing from CLAUDE.md."""

    def test_extracts_email_rules(self, tmp_path):
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("""# Rules
- ONLY use admin@correct.example.com
- NEVER use admin@old.example.com or admin@legacy.example.com
""")
        kb_file = tmp_path / "kb.json"
        kb_file.write_text(json.dumps({"context_patterns": [], "version": "1.0", "last_synced": None}))

        count = sync_from_claude_md(str(claude_md), str(kb_file))
        assert count >= 1

        kb = load_kb(str(kb_file))
        email_patterns = [p for p in kb["context_patterns"] if p.get("category") == "email"]
        assert len(email_patterns) >= 1
        assert all(p.get("source") == "sync_claude_md" for p in email_patterns)

    def test_extracts_never_always_rules(self, tmp_path):
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("""# Rules
- NEVER write new code or scripts without asking User first
- ALWAYS check git history before making changes
""")
        kb_file = tmp_path / "kb.json"
        kb_file.write_text(json.dumps({"context_patterns": [], "version": "1.0", "last_synced": None}))

        count = sync_from_claude_md(str(claude_md), str(kb_file))
        assert count >= 1

        kb = load_kb(str(kb_file))
        workflow = [p for p in kb["context_patterns"] if p.get("category") == "workflow"]
        assert len(workflow) >= 1

    def test_replaces_old_entries(self, tmp_path):
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("NEVER delete production data without backup")
        kb_file = tmp_path / "kb.json"
        kb_file.write_text(json.dumps({
            "context_patterns": [
                {"pattern": "old", "context": "old rule", "source": "sync_claude_md"},
                {"pattern": "manual", "context": "keep", "category": "workflow"},
            ],
            "version": "1.0",
            "last_synced": None,
        }))

        sync_from_claude_md(str(claude_md), str(kb_file))
        kb = load_kb(str(kb_file))

        # Old sync_claude_md entry gone, manual preserved
        old = [p for p in kb["context_patterns"] if p.get("context") == "old rule"]
        assert len(old) == 0
        manual = [p for p in kb["context_patterns"] if p.get("context") == "keep"]
        assert len(manual) == 1

    def test_missing_file(self, tmp_path):
        count = sync_from_claude_md(str(tmp_path / "nope.md"), str(tmp_path / "kb.json"))
        assert count == 0


class TestSyncFromMemoryMd:
    """Syncing from MEMORY.md."""

    def test_extracts_rules(self, tmp_path):
        memory_md = tmp_path / "MEMORY.md"
        memory_md.write_text("""# Memory

## WhatsApp Rules
- NEVER use node wa.js send — it creates competing connections
- ALWAYS use the outbox /tmp/wa_outbox/ for sending messages
""")
        kb_file = tmp_path / "kb.json"
        kb_file.write_text(json.dumps({"context_patterns": [], "version": "1.0", "last_synced": None}))

        count = sync_from_memory_md(str(memory_md), str(kb_file))
        assert count >= 1

        kb = load_kb(str(kb_file))
        patterns = [p for p in kb["context_patterns"] if p.get("source") == "sync_memory_md"]
        assert len(patterns) >= 1

    def test_replaces_old_entries(self, tmp_path):
        memory_md = tmp_path / "MEMORY.md"
        memory_md.write_text("## Rules\n- NEVER delete auth files without checking logs first")
        kb_file = tmp_path / "kb.json"
        kb_file.write_text(json.dumps({
            "context_patterns": [
                {"pattern": "old", "context": "stale", "source": "sync_memory_md"},
            ],
            "version": "1.0",
            "last_synced": None,
        }))

        sync_from_memory_md(str(memory_md), str(kb_file))
        kb = load_kb(str(kb_file))

        stale = [p for p in kb["context_patterns"] if p.get("context") == "stale"]
        assert len(stale) == 0

    def test_missing_file(self, tmp_path):
        count = sync_from_memory_md(str(tmp_path / "nope.md"), str(tmp_path / "kb.json"))
        assert count == 0


class TestSyncFromSkills:
    """Syncing from skill SKILL.md files."""

    def test_extracts_skill_patterns(self, tmp_path):
        skills_dir = tmp_path / "skills"
        skill1 = skills_dir / "test-skill"
        skill1.mkdir(parents=True)
        (skill1 / "SKILL.md").write_text("""---
name: test-skill
description: A test skill for unit testing
triggers:
  - test skill
  - run test
---

# Test Skill

- NEVER run without parameters
- ALWAYS check output before proceeding
""")
        kb_file = tmp_path / "kb.json"
        kb_file.write_text(json.dumps({"context_patterns": [], "version": "1.0", "last_synced": None}))

        count = sync_from_skills(str(skills_dir), str(kb_file))
        assert count == 1

        kb = load_kb(str(kb_file))
        skill_patterns = [p for p in kb["context_patterns"] if p.get("category") == "skill"]
        assert len(skill_patterns) == 1
        assert skill_patterns[0]["source"] == "sync_skills"
        assert "test" in skill_patterns[0]["pattern"]
        assert "NEVER" in skill_patterns[0]["context"]

    def test_multiple_skills(self, tmp_path):
        skills_dir = tmp_path / "skills"
        for name in ["alpha", "beta", "gamma"]:
            d = skills_dir / name
            d.mkdir(parents=True)
            (d / "SKILL.md").write_text(f"""---
name: {name}
description: Skill {name}
---
# {name}
""")
        kb_file = tmp_path / "kb.json"
        kb_file.write_text(json.dumps({"context_patterns": [], "version": "1.0", "last_synced": None}))

        count = sync_from_skills(str(skills_dir), str(kb_file))
        assert count == 3

    def test_replaces_old_entries(self, tmp_path):
        skills_dir = tmp_path / "skills"
        skill = skills_dir / "only-skill"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("""---
name: only-skill
description: The only skill
---
""")
        kb_file = tmp_path / "kb.json"
        kb_file.write_text(json.dumps({
            "context_patterns": [
                {"pattern": "old-skill", "context": "removed", "source": "sync_skills"},
            ],
            "version": "1.0",
            "last_synced": None,
        }))

        sync_from_skills(str(skills_dir), str(kb_file))
        kb = load_kb(str(kb_file))

        old = [p for p in kb["context_patterns"] if p.get("context") == "removed"]
        assert len(old) == 0

    def test_missing_dir(self, tmp_path):
        count = sync_from_skills(str(tmp_path / "nope"), str(tmp_path / "kb.json"))
        assert count == 0
