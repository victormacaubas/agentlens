"""SQL over the deterministic session grain: ``fact_session``, ``fact_tool_event``,
and ``bridge_session_skill``.

Every write here honors the staleness rule: a malformed, older, or identical
snapshot never replaces a sound stored one. The savepoint machinery is what
lets one session in a batch be skipped without discarding its siblings' writes.
"""

import sqlite3
from collections.abc import Sequence

from agentlens.models.agent_definitions import AgentDefinition
from agentlens.models.session_facts import SessionFacts
from agentlens.models.skill_signals import SessionSkillSignal
from agentlens.store.agent_definitions import catalog_definition
from agentlens.store.outcomes import UpsertOutcome
from agentlens.store.rows import (
    fact_session_to_row,
    fact_tool_event_to_row,
    row_to_fact_session,
    row_to_fact_tool_event,
    row_to_session_skill_signal,
    session_skill_signal_to_row,
)
from agentlens.store.schema import (
    BRIDGE_SESSION_SKILL_COLUMN_NAMES,
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

_SESSION_SAVEPOINT_NAME = "session_upsert"


class _StalenessRefusalError(Exception):
    """Raised internally to unwind one session's savepoint; never escapes this module."""


def upsert_session(connection: sqlite3.Connection, facts: SessionFacts) -> UpsertOutcome:
    """Replace a session's stored rows with ``facts``, honoring the staleness rule.

    Deletes the session's existing tool-invocation and skill-bridge rows,
    inserts the new ones, then upserts the session row, all as one
    transaction. When the incoming snapshot is not sound to write over what is
    stored, the entire transaction rolls back, including the delete and
    reinsert of the tool-invocation and skill-bridge rows.
    """
    with connection:
        connection.execute("BEGIN")
        return _apply_session(connection, facts)


def upsert_batch(
    connection: sqlite3.Connection,
    *,
    definitions: Sequence[AgentDefinition],
    facts: Sequence[SessionFacts],
) -> tuple[UpsertOutcome, ...]:
    """Apply every definition and session as one all-or-nothing transaction.

    Each session's staleness outcome is decided independently, through its
    own savepoint, so one session being skipped or refused as stale never
    discards another session's writes in the same batch. A database error
    anywhere — a stale outcome is not one — rolls back everything in the
    batch, leaving the store exactly as it was before this call.
    """
    with connection:
        connection.execute("BEGIN")
        for definition in definitions:
            catalog_definition(connection, definition)
        return tuple(_apply_session(connection, one) for one in facts)


def _apply_session(connection: sqlite3.Connection, facts: SessionFacts) -> UpsertOutcome:
    """Write one session's rows under its own savepoint, honoring the staleness rule.

    A staleness refusal rolls back only this savepoint; a real database error
    propagates uncaught, so a caller running several of these under one outer
    transaction can let that error abort the whole transaction.

    Assumes the caller already opened an explicit transaction. Releasing a
    savepoint that turns out to be the outermost one commits immediately,
    the same as a bare ``BEGIN``/``COMMIT`` pair, which would silently defeat
    the batch's all-or-nothing guarantee — the explicit ``BEGIN`` every caller
    issues first is what keeps this savepoint nested instead.
    """
    session_id = facts.session.identity.session_id
    connection.execute(f"SAVEPOINT {_SESSION_SAVEPOINT_NAME}")
    try:
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
        connection.execute(f"ROLLBACK TO SAVEPOINT {_SESSION_SAVEPOINT_NAME}")
        connection.execute(f"RELEASE SAVEPOINT {_SESSION_SAVEPOINT_NAME}")
        stored_fingerprint_row = connection.execute(
            _SELECT_STORED_DERIVATION_FINGERPRINT_SQL, (session_id,)
        ).fetchone()
        stored_fingerprint = (
            stored_fingerprint_row[0] if stored_fingerprint_row is not None else None
        )
        if stored_fingerprint == facts.session.derivation_fingerprint:
            return UpsertOutcome.SKIPPED_IDENTICAL
        return UpsertOutcome.REFUSED_STALE
    connection.execute(f"RELEASE SAVEPOINT {_SESSION_SAVEPOINT_NAME}")
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


def read_skill_signals_for_sessions(
    connection: sqlite3.Connection, session_ids: Sequence[str]
) -> dict[str, tuple[SessionSkillSignal, ...]]:
    """Return skill-bridge rows for every id in ``session_ids``, grouped by session id.

    One parameterized query covers the whole sequence, regardless of how
    many ids are requested, so a report window's spawns never trigger one
    skill-bridge query per spawn. Returns an empty mapping without issuing
    any query when ``session_ids`` is empty. A session id with no
    skill-bridge rows is simply absent from the result rather than present
    with an empty tuple; a caller reading a report's spawns should treat a
    missing key the same as one mapped to ``()``.
    """
    if not session_ids:
        return {}
    placeholders = ", ".join(["?"] * len(session_ids))
    sql = f"""
    SELECT
        {_SKILL_SIGNAL_COLUMN_LIST}
    FROM bridge_session_skill
    WHERE session_id IN ({placeholders})
    ORDER BY session_id, skill_name
    """  # noqa: S608
    rows = connection.execute(sql, tuple(session_ids)).fetchall()
    grouped: dict[str, list[SessionSkillSignal]] = {}
    for row in rows:
        signal = row_to_session_skill_signal(row)
        grouped.setdefault(signal.session_id, []).append(signal)
    return {session_id: tuple(signals) for session_id, signals in grouped.items()}
