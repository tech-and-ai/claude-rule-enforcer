"""Tests for the gate engine — Layer 1 regex + multi-tool routing + alignment check."""

import json
import os
import sys
import pytest

# Ensure src is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from cre.gate import (
    regex_check, make_output, process_bash, process_write_edit,
    _has_tool_instruction, _evaluate_alignment, _normalize_command,
)


@pytest.fixture
def rules():
    """Standard test rules."""
    return {
        "enabled": True,
        "llm_review_enabled": False,  # Disable L2 for unit tests
        "always_block": [
            {"pattern": ":(){ :|:& }", "reason": "Fork bomb"},
            {"pattern": "mkfs", "reason": "Filesystem format"},
            {"pattern": "dd of=/dev/", "reason": "Disk write to raw device"},
        ],
        "always_allow": [
            {"pattern": "^(ls|cat|head|tail|wc|echo|pwd)\\b"},
            {"pattern": "^git (status|log|diff|branch)"},
        ],
        "needs_llm_review": [
            {"pattern": "ssh|scp|rsync", "context": "Remote access"},
            {"pattern": "git push", "context": "Push code"},
            {"pattern": "rm -rf", "context": "Recursive delete"},
        ],
        "preferences": [],
        "suggested_rules": [],
        "learned_rules": [],
    }


class TestRegexCheck:
    """Layer 1 regex matching."""

    def test_allow_safe_commands(self, rules):
        assert regex_check("ls -la", rules)[0] == "allow"
        assert regex_check("cat README.md", rules)[0] == "allow"
        assert regex_check("git status", rules)[0] == "allow"
        assert regex_check("git log --oneline", rules)[0] == "allow"
        assert regex_check("echo hello", rules)[0] == "allow"

    def test_block_dangerous_commands(self, rules):
        assert regex_check(":(){ :|:& }", rules)[0] == "deny"
        assert regex_check("mkfs /dev/sda1", rules)[0] == "deny"
        assert regex_check("dd of=/dev/sda", rules)[0] == "deny"

    def test_review_flagged_commands(self, rules):
        assert regex_check("ssh root@prod", rules)[0] == "review"
        assert regex_check("scp file.txt server:", rules)[0] == "review"
        assert regex_check("git push origin main", rules)[0] == "review"
        assert regex_check("rm -rf /tmp/stuff", rules)[0] == "review"

    def test_no_match_allows(self, rules):
        decision, reason = regex_check("python3 -c 'print(1)'", rules)
        assert decision == "allow"
        assert "No policy rule matched" in reason

    def test_block_reason_included(self, rules):
        decision, reason = regex_check("mkfs", rules)
        assert decision == "deny"
        assert "Filesystem format" in reason

    def test_review_context_included(self, rules):
        decision, reason = regex_check("ssh server", rules)
        assert decision == "review"
        assert "Remote access" in reason


class TestCommandNormalisation:
    """L1 must catch evasion via string concatenation, empty quotes, etc."""

    def test_empty_double_quotes_evasion(self, rules):
        """cre_""enabled should normalise to cre_enabled and be blocked."""
        rules["always_block"].append({"pattern": "cre_enabled", "reason": "Gate toggle"})
        decision, _ = regex_check('rm -f "$HOME/.claude/cre_""enabled"', rules)
        assert decision == "deny", "Empty double-quote evasion should be caught"

    def test_empty_single_quotes_evasion(self, rules):
        """cre_''enabled should normalise to cre_enabled."""
        rules["always_block"].append({"pattern": "cre_enabled", "reason": "Gate toggle"})
        decision, _ = regex_check("rm -f ~/.claude/cre_''enabled", rules)
        assert decision == "deny", "Empty single-quote evasion should be caught"

    def test_adjacent_string_concat(self, rules):
        """Adjacent quoted strings: "cre_" "enabled" → cre_enabled."""
        rules["always_block"].append({"pattern": "cre_enabled", "reason": "Gate toggle"})
        decision, _ = regex_check('rm "$HOME/.claude/cre_" "enabled"', rules)
        assert decision == "deny", "Adjacent string concat evasion should be caught"

    def test_ansi_c_quoting(self, rules):
        """$'cre_enabled' ANSI-C quoting should be normalised."""
        rules["always_block"].append({"pattern": "cre_enabled", "reason": "Gate toggle"})
        decision, _ = regex_check("rm $'cre_enabled'", rules)
        assert decision == "deny", "ANSI-C quoting evasion should be caught"

    def test_normal_command_unaffected(self, rules):
        """Normalisation should not break legitimate commands."""
        decision, _ = regex_check("ls -la", rules)
        assert decision == "allow"

    def test_normal_quotes_unaffected(self, rules):
        """Commands with real quoted strings should still work."""
        decision, _ = regex_check('echo "hello world"', rules)
        assert decision == "allow"


