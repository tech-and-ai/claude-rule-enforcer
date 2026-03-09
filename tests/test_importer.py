"""Tests for the instruction file importer."""

import json
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from cre.importer import apply_rules, format_preview, read_instruction_file, cross_reference_with_existing, save_as_suggestions


class TestReadFile:
    """Reading instruction files."""

    def test_reads_existing_file(self, tmp_path):
        f = tmp_path / "CLAUDE.md"
        f.write_text("# Rules\nNever push to main")
        content, err = read_instruction_file(str(f))
        assert err is None
        assert "Never push to main" in content

    def test_missing_file_returns_error(self):
        content, err = read_instruction_file("/tmp/nonexistent_cre_test.md")
        assert content is None
        assert "not found" in err


class TestApplyRules:
    """Applying extracted rules directly to rules.json (--direct mode)."""

    def test_adds_block_rule(self):
        extracted = [
            {"category": "always_block", "pattern": "rm -rf /", "reason": "Root delete", "enforceable": True}
        ]
        rules = {"always_block": [], "always_allow": [], "needs_llm_review": [], "preferences": []}
        num, summary = apply_rules(extracted, rules)
        assert num == 1
        assert len(rules["always_block"]) == 1
        assert rules["always_block"][0]["pattern"] == "rm -rf /"
        assert rules["always_block"][0]["source"] == "import"

    def test_adds_review_rule(self):
        extracted = [
            {"category": "needs_llm_review", "pattern": "deploy", "context": "Check approval", "enforceable": True}
        ]
        rules = {"always_block": [], "always_allow": [], "needs_llm_review": [], "preferences": []}
        num, summary = apply_rules(extracted, rules)
        assert num == 1
        assert rules["needs_llm_review"][0]["pattern"] == "deploy"

    def test_adds_preference(self):
        extracted = [
            {"category": "preference", "rule": "Always use max_turns=200", "confidence": "high", "enforceable": True}
        ]
        rules = {"always_block": [], "always_allow": [], "needs_llm_review": [], "preferences": []}
        num, summary = apply_rules(extracted, rules)
        assert num == 1
        assert rules["preferences"][0]["rule"] == "Always use max_turns=200"

    def test_adds_allow_rule(self):
        extracted = [
            {"category": "always_allow", "pattern": "^npm test", "enforceable": True}
        ]
        rules = {"always_block": [], "always_allow": [], "needs_llm_review": [], "preferences": []}
        num, summary = apply_rules(extracted, rules)
        assert num == 1
        assert rules["always_allow"][0]["pattern"] == "^npm test"

    def test_skips_duplicates(self):
        extracted = [
            {"category": "always_block", "pattern": "mkfs", "reason": "Format disk", "enforceable": True}
        ]
        rules = {"always_block": [{"pattern": "mkfs", "reason": "Already exists"}], "always_allow": [], "needs_llm_review": [], "preferences": []}
        num, summary = apply_rules(extracted, rules)
        assert num == 0
        assert len(rules["always_block"]) == 1  # Not duplicated

    def test_skips_non_enforceable(self):
        extracted = [
            {"category": "always_block", "pattern": "test", "reason": "Test", "enforceable": False}
        ]
        rules = {"always_block": [], "always_allow": [], "needs_llm_review": [], "preferences": []}
        num, summary = apply_rules(extracted, rules)
        assert num == 0

    def test_mixed_categories(self):
        extracted = [
            {"category": "always_block", "pattern": "mkfs", "reason": "Disk format", "enforceable": True},
            {"category": "needs_llm_review", "pattern": "ssh", "context": "Remote access", "enforceable": True},
            {"category": "preference", "rule": "Use vim keybindings", "confidence": "medium", "enforceable": True},
        ]
        rules = {"always_block": [], "always_allow": [], "needs_llm_review": [], "preferences": []}
        num, summary = apply_rules(extracted, rules)
        assert num == 3
        assert len(summary["always_block"]) == 1
        assert len(summary["needs_llm_review"]) == 1
        assert len(summary["preference"]) == 1

    def test_empty_input(self):
        rules = {"always_block": [], "always_allow": [], "needs_llm_review": [], "preferences": []}
        num, summary = apply_rules([], rules)
        assert num == 0


