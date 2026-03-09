"""Tests for the learning engine."""

import json
import os
import sys
import tempfile
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from cre.learner import _deduplicate, approve_suggestion, dismiss_suggestion, _extract_section


class TestDeduplicate:
    """Suggestion deduplication."""

    def test_removes_exact_duplicates(self):
        suggestions = [
            {"proposed_rule": "Always use max_turns=200", "confidence": "high"},
            {"proposed_rule": "Always use max_turns=200", "confidence": "medium"},
            {"proposed_rule": "Never push to main", "confidence": "high"},
        ]
        result = _deduplicate(suggestions)
        assert len(result) == 2
        assert result[0]["proposed_rule"] == "Always use max_turns=200"
        assert result[1]["proposed_rule"] == "Never push to main"

    def test_case_insensitive_dedup(self):
        suggestions = [
            {"proposed_rule": "Use user@example.com for email", "confidence": "high"},
            {"proposed_rule": "use user@example.com for email", "confidence": "medium"},
        ]
        result = _deduplicate(suggestions)
        assert len(result) == 1

    def test_empty_list(self):
        assert _deduplicate([]) == []

    def test_no_duplicates(self):
        suggestions = [
            {"proposed_rule": "Rule A", "confidence": "high"},
            {"proposed_rule": "Rule B", "confidence": "high"},
        ]
        result = _deduplicate(suggestions)
        assert len(result) == 2


