"""Tests for the CLI entry point."""

import json
import os
import sys
import subprocess
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


class TestCLIHelp:
    """CLI responds to --help and --version."""

    def test_help(self):
        result = subprocess.run(
            [sys.executable, "-m", "cre.cli", "--help"],
            capture_output=True, text=True,
            env={**os.environ, "PYTHONPATH": os.path.join(os.path.dirname(__file__), '..', 'src')}
        )
        assert result.returncode == 0
        assert "Claude Rule Enforcer" in result.stdout

    def test_version(self):
        result = subprocess.run(
            [sys.executable, "-m", "cre.cli", "--version"],
            capture_output=True, text=True,
            env={**os.environ, "PYTHONPATH": os.path.join(os.path.dirname(__file__), '..', 'src')}
        )
        assert result.returncode == 0
        assert "0.3.0" in result.stdout


class TestCLIStatus:
    """cre status command."""

    def test_status_runs(self, tmp_path):
        rules = {
            "enabled": True, "llm_review_enabled": True,
            "always_block": [{"pattern": "mkfs", "reason": "test"}],
            "always_allow": [], "needs_llm_review": [],
            "learned_rules": [], "suggested_rules": [], "preferences": [],
        }
        rules_file = tmp_path / "rules.json"
        rules_file.write_text(json.dumps(rules))

        result = subprocess.run(
            [sys.executable, "-m", "cre.cli", "status"],
            capture_output=True, text=True,
            env={
                **os.environ,
                "PYTHONPATH": os.path.join(os.path.dirname(__file__), '..', 'src'),
                "CRE_RULES_PATH": str(rules_file),
            }
        )
        assert result.returncode == 0
        assert "Claude Rule Enforcer" in result.stdout
        assert "always_block" in result.stdout


class TestCLITest:
    """cre test command."""

    def test_allow_command(self, tmp_path):
        rules = {
            "enabled": True, "always_block": [],
            "always_allow": [{"pattern": "^ls\\b"}],
            "needs_llm_review": [],
        }
        rules_file = tmp_path / "rules.json"
        rules_file.write_text(json.dumps(rules))

        result = subprocess.run(
            [sys.executable, "-m", "cre.cli", "test", "ls -la"],
            capture_output=True, text=True,
            env={
                **os.environ,
                "PYTHONPATH": os.path.join(os.path.dirname(__file__), '..', 'src'),
                "CRE_RULES_PATH": str(rules_file),
            }
        )
        assert result.returncode == 0
        assert "ALLOW" in result.stdout

    def test_deny_command(self, tmp_path):
        rules = {
            "enabled": True,
            "always_block": [{"pattern": "mkfs", "reason": "Filesystem format"}],
            "always_allow": [], "needs_llm_review": [],
        }
        rules_file = tmp_path / "rules.json"
        rules_file.write_text(json.dumps(rules))

        result = subprocess.run(
            [sys.executable, "-m", "cre.cli", "test", "mkfs /dev/sda"],
            capture_output=True, text=True,
            env={
                **os.environ,
                "PYTHONPATH": os.path.join(os.path.dirname(__file__), '..', 'src'),
                "CRE_RULES_PATH": str(rules_file),
            }
        )
        assert result.returncode == 0
        assert "DENY" in result.stdout

    def test_review_command(self, tmp_path):
        rules = {
            "enabled": True, "always_block": [], "always_allow": [],
            "needs_llm_review": [{"pattern": "ssh", "context": "Remote access"}],
        }
        rules_file = tmp_path / "rules.json"
        rules_file.write_text(json.dumps(rules))

        result = subprocess.run(
            [sys.executable, "-m", "cre.cli", "test", "ssh root@prod"],
            capture_output=True, text=True,
            env={
                **os.environ,
                "PYTHONPATH": os.path.join(os.path.dirname(__file__), '..', 'src'),
                "CRE_RULES_PATH": str(rules_file),
            }
        )
        assert result.returncode == 0
        assert "REVIEW" in result.stdout


