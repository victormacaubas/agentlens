import sqlite3

from agentlens.models.session_facts import SessionFacts
from agentlens.store.outcomes import UpsertOutcome
from agentlens.store.rows import (
    fact_session_to_row,
    fact_tool_event_to_row,
    row_to_fact_session,
    row_to_fact_tool_event,
)
from agentlens.store.schema import FACT_SESSION_COLUMN_NAMES, FACT_TOOL_EVENT_COLUMN_NAMES

_SESSION_CONFLICT_TARGET = "session_id"

_SESSION_COLUMN_LIST = ", ".join(FACT_SESSION_COLUMN_NAMES)
_SESSION_PLACEHOLDERS = ", ".join(["?"] * len(FACT_SESSION_COLUMN_NAMES))
_SESSION_UPDATE_ASSIGNMENTS = ",\n    ".join(
    f"{name} = excluded.{name}"
    for name in FACT_SESSION_COLUMN_NAMES
    if name != _SESSION_CONFLICT_TARGET
)

_TOOL_EVENT_COLUMN_LIST = ", ".join(FACT_TOOL_EVENT_COLUMN_NAMES)
_TOOL_EVENT_PLACEHOLDERS = ", ".join(["?"] * len(FACT_TOOL_EVENT_COLUMN_NAMES))

_DELETE_TOOL_EVENTS_SQL = "DELETE FROM fact_tool_event WHERE session_id = ?"

_INSERT_TOOL_EVENT_SQL = f"""
INSERT INTO fact_tool_event (
    {_TOOL_EVENT_COLUMN_LIST}
) VALUES ({_TOOL_EVENT_PLACEHOLDERS})
"""  # noqa: S608

_UPSERT_SESSION_SQL = f"""
INSERT INTO fact_session (
    {_SESSION_COLUMN_LIST}
) VALUES ({_SESSION_PLACEHOLDERS})
ON CONFLICT(session_id) DO UPDATE SET
    {_SESSION_UPDATE_ASSIGNMENTS}
WHERE excluded.revision_content_hash != fact_session.revision_content_hash
  AND excluded.revision_mtime_ns >= fact_session.revision_mtime_ns
"""  # noqa: S608

_SELECT_STORED_CONTENT_HASH_SQL = (
    "SELECT revision_content_hash FROM fact_session WHERE session_id = ?"
)

_SELECT_SESSION_SQL = f"""
SELECT
    {_SESSION_COLUMN_LIST}
FROM fact_session
WHERE session_id = ?
"""  # noqa: S608

_SELECT_TOOL_EVENTS_SQL = f"""
SELECT
    {_TOOL_EVENT_COLUMN_LIST}
FROM fact_tool_event
WHERE session_id = ?
ORDER BY ordinal
"""  # noqa: S608


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
        session=row_to_fact_session(session_row),
        tool_events=tuple(row_to_fact_tool_event(row) for row in event_rows),
    )
