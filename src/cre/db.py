"""
SQLite Database Module — Unified backend for CRE v1.0.

All events, sessions, advice, and overrides go through this single database.
DB path: ~/.cre/cre.db (user-level, not project-level)
"""

import os
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

# Database path (user-level, shared across projects)
DB_PATH = os.environ.get(
    "CRE_DB_PATH",
    str(Path.home() / ".cre" / "cre.db")
)

# Thread-local storage for connections
_local = threading.local()


def _get_connection() -> sqlite3.Connection:
    """Get or create a thread-local database connection."""
    if not hasattr(_local, 'connection') or _local.connection is None:
        # Ensure parent directory exists
        db_dir = os.path.dirname(DB_PATH)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        
        # Create connection
        _local.connection = sqlite3.connect(
            DB_PATH,
            timeout=30.0,
            check_same_thread=False
        )
        
        # Enable WAL mode for concurrent read/write support
        _local.connection.execute("PRAGMA journal_mode=WAL")
        
        # Enable foreign keys
        _local.connection.execute("PRAGMA foreign_keys=ON")
        
        # Use Row factory for dict-like access
        _local.connection.row_factory = sqlite3.Row
    
    return _local.connection


def close_db():
    """Close the thread-local database connection."""
    if hasattr(_local, 'connection') and _local.connection is not None:
        try:
            _local.connection.close()
        except Exception:
            pass
        _local.connection = None


def init_db():
    """Initialize database schema. Creates tables if not exist."""
    conn = _get_connection()
    cursor = conn.cursor()
    
    # Sessions table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            tool_type TEXT,
            project TEXT,
            first_seen DATETIME,
            last_active DATETIME,
            status TEXT
        )
    """)
    
    # Events table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            command TEXT,
            decision TEXT,
            reason TEXT,
            layer TEXT,
            tool_name TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Advice table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS advice (
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
        )
    """)
    
    # Overrides table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS overrides (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            command TEXT,
            pin_valid BOOLEAN,
            context_returned TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Create indexes for common queries
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_events_session_id 
        ON events(session_id)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_events_timestamp 
        ON events(timestamp)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_sessions_status 
        ON sessions(status)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_advice_pattern 
        ON advice(pattern)
    """)
    
    conn.commit()


def log_event(
    session_id: str,
    command: str,
    decision: str,
    reason: str,
    layer: str,
    tool_name: str = "Bash"
) -> int:
    """Log an enforcement event to the events table.
    
    Args:
        session_id: Session identifier
        command: The command or file path being evaluated
        decision: allow, deny, review, ask
        reason: Explanation for the decision
        layer: L1, L2a, L2b, PIN
        tool_name: Bash, Write, Edit, cre_run, etc.
    
    Returns:
        The row ID of the inserted event
    """
    conn = _get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        INSERT INTO events (session_id, command, decision, reason, layer, tool_name, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (session_id, command, decision, reason, layer, tool_name, datetime.now().isoformat()))
    
    conn.commit()
    return cursor.lastrowid


def log_session(
    session_id: str,
    tool_type: str = "claude-code",
    project: Optional[str] = None,
    status: str = "active"
) -> None:
    """Register or update a session.
    
    Creates a new session record if not exists, or updates last_active timestamp.
    
    Args:
        session_id: Unique session identifier
        tool_type: claude-code, amp, cursor, mcp
        project: Project name or path
        status: active, stale, disconnected
    """
    conn = _get_connection()
    cursor = conn.cursor()
    
    now = datetime.now().isoformat()
    
    # Check if session exists
    cursor.execute("""
        SELECT id FROM sessions WHERE id = ?
    """, (session_id,))
    
    if cursor.fetchone():
        # Update existing session
        cursor.execute("""
            UPDATE sessions 
            SET last_active = ?, status = ?
            WHERE id = ?
        """, (now, status, session_id))
    else:
        # Create new session
        cursor.execute("""
            INSERT INTO sessions (id, tool_type, project, first_seen, last_active, status)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (session_id, tool_type, project, now, now, status))
    
    conn.commit()


def log_advice(
    pattern: str,
    outcome: str,
    command: str = "",
    reason: str = ""
) -> None:
    """Log an advice pattern outcome (proceed or stop).
    
    Creates or updates the advice record for a pattern.
    
    Args:
        pattern: The command pattern
        outcome: "proceed" or "stop"
        command: The actual command evaluated
        reason: The reason for the decision
    """
    conn = _get_connection()
    cursor = conn.cursor()
    
    now = datetime.now().isoformat()
    
    # Check if pattern exists
    cursor.execute("""
        SELECT id, proceed_count, stop_count FROM advice WHERE pattern = ?
    """, (pattern,))
    
    row = cursor.fetchone()
    
    if row:
        # Update existing pattern
        if outcome == "proceed":
            cursor.execute("""
                UPDATE advice 
                SET proceed_count = proceed_count + 1,
                    last_command = ?,
                    last_reason = ?,
                    updated = ?
                WHERE pattern = ?
            """, (command, reason, now, pattern))
        else:  # stop
            cursor.execute("""
                UPDATE advice 
                SET stop_count = stop_count + 1,
                    last_command = ?,
                    last_reason = ?,
                    updated = ?
                WHERE pattern = ?
            """, (command, reason, now, pattern))
    else:
        # Create new pattern
        proceed_count = 1 if outcome == "proceed" else 0
        stop_count = 1 if outcome == "stop" else 0
        
        cursor.execute("""
            INSERT INTO advice (pattern, proceed_count, stop_count, last_command, last_reason, created, updated)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (pattern, proceed_count, stop_count, command, reason, now, now))
    
    conn.commit()


def log_override(
    session_id: str,
    command: str,
    pin_valid: bool,
    context_returned: str = ""
) -> int:
    """Log a PIN override event.
    
    Args:
        session_id: Session identifier
        command: The command that was overridden
        pin_valid: Whether the PIN was valid
        context_returned: Context or reason returned
    
    Returns:
        The row ID of the inserted override
    """
    conn = _get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        INSERT INTO overrides (session_id, command, pin_valid, context_returned, timestamp)
        VALUES (?, ?, ?, ?, ?)
    """, (session_id, command, pin_valid, context_returned, datetime.now().isoformat()))
    
    conn.commit()
    return cursor.lastrowid