class TestCLIRules:
    """cre rules list/add commands."""

    def test_rules_list(self, tmp_path):
        rules = {
            "enabled": True,
            "always_block": [{"pattern": "mkfs", "reason": "Format"}],
            "always_allow": [{"pattern": "^ls\\b"}],
            "needs_llm_review": [{"pattern": "ssh", "context": "Remote"}],
            "preferences": [], "suggested_rules": [], "learned_rules": [],
        }
        rules_file = tmp_path / "rules.json"
        rules_file.write_text(json.dumps(rules))

        result = subprocess.run(
            [sys.executable, "-m", "cre.cli", "rules", "list"],
            capture_output=True, text=True,
            env={
                **os.environ,
                "PYTHONPATH": os.path.join(os.path.dirname(__file__), '..', 'src'),
                "CRE_RULES_PATH": str(rules_file),
            }
        )
        assert result.returncode == 0
        assert "mkfs" in result.stdout
        assert "ssh" in result.stdout

    def test_rules_add_block(self, tmp_path):
        rules = {
            "enabled": True, "always_block": [], "always_allow": [],
            "needs_llm_review": [], "preferences": [], "suggested_rules": [],
            "learned_rules": [],
        }
        rules_file = tmp_path / "rules.json"
        rules_file.write_text(json.dumps(rules))

        result = subprocess.run(
            [sys.executable, "-m", "cre.cli", "rules", "add", "--block", "rm -rf /", "--reason", "Root delete"],
            capture_output=True, text=True,
            env={
                **os.environ,
                "PYTHONPATH": os.path.join(os.path.dirname(__file__), '..', 'src'),
                "CRE_RULES_PATH": str(rules_file),
            }
        )
        assert result.returncode == 0
        assert "always_block" in result.stdout

        # Verify rule was written
        with open(rules_file) as f:
            updated = json.load(f)
        assert len(updated["always_block"]) == 1
        assert updated["always_block"][0]["pattern"] == "rm -rf /"


class TestCLIMemory:
    """cre memory command."""

    def test_memory_no_file(self, tmp_path):
        cre_md = tmp_path / "CRE.md"
        result = subprocess.run(
            [sys.executable, "-m", "cre.cli", "memory"],
            capture_output=True, text=True,
            env={
                **os.environ,
                "PYTHONPATH": os.path.join(os.path.dirname(__file__), '..', 'src'),
                "CRE_MD_PATH": str(cre_md),
            }
        )
        assert result.returncode == 0
        assert "empty" in result.stdout.lower() or "doesn't exist" in result.stdout.lower()

    def test_memory_stats(self, tmp_path):
        cre_md = tmp_path / "CRE.md"
        cre_md.write_text("# CRE Memory\n\n## Active Patterns\n- [2026-03-01 10:00] DENY: test\n")
        result = subprocess.run(
            [sys.executable, "-m", "cre.cli", "memory", "stats"],
            capture_output=True, text=True,
            env={
                **os.environ,
                "PYTHONPATH": os.path.join(os.path.dirname(__file__), '..', 'src'),
                "CRE_MD_PATH": str(cre_md),
            }
        )
        assert result.returncode == 0
        assert "Entries" in result.stdout or "entries" in result.stdout.lower()

    def test_memory_clear(self, tmp_path):
        cre_md = tmp_path / "CRE.md"
        cre_md.write_text("# CRE Memory\n\n## Active Patterns\n- [2026-03-01 10:00] DENY: test\n")
        result = subprocess.run(
            [sys.executable, "-m", "cre.cli", "memory", "clear"],
            capture_output=True, text=True,
            env={
                **os.environ,
                "PYTHONPATH": os.path.join(os.path.dirname(__file__), '..', 'src'),
                "CRE_MD_PATH": str(cre_md),
            }
        )
        assert result.returncode == 0
        assert "cleared" in result.stdout.lower()