class TestApproveAndDismiss:
    """Approve/dismiss suggestions via rules.json."""

    @pytest.fixture
    def rules_file(self, tmp_path):
        """Create a temp rules.json with pending suggestions."""
        rules = {
            "enabled": True,
            "always_block": [],
            "always_allow": [],
            "needs_llm_review": [],
            "learned_rules": [],
            "suggested_rules": [
                {
                    "id": "sug_test001",
                    "proposed_rule": "Always use max_turns=200",
                    "interpretation": "User frustrated by low max_turns",
                    "evidence": [{"timestamp": "2026-02-27T14:32", "content": "why max_turns=10?"}],
                    "confidence": "high",
                    "category": "preference",
                    "status": "pending",
                    "target_category": "preference",
                    "suggestion_type": "new_rule",
                    "source": "scan",
                },
                {
                    "id": "sug_test002",
                    "proposed_rule": "Never use user@wrongdomain.com",
                    "interpretation": "Wrong email used repeatedly",
                    "evidence": [],
                    "confidence": "high",
                    "category": "restriction",
                    "status": "pending",
                    "target_category": "always_block",
                    "suggestion_type": "new_rule",
                    "source": "scan",
                    "pattern": "wrongdomain\\.com",
                    "reason": "Wrong email domain",
                },
                {
                    "id": "sug_test003",
                    "proposed_rule": "Review SSH commands",
                    "pattern": "ssh|scp",
                    "context": "Remote access needs review",
                    "confidence": "high",
                    "status": "pending",
                    "target_category": "needs_llm_review",
                    "suggestion_type": "new_rule",
                    "source": "import",
                },
                {
                    "id": "sug_test004",
                    "proposed_rule": "Allow npm test",
                    "pattern": "^npm test",
                    "confidence": "high",
                    "status": "pending",
                    "target_category": "always_allow",
                    "suggestion_type": "new_rule",
                    "source": "import",
                },
                {
                    "id": "sug_test005",
                    "proposed_rule": "Remove: old ssh pattern too broad",
                    "pattern": "ssh",
                    "old_pattern": "ssh",
                    "confidence": "medium",
                    "status": "pending",
                    "target_category": "needs_llm_review",
                    "suggestion_type": "remove",
                    "source": "l2_refinement",
                },
                {
                    "id": "sug_test006",
                    "proposed_rule": "Narrow: ssh pattern",
                    "pattern": "ssh root@",
                    "old_pattern": "ssh",
                    "confidence": "medium",
                    "status": "pending",
                    "target_category": "needs_llm_review",
                    "suggestion_type": "narrow",
                    "source": "l2_refinement",
                },
            ],
            "preferences": [],
        }
        p = tmp_path / "rules.json"
        p.write_text(json.dumps(rules, indent=2))
        return str(p)

    def test_approve_to_preference(self, rules_file, monkeypatch):
        monkeypatch.setattr("cre.config.RULES_PATH", rules_file)
        ok = approve_suggestion("sug_test001")
        assert ok

        with open(rules_file) as f:
            rules = json.load(f)

        sug = [s for s in rules["suggested_rules"] if s["id"] == "sug_test001"][0]
        assert sug["status"] == "approved"
        assert len(rules["preferences"]) == 1
        assert rules["preferences"][0]["rule"] == "Always use max_turns=200"

    def test_approve_to_always_block(self, rules_file, monkeypatch):
        monkeypatch.setattr("cre.config.RULES_PATH", rules_file)
        ok = approve_suggestion("sug_test002", target_category="always_block")
        assert ok

        with open(rules_file) as f:
            rules = json.load(f)

        assert len(rules["always_block"]) == 1
        assert rules["always_block"][0]["pattern"] == "wrongdomain\\.com"
        assert rules["always_block"][0]["reason"] == "Wrong email domain"

    def test_approve_to_needs_llm_review(self, rules_file, monkeypatch):
        monkeypatch.setattr("cre.config.RULES_PATH", rules_file)
        ok = approve_suggestion("sug_test003", target_category="needs_llm_review")
        assert ok

        with open(rules_file) as f:
            rules = json.load(f)

        assert len(rules["needs_llm_review"]) == 1
        assert rules["needs_llm_review"][0]["pattern"] == "ssh|scp"

    def test_approve_to_always_allow(self, rules_file, monkeypatch):
        monkeypatch.setattr("cre.config.RULES_PATH", rules_file)
        ok = approve_suggestion("sug_test004", target_category="always_allow")
        assert ok

        with open(rules_file) as f:
            rules = json.load(f)

        assert len(rules["always_allow"]) == 1
        assert rules["always_allow"][0]["pattern"] == "^npm test"

    def test_approve_remove_type(self, rules_file, monkeypatch):
        """Approve a 'remove' suggestion removes the rule from the category."""
        monkeypatch.setattr("cre.config.RULES_PATH", rules_file)
        # First add a rule to remove
        with open(rules_file) as f:
            rules = json.load(f)
        rules["needs_llm_review"].append({"pattern": "ssh", "context": "Remote access"})
        with open(rules_file, 'w') as f:
            json.dump(rules, f)

        ok = approve_suggestion("sug_test005")
        assert ok

        with open(rules_file) as f:
            rules = json.load(f)

        # The ssh rule should be removed
        ssh_rules = [r for r in rules["needs_llm_review"] if r.get("pattern") == "ssh"]
        assert len(ssh_rules) == 0

    def test_approve_narrow_type(self, rules_file, monkeypatch):
        """Approve a 'narrow' suggestion replaces old pattern with new."""
        monkeypatch.setattr("cre.config.RULES_PATH", rules_file)
        # First add the old broad rule
        with open(rules_file) as f:
            rules = json.load(f)
        rules["needs_llm_review"].append({"pattern": "ssh", "context": "Remote access"})
        with open(rules_file, 'w') as f:
            json.dump(rules, f)

        ok = approve_suggestion("sug_test006")
        assert ok

        with open(rules_file) as f:
            rules = json.load(f)

        # Old "ssh" should be replaced with "ssh root@"
        patterns = [r.get("pattern") for r in rules["needs_llm_review"]]
        assert "ssh root@" in patterns
        assert "ssh" not in patterns

    def test_dismiss_marks_dismissed(self, rules_file, monkeypatch):
        monkeypatch.setattr("cre.config.RULES_PATH", rules_file)
        ok = dismiss_suggestion("sug_test002")
        assert ok

        with open(rules_file) as f:
            rules = json.load(f)

        sug = [s for s in rules["suggested_rules"] if s["id"] == "sug_test002"][0]
        assert sug["status"] == "dismissed"
        assert "dismissed_at" in sug

    def test_approve_nonexistent_returns_false(self, rules_file, monkeypatch):
        monkeypatch.setattr("cre.config.RULES_PATH", rules_file)
        assert not approve_suggestion("sug_fake")

    def test_dismiss_nonexistent_returns_false(self, rules_file, monkeypatch):
        monkeypatch.setattr("cre.config.RULES_PATH", rules_file)
        assert not dismiss_suggestion("sug_fake")


class TestExtractSection:
    """Extract content between section headers."""

    def test_extracts_section(self):
        content = """# CRE Memory

## Active Patterns
- [2026-03-01 10:00] DENY (bash): `ssh root` — No permission
- [2026-03-01 10:05] ALLOW (bash): `ls` — Safe

## Override Log
- [2026-03-01 11:00] ALLOW (override): `ssh root` — User approved

## Emerging Patterns
"""
        active = _extract_section(content, "## Active Patterns")
        assert "ssh root" in active
        assert "ls" in active
        assert "User approved" not in active

        override = _extract_section(content, "## Override Log")
        assert "User approved" in override
        assert "ssh root" in override

    def test_empty_section(self):
        content = "## Active Patterns\n\n## Override Log\n"
        result = _extract_section(content, "## Active Patterns")
        assert result == ""

    def test_missing_section(self):
        content = "## Active Patterns\nsome data\n"
        result = _extract_section(content, "## Nonexistent")
        assert result == ""
