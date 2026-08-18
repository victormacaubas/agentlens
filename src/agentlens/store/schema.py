"""DDL for the two fact tables the store persists.

``fact_session`` holds one row per spawn, keyed on the qualified session key.
``fact_tool_event`` holds one row per tool invocation, keyed on the session
together with its ordinal position within that session.
"""

import sqlite3

CREATE_FACT_SESSION_SQL = """
CREATE TABLE IF NOT EXISTS fact_session (
    session_id TEXT PRIMARY KEY,
    source_project TEXT NOT NULL,
    session_kind TEXT NOT NULL,
    raw_session_id TEXT NOT NULL,
    revision_mtime_ns INTEGER NOT NULL,
    revision_size INTEGER NOT NULL,
    revision_content_hash TEXT NOT NULL,
    agent_type TEXT NOT NULL,
    name_source TEXT NOT NULL,
    task_description TEXT NOT NULL,
    spawning_tool_use_id TEXT,
    spawn_depth INTEGER NOT NULL,
    n_turns INTEGER NOT NULL,
    n_invocations INTEGER NOT NULL,
    n_reads INTEGER NOT NULL,
    n_edits INTEGER NOT NULL,
    n_writes INTEGER NOT NULL,
    n_bash INTEGER NOT NULL,
    n_distinct_files INTEGER NOT NULL,
    n_errors INTEGER NOT NULL,
    n_denials INTEGER NOT NULL,
    n_repeated_invocations INTEGER NOT NULL,
    duration_ms INTEGER NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cache_read_tokens INTEGER NOT NULL,
    cache_creation_tokens INTEGER NOT NULL,
    unreadable_line_count INTEGER NOT NULL
)
"""

CREATE_FACT_TOOL_EVENT_SQL = """
CREATE TABLE IF NOT EXISTS fact_tool_event (
    session_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    tool_name TEXT NOT NULL,
    input_fingerprint TEXT NOT NULL,
    file_identity TEXT,
    timestamp TEXT NOT NULL,
    is_error INTEGER NOT NULL,
    denial_kind TEXT,
    result_size INTEGER,
    PRIMARY KEY (session_id, ordinal)
)
"""


def ensure_schema(connection: sqlite3.Connection) -> None:
    """Create both fact tables if they do not already exist."""
    connection.execute(CREATE_FACT_SESSION_SQL)
    connection.execute(CREATE_FACT_TOOL_EVENT_SQL)
    connection.commit()
