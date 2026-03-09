"""Tests for L2 pattern promotion — repeated denials become L1 suggestions."""

import json
import os
import sys
import pytest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from cre.learner import create_promotion_suggestion


@pytest.fixture
def rules_file(tmp_path):
    """Create a temp rules.json."""
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
    return str(p)


class TestCreatePromotionSuggestion:
    """Creating promotion suggestions from repeated L2 denials."""

    def test_creates_suggestion(self, rules_file, monkeypatch):
        monkeypatch.setattr("cre.config.RULES_PATH", rules_file)
        result = create_promotion_suggestion(
            pattern="ssh",
            command_examples=["ssh root@prod", "ssh admin@server", "ssh deploy@staging"],
            deny_count=3,
            suggested_category="always_block"
        )
        assert result is not None
        assert result["source"] == "l2_promotion"
        assert result["suggestion_type"] == "new_rule"
        assert result["target_category"] == "always_block"
        assert result["pattern"] == "ssh"
        assert result["confidence"] == "high"
        assert len(result["evidence"]) == 3

    def test_saves_to_rules_json(self, rules_file, monkeypatch):
        monkeypatch.setattr("cre.config.RULES_PATH", rules_file)
        create_promotion_suggestion("deploy", ["deploy prod", "deploy staging", "deploy live"], 3)
        with open(rules_file) as f:
            rules = json.load(f)
        pending = [s for s in rules["suggested_rules"] if s["status"] == "pending"]
        assert len(pending) == 1
        assert pending[0]["pattern"] == "deploy"

    def test_dedup_prevents_duplicate(self, rules_file, monkeypatch):
        monkeypatch.setattr("cre.config.RULES_PATH", rules_file)
        first = create_promotion_suggestion("ssh", ["ssh a", "ssh b", "ssh c"], 3)
        assert first is not None
        second = create_promotion_suggestion("ssh", ["ssh d", "ssh e", "ssh f"], 3)
        assert second is None  # Dedup caught it

    def test_suggestion_format(self, rules_file, monkeypatch):
        monkeypatch.setattr("cre.config.RULES_PATH", rules_file)
        result = create_promotion_suggestion("rm -rf", ["rm -rf /tmp", "rm -rf ./build"], 5)
        assert "id" in result
        assert result["id"].startswith("sug_")
        assert "denied 5 times" in result.get("reason", "").lower() or "5" in result.get("proposed_rule", "")
