import sqlite3

from agentlens.models.session_facts import SessionFacts
from agentlens.store.outcomes import UpsertOutcome
from agentlens.store.rows import (
    fact_session_to_row,
    fact_tool_event_to_row,
    row_to_fact_session,
    row_to_fact_tool_event,
)

_DELETE_TOOL_EVENTS_SQL = "DELETE FROM fact_tool_event WHERE session_id = ?"

_INSERT_TOOL_EVENT_SQL = """
INSERT INTO fact_tool_event (
    session_id, ordinal, tool_name, input_fingerprint, file_identity,
    timestamp, is_error, denial_kind, result_size
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_UPSERT_SESSION_SQL = """
INSERT INTO fact_session (
    session_id, source_project, session_kind, raw_session_id,
    revision_mtime_ns, revision_size, revision_content_hash,
    agent_type, name_source, task_description, spawning_tool_use_id, spawn_depth,
    n_turns, n_invocations, n_reads, n_edits, n_writes, n_bash,
    n_distinct_files, n_errors, n_denials, n_repeated_invocations,
    duration_ms, input_tokens, output_tokens, cache_read_tokens,
    cache_creation_tokens, unreadable_line_count
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(session_id) DO UPDATE SET
    source_project = excluded.source_project,
    session_kind = excluded.session_kind,
    raw_session_id = excluded.raw_session_id,
    revision_mtime_ns = excluded.revision_mtime_ns,
    revision_size = excluded.revision_size,
    revision_content_hash = excluded.revision_content_hash,
    agent_type = excluded.agent_type,
    name_source = excluded.name_source,
    task_description = excluded.task_description,
    spawning_tool_use_id = excluded.spawning_tool_use_id,
    spawn_depth = excluded.spawn_depth,
    n_turns = excluded.n_turns,
    n_invocations = excluded.n_invocations,
    n_reads = excluded.n_reads,
    n_edits = excluded.n_edits,
    n_writes = excluded.n_writes,
    n_bash = excluded.n_bash,
    n_distinct_files = excluded.n_distinct_files,
    n_errors = excluded.n_errors,
    n_denials = excluded.n_denials,
    n_repeated_invocations = excluded.n_repeated_invocations,
    duration_ms = excluded.duration_ms,
    input_tokens = excluded.input_tokens,
    output_tokens = excluded.output_tokens,
    cache_read_tokens = excluded.cache_read_tokens,
    cache_creation_tokens = excluded.cache_creation_tokens,
    unreadable_line_count = excluded.unreadable_line_count
WHERE excluded.revision_content_hash != fact_session.revision_content_hash
  AND excluded.revision_mtime_ns >= fact_session.revision_mtime_ns
"""

_SELECT_STORED_CONTENT_HASH_SQL = (
    "SELECT revision_content_hash FROM fact_session WHERE session_id = ?"
)

_SELECT_SESSION_SQL = """
SELECT
    session_id, source_project, session_kind, raw_session_id,
    revision_mtime_ns, revision_size, revision_content_hash,
    agent_type, name_source, task_description, spawning_tool_use_id, spawn_depth,
    n_turns, n_invocations, n_reads, n_edits, n_writes, n_bash,
    n_distinct_files, n_errors, n_denials, n_repeated_invocations,
    duration_ms, input_tokens, output_tokens, cache_read_tokens,
    cache_creation_tokens, unreadable_line_count
FROM fact_session
WHERE session_id = ?
"""

_SELECT_TOOL_EVENTS_SQL = """
SELECT
    session_id, ordinal, tool_name, input_fingerprint, file_identity,
    timestamp, is_error, denial_kind, result_size
FROM fact_tool_event
WHERE session_id = ?
ORDER BY ordinal
"""


class _StalenessRefusalError(Exception):
    """Raised internally to unwind the transaction; never escapes this module."""


def upsert_session(connection: sqlite3.Connection, facts: SessionFacts) -> UpsertOutcome:
    """Replace a session's stored rows with ``facts``, honoring the staleness rule.

    Deletes the session's existing tool-invocation rows, inserts the new ones,
    then upserts the session row, all as one transaction. When the incoming
    snapshot is not sound to write over what is stored, the entire transaction
    rolls back, including the delete and reinsert of the tool-invocation rows.
    """
    session_id = facts.session.identity.session_id
    try:
        with connection:
            connection.execute(_DELETE_TOOL_EVENTS_SQL, (session_id,))
            connection.executemany(
                _INSERT_TOOL_EVENT_SQL,
                [fact_tool_event_to_row(event) for event in facts.tool_events],
            )
            cursor = connection.execute(_UPSERT_SESSION_SQL, fact_session_to_row(facts.session))
            if cursor.rowcount == 0:
                raise _StalenessRefusalError
    except _StalenessRefusalError:
        stored_hash_row = connection.execute(
            _SELECT_STORED_CONTENT_HASH_SQL, (session_id,)
        ).fetchone()
        stored_hash = stored_hash_row[0] if stored_hash_row is not None else None
        if stored_hash == facts.session.revision.content_hash:
            return UpsertOutcome.SKIPPED_IDENTICAL
        return UpsertOutcome.REFUSED_STALE
    return UpsertOutcome.REPLACED


def read_session(connection: sqlite3.Connection, session_id: str) -> SessionFacts | None:
    """Return the stored session and its ordered tool-invocation rows, or ``None``."""
    session_row = connection.execute(_SELECT_SESSION_SQL, (session_id,)).fetchone()
    if session_row is None:
        return None
    event_rows = connection.execute(_SELECT_TOOL_EVENTS_SQL, (session_id,)).fetchall()
    return SessionFacts(
        session=row_to_fact_session(tuple(session_row)),
        tool_events=tuple(row_to_fact_tool_event(tuple(row)) for row in event_rows),
    )
