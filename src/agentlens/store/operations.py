import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import cast

from agentlens.models.agent_definitions import AgentDefinition
from agentlens.models.facts import FactSession
from agentlens.models.identity import SessionKind
from agentlens.models.report_aggregates import (
    AgentRollup,
    MetricTotals,
    PerSpawnAverages,
    TrendStatus,
    WeightedProportion,
)
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

_DEFAULT_MIN_SESSIONS_FOR_TREND = 5

_ADDITIVE_METRIC_COLUMNS: tuple[str, ...] = (
    "n_turns",
    "n_invocations",
    "n_reads",
    "n_edits",
    "n_writes",
    "n_bash",
    "n_distinct_files",
    "n_errors",
    "n_denials",
    "n_repeated_invocations",
    "n_skills_fired",
    "duration_ms",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_creation_tokens",
    "unreadable_line_count",
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

_SELECT_SPAWNS_IN_WINDOW_SQL = f"""
SELECT
    {_SESSION_COLUMN_LIST}
FROM fact_session
WHERE session_kind = ?
  AND started_at >= ?
  AND started_at < ?
  AND (? IS NULL OR agent_type = ?)
ORDER BY started_at, session_id
"""  # noqa: S608

_AGENT_POPULATION_SUM_COLUMN_LIST = ",\n    ".join(
    f"SUM({name}) AS {name}" for name in _ADDITIVE_METRIC_COLUMNS
)

_SELECT_AGENT_POPULATION_SQL = f"""
SELECT
    agent_type,
    COUNT(*) AS n_spawns,
    {_AGENT_POPULATION_SUM_COLUMN_LIST}
FROM fact_session
WHERE session_kind = ?
  AND started_at >= ?
  AND started_at < ?
  AND (? IS NULL OR agent_type = ?)
GROUP BY agent_type
ORDER BY agent_type
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
            connection.execute(_UPSERT_AGENT_DEFINITION_SQL, agent_definition_to_row(definition))
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


def _utc_bound(moment: datetime) -> str:
    """Render a window bound in the same fixed-width UTC form ``started_at`` is stored in.

    Both sides of the SQL range comparison must share one string width, or a
    lexicographic ``<``/``>=`` comparison goes wrong exactly at a window
    boundary; see the note on the ``started_at`` extractor in ``rows.py``.
    """
    return moment.astimezone(UTC).isoformat(timespec="microseconds")


def read_spawns_in_window(
    connection: sqlite3.Connection,
    start: datetime,
    end: datetime,
    agent_type: str | None,
) -> tuple[FactSession, ...]:
    """Return every subagent spawn whose ``started_at`` falls in ``[start, end)``.

    Ordered by ``(started_at, session_id)`` so the result is reproducible
    regardless of physical row order; ties on ``started_at`` break on the
    qualified session key, which is total. Main-session rows never qualify —
    the query is scoped to ``session_kind = 'subagent'`` structurally, not
    filtered out after the fact. ``agent_type`` narrows the result to one
    agent type; ``None`` returns every subagent spawn in the window.
    """
    rows = connection.execute(
        _SELECT_SPAWNS_IN_WINDOW_SQL,
        (SessionKind.SUBAGENT.value, _utc_bound(start), _utc_bound(end), agent_type, agent_type),
    ).fetchall()
    return tuple(row_to_fact_session(row) for row in rows)


def _select_agent_population(
    connection: sqlite3.Connection, *, start: datetime, end: datetime, agent_type: str | None
) -> list[sqlite3.Row]:
    return connection.execute(
        _SELECT_AGENT_POPULATION_SQL,
        (SessionKind.SUBAGENT.value, _utc_bound(start), _utc_bound(end), agent_type, agent_type),
    ).fetchall()


def _row_to_metric_totals(row: sqlite3.Row) -> MetricTotals:
    return MetricTotals(**{name: cast(int, row[name]) for name in _ADDITIVE_METRIC_COLUMNS})


def _divide_totals(totals: MetricTotals, n_spawns: int) -> PerSpawnAverages:
    """Divide each additive metric's total by ``n_spawns``.

    Never called with ``n_spawns == 0``: every ``totals`` here came from a
    ``GROUP BY agent_type`` row, which only exists when at least one spawn
    contributed to it.
    """
    return PerSpawnAverages(
        **{name: getattr(totals, name) / n_spawns for name in _ADDITIVE_METRIC_COLUMNS}
    )


def _subtract_averages(current: PerSpawnAverages, prior: PerSpawnAverages) -> PerSpawnAverages:
    return PerSpawnAverages(
        **{name: getattr(current, name) - getattr(prior, name) for name in _ADDITIVE_METRIC_COLUMNS}
    )


def _cache_read_proportion(row: sqlite3.Row) -> float | None:
    """Compute the cache-read proportion from one window's summed totals.

    ``cache_read_tokens / (cache_read_tokens + cache_creation_tokens +
    input_tokens)``, where ``input_tokens`` is the uncached input. Computed
    from summed totals rather than averaged per-spawn percentages, so one
    large run is not weighted the same as a tiny one. ``None`` when the
    denominator is zero — an unmeasurable proportion, never ``0.0``.
    """
    cache_read = cast(int, row["cache_read_tokens"])
    cache_creation = cast(int, row["cache_creation_tokens"])
    input_tokens = cast(int, row["input_tokens"])
    denominator = cache_read + cache_creation + input_tokens
    if denominator == 0:
        return None
    return cache_read / denominator


def _build_agent_rollup(
    current_row: sqlite3.Row,
    prior_row: sqlite3.Row | None,
    *,
    min_sessions_for_trend: int,
) -> AgentRollup:
    n_spawns = cast(int, current_row["n_spawns"])
    n_spawns_prior = cast(int, prior_row["n_spawns"]) if prior_row is not None else 0
    totals = _row_to_metric_totals(current_row)
    averages = _divide_totals(totals, n_spawns)
    prior_averages = (
        _divide_totals(_row_to_metric_totals(prior_row), n_spawns_prior)
        if prior_row is not None
        else None
    )
    trend_status = (
        TrendStatus.COMPARABLE
        if n_spawns >= min_sessions_for_trend and n_spawns_prior >= min_sessions_for_trend
        else TrendStatus.INSUFFICIENT_DATA
    )
    average_deltas = (
        _subtract_averages(averages, prior_averages)
        if trend_status is TrendStatus.COMPARABLE and prior_averages is not None
        else None
    )
    current_proportion = _cache_read_proportion(current_row)
    prior_proportion = _cache_read_proportion(prior_row) if prior_row is not None else None
    proportion_delta = (
        current_proportion - prior_proportion
        if trend_status is TrendStatus.COMPARABLE
        and current_proportion is not None
        and prior_proportion is not None
        else None
    )
    return AgentRollup(
        agent_type=cast(str, current_row["agent_type"]),
        n_spawns=n_spawns,
        n_spawns_prior=n_spawns_prior,
        trend_status=trend_status,
        totals=totals,
        averages=averages,
        prior_averages=prior_averages,
        average_deltas=average_deltas,
        cache_read_proportion=WeightedProportion(
            current=current_proportion, prior=prior_proportion, delta=proportion_delta
        ),
    )


def read_agent_rollups(
    connection: sqlite3.Connection,
    current_start: datetime,
    current_end: datetime,
    prior_start: datetime,
    prior_end: datetime,
    agent_type: str | None,
    *,
    min_sessions_for_trend: int = _DEFAULT_MIN_SESSIONS_FOR_TREND,
) -> tuple[AgentRollup, ...]:
    """Build one rollup per agent type present in the current window.

    Population, totals, per-spawn averages, and the weighted cache-read
    proportion are all computed from ``fact_session`` alone — verdict data is
    never joined. An agent type with zero current-window spawns never gets a
    rollup, even when it has prior-window spawns: a rollup's existence is
    entirely current-window scoped, and this is checked first as a short
    circuit so an empty current window costs a second query.

    Rollups are ordered by ``agent_type``. Each rollup's ``trend_status`` is
    ``TrendStatus.COMPARABLE`` only when both its current and prior spawn
    counts meet ``min_sessions_for_trend``; otherwise every per-spawn average
    and the cache-read proportion still report their current (and, when
    available, prior) values, but never a signed delta.
    """
    current_rows = _select_agent_population(
        connection, start=current_start, end=current_end, agent_type=agent_type
    )
    if not current_rows:
        return ()
    prior_rows = _select_agent_population(
        connection, start=prior_start, end=prior_end, agent_type=agent_type
    )
    prior_by_agent_type = {cast(str, row["agent_type"]): row for row in prior_rows}
    return tuple(
        _build_agent_rollup(
            current_row,
            prior_by_agent_type.get(cast(str, current_row["agent_type"])),
            min_sessions_for_trend=min_sessions_for_trend,
        )
        for current_row in current_rows
    )