class TestMakeOutput:
    """Hook output format."""

    def test_deny_output(self):
        out = make_output("deny", "Not allowed")
        assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert out["hookSpecificOutput"]["permissionDecisionReason"] == "Not allowed"
        assert out["hookSpecificOutput"]["hookEventName"] == "PreToolUse"

    def test_allow_is_empty(self):
        # Allow returns empty JSON (no hookSpecificOutput)
        # This is handled by the caller, not make_output
        pass


class TestProcessBash:
    """Bash tool processing."""

    def test_allow_safe_command(self, rules):
        hook = {"tool_name": "Bash", "tool_input": {"command": "ls -la"}}
        result = json.loads(process_bash(hook, rules))
        assert result == {}  # Empty = allow

    def test_deny_dangerous_command(self, rules):
        hook = {"tool_name": "Bash", "tool_input": {"command": "mkfs /dev/sda"}}
        result = json.loads(process_bash(hook, rules))
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_empty_command_allows(self, rules):
        hook = {"tool_name": "Bash", "tool_input": {"command": ""}}
        result = json.loads(process_bash(hook, rules))
        assert result == {}

    def test_review_without_llm_warns(self, rules):
        """When LLM review is disabled, review-flagged commands warn (advisory, not blocked)."""
        hook = {"tool_name": "Bash", "tool_input": {"command": "ssh root@prod"}}
        result = json.loads(process_bash(hook, rules))
        # L2 is advisory — returns empty JSON (allow) not a deny
        assert result == {}


class TestProcessWriteEdit:
    """Write/Edit tool processing."""

    def test_no_file_path_allows(self, rules):
        hook = {"tool_name": "Write", "tool_input": {"file_path": "", "content": "hello"}}
        result = json.loads(process_write_edit(hook, rules))
        assert result == {}

    def test_write_without_llm_allows(self, rules):
        """When LLM review is disabled, Write/Edit pass through."""
        hook = {"tool_name": "Write", "tool_input": {"file_path": "/tmp/test.py", "content": "print('hi')"}}
        result = json.loads(process_write_edit(hook, rules))
        assert result == {}

    def test_edit_without_llm_allows(self, rules):
        hook = {"tool_name": "Edit", "tool_input": {"file_path": "/tmp/test.py", "new_string": "fixed"}}
        result = json.loads(process_write_edit(hook, rules))
        assert result == {}