class TestCLIKB:
    """cre kb commands."""

    def test_kb_list(self, tmp_path):
        kb = {
            "context_patterns": [
                {"pattern": "ssh.*10\\.0\\.0\\.1", "context": "GPU server", "category": "server"},
                {"pattern": "git push", "context": "Private repo", "category": "workflow"},
            ],
            "version": "1.0",
            "last_synced": None,
        }
        kb_file = tmp_path / "kb.json"
        kb_file.write_text(json.dumps(kb))

        result = subprocess.run(
            [sys.executable, "-m", "cre.cli", "kb", "list"],
            capture_output=True, text=True,
            env={
                **os.environ,
                "PYTHONPATH": os.path.join(os.path.dirname(__file__), '..', 'src'),
                "CRE_KB_PATH": str(kb_file),
            }
        )
        assert result.returncode == 0
        assert "GPU" in result.stdout
        assert "server" in result.stdout
        assert "2 patterns" in result.stdout

    def test_kb_test_match(self, tmp_path):
        kb = {
            "context_patterns": [
                {"pattern": "10\\.0\\.0\\.1", "context": "GPU server port 11434", "category": "server"},
            ],
            "version": "1.0",
            "last_synced": None,
        }
        kb_file = tmp_path / "kb.json"
        kb_file.write_text(json.dumps(kb))

        result = subprocess.run(
            [sys.executable, "-m", "cre.cli", "kb", "test", "ssh admin@10.0.0.1"],
            capture_output=True, text=True,
            env={
                **os.environ,
                "PYTHONPATH": os.path.join(os.path.dirname(__file__), '..', 'src'),
                "CRE_KB_PATH": str(kb_file),
            }
        )
        assert result.returncode == 0
        assert "MATCH" in result.stdout
        assert "GPU" in result.stdout

    def test_kb_test_no_match(self, tmp_path):
        kb = {"context_patterns": [], "version": "1.0", "last_synced": None}
        kb_file = tmp_path / "kb.json"
        kb_file.write_text(json.dumps(kb))

        result = subprocess.run(
            [sys.executable, "-m", "cre.cli", "kb", "test", "ls -la"],
            capture_output=True, text=True,
            env={
                **os.environ,
                "PYTHONPATH": os.path.join(os.path.dirname(__file__), '..', 'src'),
                "CRE_KB_PATH": str(kb_file),
            }
        )
        assert result.returncode == 0
        assert "NO MATCH" in result.stdout

    def test_kb_add_and_remove(self, tmp_path):
        kb_file = tmp_path / "kb.json"
        kb_file.write_text(json.dumps({"context_patterns": [], "version": "1.0", "last_synced": None}))

        # Add
        result = subprocess.run(
            [sys.executable, "-m", "cre.cli", "kb", "add", "test_pattern", "test context", "--category", "workflow"],
            capture_output=True, text=True,
            env={
                **os.environ,
                "PYTHONPATH": os.path.join(os.path.dirname(__file__), '..', 'src'),
                "CRE_KB_PATH": str(kb_file),
            }
        )
        assert result.returncode == 0
        assert "Added" in result.stdout

        # Verify it was added
        with open(kb_file) as f:
            kb = json.load(f)
        assert len(kb["context_patterns"]) == 1
        assert kb["context_patterns"][0]["pattern"] == "test_pattern"

        # Remove
        result = subprocess.run(
            [sys.executable, "-m", "cre.cli", "kb", "remove", "0"],
            capture_output=True, text=True,
            env={
                **os.environ,
                "PYTHONPATH": os.path.join(os.path.dirname(__file__), '..', 'src'),
                "CRE_KB_PATH": str(kb_file),
            }
        )
        assert result.returncode == 0
        assert "Removed" in result.stdout

        with open(kb_file) as f:
            kb = json.load(f)
        assert len(kb["context_patterns"]) == 0


class TestCLIScanRefine:
    """cre scan --refine flag."""

    def test_scan_refine_flag_accepted(self, tmp_path):
        """Verify --refine flag is accepted without error."""
        rules_file = tmp_path / "rules.json"
        rules_file.write_text(json.dumps({
            "enabled": True, "always_block": [], "always_allow": [],
            "needs_llm_review": [], "preferences": [], "suggested_rules": [],
            "learned_rules": [],
        }))
        result = subprocess.run(
            [sys.executable, "-m", "cre.cli", "scan", "--refine"],
            capture_output=True, text=True,
            env={
                **os.environ,
                "PYTHONPATH": os.path.join(os.path.dirname(__file__), '..', 'src'),
                "CRE_RULES_PATH": str(rules_file),
                "CRE_SESSIONS_DIR": str(tmp_path / "sessions"),
                "CRE_LLM_API_KEY": "",
            }
        )
        assert result.returncode == 0
        assert "refinement" in result.stdout.lower() or "Processed" in result.stdout


class TestCLIGate:
    """cre gate (stdin/stdout hook mode)."""

    def test_gate_allow(self, tmp_path):
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
        assert output == {}  # Empty = allow

    def test_gate_deny(self, tmp_path):
        rules = {
            "enabled": True,
            "always_block": [{"pattern": "mkfs", "reason": "Filesystem format"}],
            "always_allow": [], "needs_llm_review": [],
        }
        rules_file = tmp_path / "rules.json"
        rules_file.write_text(json.dumps(rules))

        hook_input = json.dumps({"tool_name": "Bash", "tool_input": {"command": "mkfs /dev/sda"}})

        # Create toggle file so gate is enabled
        enabled_file = tmp_path / "cre_enabled"
        enabled_file.touch()

        result = subprocess.run(
            [sys.executable, "-m", "cre.cli", "gate"],
            input=hook_input, capture_output=True, text=True,
            env={
                **os.environ,
                "PYTHONPATH": os.path.join(os.path.dirname(__file__), '..', 'src'),
                "CRE_RULES_PATH": str(rules_file),
                "CRE_ENABLED_PATH": str(enabled_file),
            }
        )
        assert result.returncode == 2  # deny
        output = json.loads(result.stdout)
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_gate_non_bash_allows(self, tmp_path):
        rules = {
            "enabled": True, "llm_review_enabled": False,
            "always_block": [], "always_allow": [], "needs_llm_review": [],
        }
        rules_file = tmp_path / "rules.json"
        rules_file.write_text(json.dumps(rules))

        hook_input = json.dumps({"tool_name": "Read", "tool_input": {"file_path": "/tmp/test"}})

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
        assert output == {}
