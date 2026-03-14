# CRE v1.0 - SQLite + MCP Unified Architecture

## Goal
One SQLite database. Everything writes to it. Dashboard reads from it.
Doesn't matter if the call came from hooks, MCP, or delegate.

## Database Schema

```sql
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    tool_type TEXT,          -- claude-code, amp, cursor, mcp
    project TEXT,
    first_seen DATETIME,
    last_active DATETIME,
    status TEXT              -- active, stale, disconnected
);

CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    command TEXT,
    decision TEXT,           -- allow, deny, review, ask
    reason TEXT,
    layer TEXT,              -- L1, L2a, L2b, PIN
    tool_name TEXT,          -- Bash, Write, Edit, cre_run, etc.
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE advice (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern TEXT UNIQUE,
    proceed_count INTEGER DEFAULT 0,
    stop_count INTEGER DEFAULT 0,
    last_command TEXT,
    last_reason TEXT,
    suggested BOOLEAN DEFAULT 0,
    promoted BOOLEAN DEFAULT 0,
    created DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE overrides (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    command TEXT,
    pin_valid BOOLEAN,
    context_returned TEXT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

## Files to Change

### 1. NEW: src/cre/db.py
- SQLite connection management
- init_db() creates tables if not exist
- log_event(), log_session(), log_advice(), log_override()
- get_sessions(), get_events(), get_advice()
- DB path: ~/.cre/cre.db (user-level, not project-level)

### 2. MODIFY: src/cre/gate.py
- Import db module
- gate_main(): register/update session on every call
- _evaluate_bash(): log event after decision
- _evaluate_write_edit(): log event after decision
- _check_pin_override(): log override
- Remove CRE.md writing (replaced by events table)

### 3. MODIFY: src/cre/mcp_server.py
- Import db module
- cre_check(): log event
- cre_run(): log event
- cre_write(): log event
- cre_override(): log override
- cre_status(): read from db instead of in-memory
- NEW tool: cre_sessions() - list protected sessions

### 4. MODIFY: src/cre/advice_tracker.py
- Switch from JSON file to advice table
- log_advice_outcome(): write to db
- _maybe_create_suggestion(): write to db
- Remove advice_tracker.json dependency

### 5. MODIFY: src/cre/dashboard.py
- Read sessions from db instead of scanning JSONL
- Read events from db for log viewer
- Read advice from db for suggestions
- Show MCP sessions alongside Claude Code sessions
- Session view: which tools are protected, last action, block count

### 6. MODIFY: src/cre/session.py
- Keep conversation reading (still needs JSONL/thread access for L2)
- Add session registration to db on first gate call

### 7. MODIFY: src/cre/config.py
- Add DB_PATH config (default: ~/.cre/cre.db)

## What Stays the Same
- L1 regex engine (regex_check)
- L2 LLM prompts (just tightened)
- PIN override logic
- Rules format (rules.json)
- CLI commands (cre init, cre status, etc.)
- Adapters (claude-code, delegate, generic)
- MCP tool interfaces (same params, same returns)

## Testing
- Existing 896 tests still pass (L1/L2 logic unchanged)
- New tests for db operations
- Dashboard integration test

## Order of Implementation
1. db.py (new module, no dependencies)
2. gate.py (log events to db)
3. advice_tracker.py (switch to db)
4. mcp_server.py (log events, add cre_sessions)
5. dashboard.py (read from db)
6. session.py (register sessions)
7. Tests