class TestFormatPreview:
    """Preview formatting."""

    def test_formats_mixed_rules(self):
        extracted = [
            {"category": "always_block", "pattern": "mkfs", "reason": "Disk format", "source_line": 5, "source_text": "Never format disks"},
            {"category": "preference", "rule": "Use British English", "source_line": 10, "source_text": "Use British English"},
        ]
        output = format_preview(extracted)
        assert "always_block" in output
        assert "preference" in output
        assert "2 enforceable rules" in output

    def test_empty_returns_zero(self):
        output = format_preview([])
        assert "0 enforceable rules" in output


class TestCrossReference:
    """Cross-referencing extracted rules with existing rules."""

    def test_detects_duplicate_block_pattern(self):
        extracted = [
            {"category": "always_block", "pattern": "mkfs", "reason": "Format", "enforceable": True},
            {"category": "always_block", "pattern": "new_pattern", "reason": "New", "enforceable": True},
        ]
        rules = {"always_block": [{"pattern": "mkfs", "reason": "Existing"}], "always_allow": [], "needs_llm_review": [], "preferences": []}
        clean, conflicts = cross_reference_with_existing(extracted, rules)
        assert len(clean) == 1
        assert clean[0]["pattern"] == "new_pattern"
        assert len(conflicts) == 1
        assert conflicts[0]["pattern"] == "mkfs"

    def test_detects_duplicate_preference(self):
        extracted = [
            {"category": "preference", "rule": "Always use max_turns=200", "enforceable": True},
        ]
        rules = {"always_block": [], "always_allow": [], "needs_llm_review": [], "preferences": [{"rule": "Always use max_turns=200"}]}
        clean, conflicts = cross_reference_with_existing(extracted, rules)
        assert len(clean) == 0
        assert len(conflicts) == 1

    def test_no_conflicts(self):
        extracted = [
            {"category": "always_block", "pattern": "rm -rf /", "reason": "Root delete", "enforceable": True},
        ]
        rules = {"always_block": [], "always_allow": [], "needs_llm_review": [], "preferences": []}
        clean, conflicts = cross_reference_with_existing(extracted, rules)
        assert len(clean) == 1
        assert len(conflicts) == 0

    def test_skips_non_enforceable(self):
        extracted = [
            {"category": "always_block", "pattern": "test", "enforceable": False},
        ]
        rules = {"always_block": [], "always_allow": [], "needs_llm_review": [], "preferences": []}
        clean, conflicts = cross_reference_with_existing(extracted, rules)
        assert len(clean) == 0
        assert len(conflicts) == 0


class TestSaveAsSuggestions:
    """Import creates suggestions instead of direct rules."""

    @pytest.fixture
    def rules_file(self, tmp_path, monkeypatch):
        rules = {
            "enabled": True,
            "always_block": [],
            "always_allow": [],
            "needs_llm_review": [],
            "learned_rules": [],
            "suggested_rules": [],
            "preferences": [],
        }
        p = tmp_path / "rules.json"
        p.write_text(json.dumps(rules, indent=2))
        monkeypatch.setattr("cre.config.RULES_PATH", str(p))
        return str(p)

    def test_creates_suggestions_not_direct_rules(self, rules_file):
        extracted = [
            {"category": "always_block", "pattern": "mkfs", "reason": "Disk format", "enforceable": True, "source_line": 5, "source_text": "Never format disks"},
        ]
        num = save_as_suggestions(extracted, filename="CLAUDE.md")
        assert num == 1

        with open(rules_file) as f:
            rules = json.load(f)

        # Should be in suggested_rules, NOT in always_block
        assert len(rules["always_block"]) == 0
        assert len(rules["suggested_rules"]) == 1
        sug = rules["suggested_rules"][0]
        assert sug["source"] == "import"
        assert sug["target_category"] == "always_block"
        assert sug["pattern"] == "mkfs"
        assert sug["status"] == "pending"

    def test_dedup_prevents_duplicate_suggestions(self, rules_file):
        extracted = [
            {"category": "always_block", "pattern": "mkfs", "reason": "Disk format", "enforceable": True},
        ]
        num1 = save_as_suggestions(extracted, filename="file1.md")
        num2 = save_as_suggestions(extracted, filename="file2.md")
        assert num1 == 1
        assert num2 == 0  # Dedup caught it

    def test_preference_target_category(self, rules_file):
        extracted = [
            {"category": "preference", "rule": "Always check tests", "confidence": "high", "enforceable": True},
        ]
        num = save_as_suggestions(extracted, filename="CLAUDE.md")
        assert num == 1

        with open(rules_file) as f:
            rules = json.load(f)

        sug = rules["suggested_rules"][0]
        assert sug["target_category"] == "preference"
        assert sug["source"] == "import"
