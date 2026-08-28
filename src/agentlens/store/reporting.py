"""Analytical SQL over ``fact_session`` for windowed reporting.

Read-only aggregation: spawn populations, additive metric totals, per-spawn
averages, and the weighted cache-read proportion. Verdict data is never joined
here — a report's deterministic figures stay measured, never modeled.
"""

import sqlite3
from datetime import UTC, datetime
from typing import cast

from agentlens.models.facts import FactSession
from agentlens.models.identity import SessionKind
from agentlens.models.report_aggregates import (
    AgentRollup,
    MetricTotals,
    PerSpawnAverages,
    TrendStatus,
    WeightedProportion,
)
from agentlens.models.windows import DEFAULT_MIN_SESSIONS_FOR_TREND
from agentlens.store.rows import row_to_fact_session
from agentlens.store.schema import FACT_SESSION_COLUMN_NAMES

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

_SESSION_COLUMN_LIST = ", ".join(FACT_SESSION_COLUMN_NAMES)

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
    min_sessions_for_trend: int = DEFAULT_MIN_SESSIONS_FOR_TREND,
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