class TestHasToolInstruction:
    """L1 triage — detect tool instructions in user messages."""

    def test_detects_use_grounded(self):
        msgs = [{"role": "user", "content": "use grounded to look up that conversation"}]
        assert _has_tool_instruction(msgs) is not None
        assert "grounded" in _has_tool_instruction(msgs).lower()

    def test_detects_use_the_skill(self):
        msgs = [{"role": "user", "content": "use the GLM-5 skill to rewrite these"}]
        result = _has_tool_instruction(msgs)
        assert result is not None
        assert "GLM-5" in result

    def test_detects_run_agent(self):
        msgs = [{"role": "user", "content": "run the research agent on this topic"}]
        result = _has_tool_instruction(msgs)
        assert result is not None
        assert "research" in result.lower()

    def test_detects_via(self):
        msgs = [{"role": "user", "content": "send it via the email skill"}]
        result = _has_tool_instruction(msgs)
        assert result is not None
        assert "email" in result.lower()

    def test_detects_check_grounded(self):
        msgs = [{"role": "user", "content": "check grounded for what I said last week"}]
        result = _has_tool_instruction(msgs)
        assert result is not None

    def test_no_instruction_in_generic_message(self):
        msgs = [{"role": "user", "content": "look up the weather in London"}]
        assert _has_tool_instruction(msgs) is None

    def test_no_instruction_in_empty(self):
        assert _has_tool_instruction([]) is None

    def test_ignores_assistant_messages(self):
        msgs = [{"role": "assistant", "content": "I'll use grounded to look that up"}]
        assert _has_tool_instruction(msgs) is None

    def test_finds_instruction_in_earlier_message(self):
        """Instruction persists even if the most recent message is generic."""
        msgs = [
            {"role": "user", "content": "use grounded to check"},
            {"role": "assistant", "content": "OK"},
            {"role": "user", "content": "what's the status?"},
        ]
        # The instruction from the earlier message still applies
        result = _has_tool_instruction(msgs)
        assert result is not None
        assert "grounded" in result.lower()


class TestKBContextInjection:
    """L1.5 knowledge base context injection in gate pipeline."""

    def test_context_injected_on_allow(self, tmp_path):
        """When KB matches an allowed command, context is injected."""
        import subprocess
        rules = {
            "enabled": True,
            "always_block": [],
            "always_allow": [{"pattern": "^ls\\b"}],
            "needs_llm_review": [],
        }
        rules_file = tmp_path / "rules.json"
        rules_file.write_text(json.dumps(rules))

        kb = {
            "context_patterns": [
                {"pattern": "10\\.0\\.0\\.1", "context": "GPU server on port 11434", "category": "server"}
            ],
            "version": "1.0",
            "last_synced": None,
        }
        kb_file = tmp_path / "kb.json"
        kb_file.write_text(json.dumps(kb))

        # Command that contains the IP but is also allowed by L1
        hook_input = json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls 10.0.0.1"}})

        result = subprocess.run(
            [sys.executable, "-m", "cre.cli", "gate"],
            input=hook_input, capture_output=True, text=True,
            env={
                **os.environ,
                "PYTHONPATH": os.path.join(os.path.dirname(__file__), '..', 'src'),
                "CRE_RULES_PATH": str(rules_file),
                "CRE_KB_PATH": str(kb_file),
            }
        )
        assert result.returncode == 0
        output = json.loads(result.stdout)
        # Should have additionalContext injected
        assert "additionalContext" in output.get("hookSpecificOutput", {})
        assert "GPU" in output["hookSpecificOutput"]["additionalContext"]

    def test_no_context_when_no_match(self, tmp_path):
        """When KB doesn't match, output is plain allow ({})."""
        import subprocess
        rules = {
            "enabled": True,
            "always_block": [],
            "always_allow": [{"pattern": "^ls\\b"}],
            "needs_llm_review": [],
        }
        rules_file = tmp_path / "rules.json"
        rules_file.write_text(json.dumps(rules))

        kb = {
            "context_patterns": [
                {"pattern": "10\\.0\\.0\\.1", "context": "GPU server", "category": "server"}
            ],
            "version": "1.0",
            "last_synced": None,
        }
        kb_file = tmp_path / "kb.json"
        kb_file.write_text(json.dumps(kb))

        hook_input = json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls -la"}})

        result = subprocess.run(
            [sys.executable, "-m", "cre.cli", "gate"],
            input=hook_input, capture_output=True, text=True,
            env={
                **os.environ,
                "PYTHONPATH": os.path.join(os.path.dirname(__file__), '..', 'src'),
                "CRE_RULES_PATH": str(rules_file),
                "CRE_KB_PATH": str(kb_file),
            }
        )
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output == {}  # Plain allow, no context

    def test_deny_not_affected_by_kb(self, tmp_path):
        """KB context should NOT be injected on deny — only on allow."""
        import subprocess
        rules = {
            "enabled": True,
            "always_block": [{"pattern": "mkfs", "reason": "Format disk"}],
            "always_allow": [],
            "needs_llm_review": [],
        }
        rules_file = tmp_path / "rules.json"
        rules_file.write_text(json.dumps(rules))

        kb = {
            "context_patterns": [
                {"pattern": "mkfs", "context": "This is dangerous", "category": "workflow"}
            ],
            "version": "1.0",
            "last_synced": None,
        }
        kb_file = tmp_path / "kb.json"
        kb_file.write_text(json.dumps(kb))

        enabled_file = tmp_path / "cre_enabled"
        enabled_file.touch()

        hook_input = json.dumps({"tool_name": "Bash", "tool_input": {"command": "mkfs /dev/sda"}})

        result = subprocess.run(
            [sys.executable, "-m", "cre.cli", "gate"],
            input=hook_input, capture_output=True, text=True,
            env={
                **os.environ,
                "PYTHONPATH": os.path.join(os.path.dirname(__file__), '..', 'src'),
                "CRE_RULES_PATH": str(rules_file),
                "CRE_KB_PATH": str(kb_file),
                "CRE_ENABLED_PATH": str(enabled_file),
            }
        )
        assert result.returncode == 2  # deny
        output = json.loads(result.stdout)
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "additionalContext" not in output.get("hookSpecificOutput", {})