def get_sessions(
    status: Optional[str] = None,
    limit: int = 100
) -> List[Dict[str, Any]]:
    """Get sessions from the database.
    
    Args:
        status: Filter by status (active, stale, disconnected) or None for all
        limit: Maximum number of sessions to return
    
    Returns:
        List of session dicts
    """
    conn = _get_connection()
    cursor = conn.cursor()
    
    if status:
        cursor.execute("""
            SELECT * FROM sessions 
            WHERE status = ?
            ORDER BY last_active DESC 
            LIMIT ?
        """, (status, limit))
    else:
        cursor.execute("""
            SELECT * FROM sessions 
            ORDER BY last_active DESC 
            LIMIT ?
        """, (limit,))
    
    rows = cursor.fetchall()
    return [dict(row) for row in rows]


def get_events(
    session_id: Optional[str] = None,
    limit: int = 100,
    offset: int = 0
) -> List[Dict[str, Any]]:
    """Get events from the database.
    
    Args:
        session_id: Filter by session or None for all
        limit: Maximum number of events to return
        offset: Number of events to skip
    
    Returns:
        List of event dicts
    """
    conn = _get_connection()
    cursor = conn.cursor()
    
    if session_id:
        cursor.execute("""
            SELECT * FROM events 
            WHERE session_id = ?
            ORDER BY timestamp DESC 
            LIMIT ? OFFSET ?
        """, (session_id, limit, offset))
    else:
        cursor.execute("""
            SELECT * FROM events 
            ORDER BY timestamp DESC 
            LIMIT ? OFFSET ?
        """, (limit, offset))
    
    rows = cursor.fetchall()
    return [dict(row) for row in rows]


def get_advice(
    suggested_only: bool = False,
    limit: int = 100
) -> List[Dict[str, Any]]:
    """Get advice patterns from the database.
    
    Args:
        suggested_only: Only return patterns marked as suggested
        limit: Maximum number of patterns to return
    
    Returns:
        List of advice dicts
    """
    conn = _get_connection()
    cursor = conn.cursor()
    
    if suggested_only:
        cursor.execute("""
            SELECT * FROM advice 
            WHERE suggested = 1
            ORDER BY updated DESC 
            LIMIT ?
        """, (limit,))
    else:
        cursor.execute("""
            SELECT * FROM advice 
            ORDER BY updated DESC 
            LIMIT ?
        """, (limit,))
    
    rows = cursor.fetchall()
    return [dict(row) for row in rows]


def mark_advice_suggested(pattern: str) -> None:
    """Mark an advice pattern as suggested for promotion.
    
    Args:
        pattern: The pattern to mark
    """
    conn = _get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        UPDATE advice 
        SET suggested = 1, updated = ?
        WHERE pattern = ?
    """, (datetime.now().isoformat(), pattern))
    
    conn.commit()


def mark_advice_promoted(pattern: str) -> None:
    """Mark an advice pattern as promoted to a rule.
    
    Args:
        pattern: The pattern to mark
    """
    conn = _get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        UPDATE advice 
        SET promoted = 1, suggested = 0, updated = ?
        WHERE pattern = ?
    """, (datetime.now().isoformat(), pattern))
    
    conn.commit()


def get_event_counts(session_id: Optional[str] = None) -> Dict[str, int]:
    """Get counts of events by decision type.
    
    Args:
        session_id: Filter by session or None for all
    
    Returns:
        Dict with counts for allow, deny, review, ask
    """
    conn = _get_connection()
    cursor = conn.cursor()
    
    if session_id:
        cursor.execute("""
            SELECT decision, COUNT(*) as count
            FROM events
            WHERE session_id = ?
            GROUP BY decision
        """, (session_id,))
    else:
        cursor.execute("""
            SELECT decision, COUNT(*) as count
            FROM events
            GROUP BY decision
        """)
    
    rows = cursor.fetchall()
    counts = {"allow": 0, "deny": 0, "review": 0, "ask": 0}
    for row in rows:
        decision = row["decision"].lower()
        if decision in counts:
            counts[decision] = row["count"]
    
    return counts


def update_session_status(session_id: str, status: str) -> None:
    """Update a session's status.
    
    Args:
        session_id: Session identifier
        status: New status (active, stale, disconnected)
    """
    conn = _get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        UPDATE sessions 
        SET status = ?, last_active = ?
        WHERE id = ?
    """, (status, datetime.now().isoformat(), session_id))
    
    conn.commit()


# Initialize database on module import
try:
    init_db()
except Exception as e:
    # Log error but don't crash - allow manual init later
    import sys
    print(f"Warning: Failed to initialize CRE database: {e}", file=sys.stderr)
