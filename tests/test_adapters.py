"""Tests for adapter layer — protocol translation between tools and CRE."""

import json
import os
import sys
import subprocess
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from cre.adapters import detect_adapter, get_adapter
from cre.adapters.claude_code import ClaudeCodeAdapter
from cre.adapters.generic import GenericAdapter


class TestAutoDetect:
    """Auto-detection of input format."""

    def test_detects_claude_code(self):
        raw = {"tool_name": "Bash", "tool_input": {"command": "ls"}}
        adapter = detect_adapter(raw)
        assert isinstance(adapter, ClaudeCodeAdapter)

    def test_detects_generic(self):
        raw = {"tool": "bash", "command": "ls"}
        adapter = detect_adapter(raw)
        assert isinstance(adapter, GenericAdapter)

    def test_unknown_format_defaults_to_generic(self):
        raw = {"something": "else"}
        adapter = detect_adapter(raw)
        assert isinstance(adapter, GenericAdapter)

    def test_get_adapter_by_name(self):
        adapter = get_adapter(name="claude-code")
        assert isinstance(adapter, ClaudeCodeAdapter)

    def test_get_adapter_by_name_generic(self):
        adapter = get_adapter(name="generic")
        assert isinstance(adapter, GenericAdapter)


class TestClaudeCodeAdapter:
    """Claude Code format parsing and output."""

    def test_parse_bash(self):
        adapter = ClaudeCodeAdapter()
        raw = {"tool_name": "Bash", "tool_input": {"command": "ls -la"}}
        n = adapter.parse_input(raw)
        assert n["tool_type"] == "bash"
        assert n["command"] == "ls -la"

    def test_parse_write(self):
        adapter = ClaudeCodeAdapter()
        raw = {"tool_name": "Write", "tool_input": {"file_path": "/tmp/x", "content": "hello"}}
        n = adapter.parse_input(raw)
        assert n["tool_type"] == "write"
        assert n["file_path"] == "/tmp/x"
        assert n["content"] == "hello"

    def test_parse_edit(self):
        adapter = ClaudeCodeAdapter()
        raw = {"tool_name": "Edit", "tool_input": {"file_path": "/tmp/x", "old_string": "a", "new_string": "b"}}
        n = adapter.parse_input(raw)
        assert n["tool_type"] == "edit"
        assert n["content"] == "b"

    def test_format_allow(self):
        adapter = ClaudeCodeAdapter()
        assert json.loads(adapter.format_allow()) == {}

    def test_format_deny(self):
        adapter = ClaudeCodeAdapter()
        out = json.loads(adapter.format_deny("bad command"))
        assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert out["hookSpecificOutput"]["permissionDecisionReason"] == "bad command"

    def test_exit_codes(self):
        adapter = ClaudeCodeAdapter()
        assert adapter.exit_allow == 0
        assert adapter.exit_deny == 2


class TestClaudeCodeAdapterContextInjection:
    """format_allow_with_context for KB injection."""

    def test_format_allow_with_context(self):
        adapter = ClaudeCodeAdapter()
        result = json.loads(adapter.format_allow_with_context("Ollama server, port 11434"))
        assert result["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
        assert result["hookSpecificOutput"]["additionalContext"] == "Ollama server, port 11434"
        # No permissionDecision = allow (exit code 0 controls that)
        assert "permissionDecision" not in result["hookSpecificOutput"]

    def test_format_allow_with_context_exit_code(self):
        """Context injection still uses exit code 0 (allow)."""
        adapter = ClaudeCodeAdapter()
        assert adapter.exit_allow == 0


class TestGenericAdapter:
    """Generic format parsing and output."""

    def test_parse_bash(self):
        adapter = GenericAdapter()
        raw = {"tool": "bash", "command": "ls -la"}
        n = adapter.parse_input(raw)
        assert n["tool_type"] == "bash"
        assert n["command"] == "ls -la"

    def test_parse_write(self):
        adapter = GenericAdapter()
        raw = {"tool": "write", "file_path": "/tmp/x", "content": "hello"}
        n = adapter.parse_input(raw)
        assert n["tool_type"] == "write"
        assert n["file_path"] == "/tmp/x"

    def test_format_allow(self):
        adapter = GenericAdapter()
        out = json.loads(adapter.format_allow())
        assert out["decision"] == "allow"

    def test_format_deny(self):
        adapter = GenericAdapter()
        out = json.loads(adapter.format_deny("blocked"))
        assert out["decision"] == "deny"
        assert out["reason"] == "blocked"

    def test_exit_codes(self):
        adapter = GenericAdapter()
        assert adapter.exit_allow == 0
        assert adapter.exit_deny == 1


class TestGateGenericFormat:
    """End-to-end: cre gate with generic JSON input format."""

    def test_generic_allow(self, tmp_path):
        rules = {
            "enabled": True,
            "always_block": [],
            "always_allow": [{"pattern": "^ls\\b"}],
            "needs_llm_review": [],
        }
        rules_file = tmp_path / "rules.json"
        rules_file.write_text(json.dumps(rules))

        hook_input = json.dumps({"tool": "bash", "command": "ls -la"})

        result = subprocess.run(
            [sys.executable, "-m", "cre.cli", "gate", "--format", "generic"],
            input=hook_input, capture_output=True, text=True,
            env={
                **os.environ,
                "PYTHONPATH": os.path.join(os.path.dirname(__file__), '..', 'src'),
                "CRE_RULES_PATH": str(rules_file),
            }
        )
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output["decision"] == "allow"

    def test_generic_deny(self, tmp_path):
        rules = {
            "enabled": True,
            "always_block": [{"pattern": "mkfs", "reason": "Filesystem format"}],
            "always_allow": [], "needs_llm_review": [],
        }
        rules_file = tmp_path / "rules.json"
        rules_file.write_text(json.dumps(rules))

        hook_input = json.dumps({"tool": "bash", "command": "mkfs /dev/sda"})

        # Create toggle file so gate is enabled
        enabled_file = tmp_path / "cre_enabled"
        enabled_file.touch()

        result = subprocess.run(
            [sys.executable, "-m", "cre.cli", "gate", "--format", "generic"],
            input=hook_input, capture_output=True, text=True,
            env={
                **os.environ,
                "PYTHONPATH": os.path.join(os.path.dirname(__file__), '..', 'src'),
                "CRE_RULES_PATH": str(rules_file),
                "CRE_ENABLED_PATH": str(enabled_file),
            }
        )
        assert result.returncode == 1  # Generic uses exit 1 for deny
        output = json.loads(result.stdout)
        assert output["decision"] == "deny"

    def test_autodetect_claude_code(self, tmp_path):
        """Without --format, Claude Code input auto-detects correctly."""
        rules = {
            "enabled": True,
            "always_block": [],
            "always_allow": [{"pattern": "^ls\\b"}],
            "needs_llm_review": [],
        }
        rules_file = tmp_path / "rules.json"
        rules_file.write_text(json.dumps(rules))

        hook_input = json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls -la"}})

        result = subprocess.run(
            [sys.executable, "-m", "cre.cli", "gate"],
            input=hook_input, capture_output=True, text=True,
            env={
                **os.environ,
                "PYTHONPATH": os.path.join(os.path.dirname(__file__), '..', 'src'),
                "CRE_RULES_PATH": str(rules_file),
            }
        )
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output == {}  # Claude Code allow format
