import sqlite3

from agentlens.models.agent_definitions import AgentDefinition
from agentlens.models.session_facts import SessionFacts
from agentlens.store.outcomes import UpsertOutcome
from agentlens.store.rows import (
    agent_definition_to_row,
    fact_session_to_row,
    fact_tool_event_to_row,
    row_to_agent_definition,
    row_to_fact_session,
    row_to_fact_tool_event,
    row_to_session_skill_signal,
    session_skill_signal_to_row,
)
from agentlens.store.schema import (
    BRIDGE_SESSION_SKILL_COLUMN_NAMES,
    DIM_AGENT_COLUMN_NAMES,
    FACT_SESSION_COLUMN_NAMES,
    FACT_TOOL_EVENT_COLUMN_NAMES,
)

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
WHERE excluded.derivation_fingerprint != fact_session.derivation_fingerprint
  AND excluded.derivation_observed_mtime_ns >= fact_session.derivation_observed_mtime_ns
"""  # noqa: S608

_SELECT_STORED_DERIVATION_FINGERPRINT_SQL = (
    "SELECT derivation_fingerprint FROM fact_session WHERE session_id = ?"
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

_SKILL_SIGNAL_COLUMN_LIST = ", ".join(BRIDGE_SESSION_SKILL_COLUMN_NAMES)
_SKILL_SIGNAL_PLACEHOLDERS = ", ".join(["?"] * len(BRIDGE_SESSION_SKILL_COLUMN_NAMES))

_DELETE_SKILL_SIGNALS_SQL = "DELETE FROM bridge_session_skill WHERE session_id = ?"

_INSERT_SKILL_SIGNAL_SQL = f"""
INSERT INTO bridge_session_skill (
    {_SKILL_SIGNAL_COLUMN_LIST}
) VALUES ({_SKILL_SIGNAL_PLACEHOLDERS})
"""  # noqa: S608

_SELECT_SKILL_SIGNALS_SQL = f"""
SELECT
    {_SKILL_SIGNAL_COLUMN_LIST}
FROM bridge_session_skill
WHERE session_id = ?
ORDER BY skill_name
"""  # noqa: S608

_DIM_AGENT_CONFLICT_TARGET = "agent_definition_id"

_DIM_AGENT_COLUMN_LIST = ", ".join(DIM_AGENT_COLUMN_NAMES)
_DIM_AGENT_PLACEHOLDERS = ", ".join(["?"] * len(DIM_AGENT_COLUMN_NAMES))

_UPSERT_AGENT_DEFINITION_SQL = f"""
INSERT INTO dim_agent (
    {_DIM_AGENT_COLUMN_LIST}
) VALUES ({_DIM_AGENT_PLACEHOLDERS})
ON CONFLICT({_DIM_AGENT_CONFLICT_TARGET}) DO NOTHING
"""  # noqa: S608

_SELECT_AGENT_DEFINITION_SQL = f"""
SELECT
    {_DIM_AGENT_COLUMN_LIST}
FROM dim_agent
WHERE agent_definition_id = ?
"""  # noqa: S608


class _StalenessRefusalError(Exception):
    """Raised internally to unwind the transaction; never escapes this module."""


def upsert_session(connection: sqlite3.Connection, facts: SessionFacts) -> UpsertOutcome:
    """Replace a session's stored rows with ``facts``, honoring the staleness rule.

    Deletes the session's existing tool-invocation and skill-bridge rows,
    inserts the new ones, then upserts the session row, all as one
    transaction. When the incoming snapshot is not sound to write over what is
    stored, the entire transaction rolls back, including the delete and
    reinsert of the tool-invocation and skill-bridge rows.
    """
    session_id = facts.session.identity.session_id
    try:
        with connection:
            connection.execute(_DELETE_TOOL_EVENTS_SQL, (session_id,))
            connection.executemany(
                _INSERT_TOOL_EVENT_SQL,
                [fact_tool_event_to_row(event) for event in facts.tool_events],
            )
            connection.execute(_DELETE_SKILL_SIGNALS_SQL, (session_id,))
            connection.executemany(
                _INSERT_SKILL_SIGNAL_SQL,
                [session_skill_signal_to_row(signal) for signal in facts.skill_signals],
            )
            cursor = connection.execute(_UPSERT_SESSION_SQL, fact_session_to_row(facts.session))
            if cursor.rowcount == 0:
                raise _StalenessRefusalError
    except _StalenessRefusalError:
        stored_fingerprint_row = connection.execute(
            _SELECT_STORED_DERIVATION_FINGERPRINT_SQL, (session_id,)
        ).fetchone()
        stored_fingerprint = (
            stored_fingerprint_row[0] if stored_fingerprint_row is not None else None
        )
        if stored_fingerprint == facts.session.derivation_fingerprint:
            return UpsertOutcome.SKIPPED_IDENTICAL
        return UpsertOutcome.REFUSED_STALE
    return UpsertOutcome.REPLACED


def read_session(connection: sqlite3.Connection, session_id: str) -> SessionFacts | None:
    """Return the stored session with its tool-invocation and skill-bridge rows, or ``None``."""
    session_row = connection.execute(_SELECT_SESSION_SQL, (session_id,)).fetchone()
    if session_row is None:
        return None
    event_rows = connection.execute(_SELECT_TOOL_EVENTS_SQL, (session_id,)).fetchall()
    skill_rows = connection.execute(_SELECT_SKILL_SIGNALS_SQL, (session_id,)).fetchall()
    return SessionFacts(
        session=row_to_fact_session(session_row),
        tool_events=tuple(row_to_fact_tool_event(row) for row in event_rows),
        skill_signals=tuple(row_to_session_skill_signal(row) for row in skill_rows),
    )


def upsert_agent_definition(connection: sqlite3.Connection, definition: AgentDefinition) -> None:
    """Insert ``definition`` into ``dim_agent`` if its identity is not already stored.

    ``agent_definition_id`` is content-addressed, so a conflicting row is
    always identical to ``definition``; a repeat catalog scan is therefore a
    no-op rather than a second, staleness-checked write.
    """
    with connection:
        connection.execute(_UPSERT_AGENT_DEFINITION_SQL, agent_definition_to_row(definition))


def read_agent_definition(
    connection: sqlite3.Connection, agent_definition_id: str
) -> AgentDefinition | None:
    """Return the cataloged definition identified by ``agent_definition_id``, or ``None``."""
    row = connection.execute(_SELECT_AGENT_DEFINITION_SQL, (agent_definition_id,)).fetchone()
    if row is None:
        return None
    return row_to_agent_definition(row)
