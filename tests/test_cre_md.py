"""Tests for CRE.md — L2's persistent memory."""

import json
import os
import sys
import pytest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from cre.gate import (
    _load_cre_context, _log_enforcement_event, update_cre_md,
    get_cre_md_stats, clear_cre_md, _insert_after_section,
    _trim_old_entries, _trim_to_size,
)


@pytest.fixture
def cre_md_path(tmp_path, monkeypatch):
    """Point CRE_MD_PATH to a temp file."""
    path = str(tmp_path / "CRE.md")
    monkeypatch.setattr("cre.config.CRE_MD_PATH", path)
    return path


class TestCreateAndLoad:
    """CRE.md creation and loading."""

    def test_load_empty_returns_empty_string(self, cre_md_path):
        assert _load_cre_context() == ""

    def test_update_creates_file(self, cre_md_path):
        update_cre_md("enforcement", "ssh root@prod", "deny", "No permission")
        assert os.path.exists(cre_md_path)
        content = _load_cre_context()
        assert "Active Patterns" in content
        assert "DENY" in content
        assert "ssh root@prod" in content

    def test_load_caps_at_8k(self, cre_md_path):
        # Write a large file
        with open(cre_md_path, 'w') as f:
            f.write("x" * 10000)
        content = _load_cre_context()
        assert len(content) <= 8000


class TestAppendAndTrim:
    """Appending entries and trimming old ones."""

    def test_append_enforcement_event(self, cre_md_path):
        update_cre_md("enforcement", "git push", "deny", "Not approved")
        update_cre_md("enforcement", "ls -la", "allow", "Safe command")
        content = _load_cre_context()
        assert "git push" in content
        assert "ls -la" in content

    def test_append_override_event(self, cre_md_path):
        update_cre_md("override", "ssh server", "allow", "User overrode")
        content = _load_cre_context()
        assert "Override Log" in content
        assert "ssh server" in content

    def test_trim_old_entries(self, cre_md_path):
        now = datetime.now()
        old_ts = (now - timedelta(days=20)).strftime("%Y-%m-%d %H:%M")
        new_ts = now.strftime("%Y-%m-%d %H:%M")

        content = f"""# CRE Memory

## Active Patterns
- [{new_ts}] DENY (bash): `ssh root` — No permission
- [{old_ts}] DENY (bash): `old command` — Old denial

## Override Log

## Emerging Patterns

## Rule Rationale
"""
        result = _trim_old_entries(content, now)
        assert "ssh root" in result
        assert "old command" not in result

    def test_trim_to_size(self):
        lines = ["# CRE Memory", "", "## Active Patterns"]
        for i in range(100):
            lines.append(f"- [2026-03-01 10:{i:02d}] DENY (bash): `cmd{i}` — reason")
        content = '\n'.join(lines)
        result = _trim_to_size(content, 500)
        assert len(result) <= 500


class TestStats:
    """CRE.md stats and clear."""

    def test_stats_no_file(self, cre_md_path):
        stats = get_cre_md_stats()
        assert stats["exists"] is False
        assert stats["entries"] == 0

    def test_stats_with_entries(self, cre_md_path):
        update_cre_md("enforcement", "cmd1", "deny", "reason1")
        update_cre_md("enforcement", "cmd2", "allow", "reason2")
        stats = get_cre_md_stats()
        assert stats["exists"] is True
        assert stats["entries"] == 2
        assert stats["size"] > 0

    def test_clear(self, cre_md_path):
        update_cre_md("enforcement", "cmd1", "deny", "reason1")
        assert get_cre_md_stats()["entries"] > 0
        ok = clear_cre_md()
        assert ok
        assert get_cre_md_stats()["entries"] == 0
        # File still exists with headers
        content = _load_cre_context()
        assert "Active Patterns" in content


class TestInsertAfterSection:
    """Helper: insert entries after section headers."""

    def test_inserts_after_header(self):
        content = "## Active Patterns\n\n## Override Log\n"
        result = _insert_after_section(content, "## Active Patterns", "- new entry\n")
        lines = result.split('\n')
        header_idx = next(i for i, l in enumerate(lines) if l.strip() == "## Active Patterns")
        assert "new entry" in lines[header_idx + 1]

    def test_creates_section_if_missing(self):
        content = "# CRE Memory\n"
        result = _insert_after_section(content, "## New Section", "- entry\n")
        assert "## New Section" in result
        assert "entry" in result
