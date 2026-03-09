# CRE Roadmap

## Shipped

### v0.1.0 — Core Gate
- Two-layer architecture: L1 regex (<10ms) + L2 LLM context review (2-5s)
- Claude Code adapter with PreToolUse hooks (Bash, Write, Edit, WebSearch, Agent)
- Generic adapter for non-Claude tools (JSON stdin/stdout)
- Fail-safe deny on timeout, parse error, missing API key
- Web dashboard with rule editor, log viewer, suggestion review

### v0.2.0 — Learning + Multi-Tool
- Conversation scanner (`cre scan`) detects preferences and patterns
- Rule importer (`cre import`) extracts enforceable rules from instruction files
- Write/Edit intent checking (L2 verifies user asked for file actions)
- Instruction alignment checking (catches tool substitution)
- Pipe-aware L1 scanning (splits compound commands on `|`, `;`, `&&`, `||`)
- 790-scenario test suite covering L1 regex, L2 context, and edge cases
- BSL-1.1 license (use free, ship licensed)

### v0.3.0 — L2 as Rule Factory (in progress)
- CRE.md persistent memory for L2 (rolling 14-day enforcement history)
- Suggestions pipeline: all new rules route through approve/dismiss flow
- L2 pattern promotion: repeated denials auto-suggest L1 rules
- Import routes through suggestions (not direct to rules.json)
- L1 refinement suggestions from override patterns

## Next: Agentic Workflow Integration

### Problem

CRE currently gates the **host AI** (Claude Code, Cursor, etc.) which is conversational and has a human in the loop. Agentic workflows are different:

- Agents (CI pipelines, coding agents, batch processors) run autonomously
- They don't read conversation context, so L2 intent checking is meaningless for them
- They execute dozens of commands per minute, so L2 latency (2-5s per call) would cripple throughput
- But they still need L1 protection against destructive commands

### Design: L1-Only Agent Mode

A configuration flag that tells CRE to skip L2 for agent-originated tool calls while keeping L1 hard rules active.

**How it works:**

```
Agent tool call → CRE Gate
                    │
            ┌───────▼───────┐
            │  Agent Mode?   │
            │  (config flag) │
            └───────┬───────┘
                    │
         ┌──── YES ────┐──── NO ────┐
         │             │             │
    L1 only        Full L1+L2    Full L1+L2
    (regex)        (current)     (current)
         │             │             │
    ALLOW/DENY    ALLOW/DENY    ALLOW/DENY
```

**Configuration (rules.json):**

```json
{
  "agent_mode": {
    "enabled": false,
    "l2_skip": true,
    "agent_indicators": [
      "CLAUDE_AGENT_SDK",
      "quality-pipeline",
      "speed-pipeline",
      "coding-agent"
    ],
    "agent_allow_patterns": [
      "^pytest\\b",
      "^pip install\\b",
      "^npm (install|test|run)\\b"
    ],
    "agent_block_patterns": [
      "git push",
      "ssh\\s",
      "rm -rf /",
      "systemctl.*(restart|stop)"
    ]
  }
}
```

**Detection:** Agent mode activates when:
1. An environment variable matches `agent_indicators` (e.g. `CLAUDE_AGENT_SDK=1`), OR
2. The hook input contains an agent identifier in metadata, OR
3. The calling process matches a known agent pattern

**Agent-specific rule lists:** Agents get their own allow/block patterns separate from the interactive lists. This lets you be more permissive (allow `pytest`, `pip install`) or more restrictive (block all SSH even from agents) depending on the use case.

**What agents keep:**
- All `always_block` rules (fork bombs, disk writes, format commands)
- Agent-specific `agent_block_patterns`
- Agent-specific `agent_allow_patterns`
- L1 fast path (<10ms per call)

**What agents skip:**
- L2 context review (no conversation to review)
- Intent checking (agents don't have user intent)
- Alignment checking (agents use whatever tools they need)
- Learning engine (agent commands shouldn't feed back into user preferences)

### Implementation

| Step | What | Files |
|------|------|-------|
| 1 | Add `agent_mode` config section to rules schema | `gate.py`, `config.py` |
| 2 | Agent detection in `gate_main()` | `gate.py` |
| 3 | L1-only fast path for detected agents | `gate.py` |
| 4 | Agent-specific allow/block pattern lists | `gate.py` |
| 5 | CLI: `cre rules add --agent-block` / `--agent-allow` | `cli.py` |
| 6 | Dashboard: agent mode toggle + agent rule editor | `dashboard.py` |
| 7 | Tests | `tests/` |

### User Configuration

This is a **user config item**, not a core capability change. The gate engine already has everything it needs (L1 regex, rule matching, adapter pattern). The work is:

1. Adding the config schema
2. Adding the detection logic
3. Wiring it into the existing gate flow
4. Exposing it in dashboard/CLI

Users who don't run agents never see this. Users who do can toggle it per-agent.

## Future

### Multi-Tool Adapters
- Cursor adapter (`.cursorrules` hook format)
- Codex adapter (OpenAI Codex hook format)
- VS Code extension adapter
- Generic webhook adapter (HTTP POST instead of stdin)

### Rule Sharing
- Export rules as shareable JSON (strip PII, keep patterns)
- Import community rule packs (e.g. "web dev safety", "infra ops")
- Versioned rule sets with diff/merge

### Audit Trail
- Structured audit log (JSON, queryable)
- Decision replay: re-run historical commands against current rules
- Compliance reporting for teams

### Context Sources
- Read from multiple AI tool session formats (not just Claude Code JSONL)
- Git diff context (what files changed recently)
- Project type detection (auto-suggest rules for web, infra, data projects)
