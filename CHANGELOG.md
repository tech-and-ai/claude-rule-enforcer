# Changelog

## [0.2.0] - 2026-03-01

### Added
- **Instruction alignment check** (L2 enhancement): when user names a specific tool ("use grounded", "run the research agent"), CRE blocks the AI from substituting a different tool
- L1 triage: fast regex scan (<5ms) detects tool instructions in user messages — 95%+ of calls pass instantly
- L2 alignment prompt: LLM review only fires when instruction detected — checks if tool call matches user's named tool
- Hooks for WebSearch, WebFetch, Agent, and ToolSearch tools (third PreToolUse matcher)
- `alignment_check_enabled` toggle in rules.json, dashboard, and `cre status`
- Alignment defaults to ALLOW on failure (advisory, not security)

## [0.1.0] - 2026-03-01

### Added
- Package restructure: `src/cre/` with proper Python packaging
- CLI entry point: `cre` command with subcommands (init, status, dashboard, test, gate, scan, rules, enable, disable)
- Learning engine (`learner.py`): batch scan conversation history, extract preferences, suggest rules with evidence chains
- Multi-tool gate: hooks Bash, Write, and Edit tools (not just Bash)
- Intent detection for Write/Edit: L2 checks if user asked for the file change
- Preferences system: approved suggestions become hard rules injected into L2 prompt
- Suggestions tab in dashboard: approve/dismiss/view evidence
- Preferences tab in dashboard: view and manage learned preferences
- `cre init`: auto-configures hooks in `~/.claude/settings.json`
- `cre scan --hours N`: scan recent conversation history for patterns
- Rule importer (`cre import`): parse CLAUDE.md, agent.md, rules.md — extracts enforceable rules via LLM
- Tool-agnostic adapter system: Claude Code + generic adapters, auto-detect input format
- Claude Code plugin format: `.claude-plugin/plugin.json` + `hooks/hooks.json`
- BSL-1.1 license (source available, commercial license required)

### Changed
- License: BSL-1.1 (source available, commercial license required)