class TestEvaluateAlignment:
    """Alignment evaluation path."""

    def test_disabled_always_allows(self):
        rules = {"alignment_check_enabled": False, "llm_review_enabled": False}
        normalized = {
            "tool_type": "websearch",
            "tool_name": "WebSearch",
            "tool_input": {"query": "test"},
            "command": "",
            "raw": {},
        }
        decision, reason = _evaluate_alignment(normalized, rules)
        assert decision == "allow"
        assert "disabled" in reason.lower()

    def test_no_instruction_allows(self):
        """When no tool instruction is detected, allow immediately."""
        rules = {"alignment_check_enabled": True, "llm_review_enabled": False}
        normalized = {
            "tool_type": "websearch",
            "tool_name": "WebSearch",
            "tool_input": {"query": "weather"},
            "command": "",
            "raw": {},
        }
        # Mock: search_recent_conversation returns generic messages
        from unittest.mock import patch
        mock_msgs = [{"role": "user", "content": "what's the weather?", "timestamp": "2026-03-01T10:00:00"}]
        with patch("cre.gate.search_recent_conversation", return_value=mock_msgs):
            decision, reason = _evaluate_alignment(normalized, rules)
        assert decision == "allow"
        assert "No specific tool instruction" in reason

    def test_instruction_detected_but_llm_disabled_allows(self):
        """When instruction is detected but LLM review is off, allow (can't check)."""
        rules = {"alignment_check_enabled": True, "llm_review_enabled": False}
        normalized = {
            "tool_type": "websearch",
            "tool_name": "WebSearch",
            "tool_input": {"query": "test"},
            "command": "",
            "raw": {},
        }
        mock_msgs = [{"role": "user", "content": "use grounded to look this up", "timestamp": "2026-03-01T10:00:00"}]
        from unittest.mock import patch
        with patch("cre.gate.search_recent_conversation", return_value=mock_msgs):
            decision, reason = _evaluate_alignment(normalized, rules)
        assert decision == "allow"
        assert "LLM review disabled" in reason
