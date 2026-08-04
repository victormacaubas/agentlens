"""Store queries and aggregate rollups for the `report` command —
`build_report` reads `fact_session` only and never ingests. Aggregates by
`agent_type` (spawns, not parent sessions), computes a prior-window delta,
applies the low-volume guard, and rolls spawns up per `parent_session_id`
(the intra-session parent lens).
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, replace
from statistics import mean
from typing import Any, Final

from agentlens.reporting.date_window import WindowRange

DEFAULT_MIN_SESSIONS_FOR_TREND: Final[int] = 5

_DELTA_FIELDS: Final[tuple[str, ...]] = (
    "n_spawns",
    "n_failures",
    "n_denial_spawns",
    "n_errors",
    "n_duplicate_tool_calls",
    "n_tool_calls",
    "total_tokens",
    "avg_duration_sec",
)


@dataclass(frozen=True)
class AgentAggregate:
    """Window rollup for one `agent_type`, counted in spawns.

    `avg_verdict_score` is `None` when no session in this aggregate's window
    has a judge verdict yet (verdict inclusion is opportunistic per the
    windowed-reporting spec) — it is populated in `build_report`, not by the
    deterministic-only `_query_agent_aggregates` query.
    """

    agent_type: str
    n_spawns: int
    n_failures: int
    n_denial_spawns: int
    n_errors: int
    n_duplicate_tool_calls: int
    n_tool_calls: int
    total_tokens: int
    avg_duration_sec: float
    avg_verdict_score: float | None = None


@dataclass(frozen=True)
class ParentLensRow:
    """Intra-session rollup: one parent session's fan-out and health."""

    parent_session_id: str
    n_spawns: int
    n_failures: int
    n_denial_spawns: int


@dataclass(frozen=True)
class AgentWindowResult:
    """One agent_type's current-window aggregate plus its trend, guarded
    by `min_sessions_for_trend`, the low-volume guard."""

    aggregate: AgentAggregate
    prior: AgentAggregate | None
    insufficient_data: bool
    delta: dict[str, float] | None


@dataclass(frozen=True)
class ReportResult:
    window: WindowRange
    agent_type_filter: str | None
    min_sessions_for_trend: int
    agents: list[AgentWindowResult]
    parent_lens: list[ParentLensRow]
    # session_id -> parsed verdict_json, for sessions in `window` with a judge
    # verdict. Opportunistic: sessions without one simply have no entry
    # (windowed-reporting spec).
    verdicts: dict[str, dict[str, Any]]

    def to_verdict_slice(self) -> dict[str, Any]:
        """The verdict JSON — deterministic counts and window rollups, plus
        judge verdicts for sessions that have one (opportunistic; absent
        entirely when no verdicts exist in the window)."""
        return {
            "window": {
                "start": self.window.start.isoformat(),
                "end": self.window.end.isoformat(),
                "days": self.window.n_days,
            },
            "agent_type_filter": self.agent_type_filter,
            "min_sessions_for_trend": self.min_sessions_for_trend,
            "agents": [
                {
                    "agent_type": result.aggregate.agent_type,
                    "n_spawns": result.aggregate.n_spawns,
                    "n_failures": result.aggregate.n_failures,
                    "n_denial_spawns": result.aggregate.n_denial_spawns,
                    "n_errors": result.aggregate.n_errors,
                    "n_duplicate_tool_calls": result.aggregate.n_duplicate_tool_calls,
                    "n_tool_calls": result.aggregate.n_tool_calls,
                    "total_tokens": result.aggregate.total_tokens,
                    "avg_duration_sec": result.aggregate.avg_duration_sec,
                    "insufficient_data": result.insufficient_data,
                    "delta": result.delta,
                    **(
                        {"avg_verdict_score": result.aggregate.avg_verdict_score}
                        if result.aggregate.avg_verdict_score is not None
                        else {}
                    ),
                }
                for result in self.agents
            ],
            "parent_lens": [
                {
                    "parent_session_id": row.parent_session_id,
                    "n_spawns": row.n_spawns,
                    "n_failures": row.n_failures,
                    "n_denial_spawns": row.n_denial_spawns,
                }
                for row in self.parent_lens
            ],
            "verdicts": self.verdicts,
        }


def build_report(
    conn: sqlite3.Connection,
    *,
    window: WindowRange,
    agent_type: str | None = None,
    min_sessions_for_trend: int = DEFAULT_MIN_SESSIONS_FOR_TREND,
) -> ReportResult:
    """Query the store for one window and assemble the report.

    Reads `fact_session` and, opportunistically, `fact_verdict` — never
    ingests. The prior-window delta compares against the immediately
    preceding equal-length span; the low-volume guard suppresses that delta
    when the current window holds fewer than `min_sessions_for_trend`
    spawns for an `agent_type`. Verdict scores (when present) never affect
    the trend delta — only deterministic counts do.
    """
    current = _query_agent_aggregates(conn, window=window, agent_type=agent_type)
    prior = _query_agent_aggregates(conn, window=window.prior(), agent_type=agent_type)
    parent_lens = _query_parent_lens(conn, window=window, agent_type=agent_type)
    verdicts, scores_by_agent_type = _query_verdicts(conn, window=window, agent_type=agent_type)

    agents: list[AgentWindowResult] = []
    for name in sorted(current):
        aggregate = current[name]
        agent_scores = scores_by_agent_type.get(name)
        if agent_scores:
            aggregate = replace(aggregate, avg_verdict_score=mean(agent_scores))
        prior_aggregate = prior.get(name)
        insufficient_data = aggregate.n_spawns < min_sessions_for_trend
        delta: dict[str, float] | None = None
        if not insufficient_data and prior_aggregate is not None:
            delta = {
                field: getattr(aggregate, field) - getattr(prior_aggregate, field)
                for field in _DELTA_FIELDS
            }
        agents.append(
            AgentWindowResult(
                aggregate=aggregate,
                prior=prior_aggregate,
                insufficient_data=insufficient_data,
                delta=delta,
            )
        )

    return ReportResult(
        window=window,
        agent_type_filter=agent_type,
        min_sessions_for_trend=min_sessions_for_trend,
        agents=agents,
        parent_lens=parent_lens,
        verdicts=verdicts,
    )


def _query_agent_aggregates(
    conn: sqlite3.Connection,
    *,
    window: WindowRange,
    agent_type: str | None,
) -> dict[str, AgentAggregate]:
    sql = """
        SELECT
            agent_type,
            COUNT(*) AS n_spawns,
            SUM(CASE WHEN n_errors > 0 OR final_report_flagged_partial = 1
                     THEN 1 ELSE 0 END) AS n_failures,
            SUM(CASE WHEN n_permission_denials > 0 THEN 1 ELSE 0 END) AS n_denial_spawns,
            COALESCE(SUM(n_errors), 0) AS n_errors,
            COALESCE(SUM(n_duplicate_tool_calls), 0) AS n_duplicate_tool_calls,
            COALESCE(SUM(n_tool_calls), 0) AS n_tool_calls,
            COALESCE(SUM(
                COALESCE(input_tokens, 0) + COALESCE(output_tokens, 0)
                + COALESCE(cache_read_tokens, 0) + COALESCE(cache_creation_tokens, 0)
            ), 0) AS total_tokens,
            COALESCE(AVG(duration_sec), 0.0) AS avg_duration_sec
        FROM fact_session
        WHERE session_kind = 'subagent'
          AND agent_type IS NOT NULL
          AND session_date >= ?
          AND session_date < ?
    """
    params: list[str] = [window.start.isoformat(), window.end.isoformat()]
    if agent_type is not None:
        sql += " AND agent_type = ?"
        params.append(agent_type)
    sql += " GROUP BY agent_type"

    rows = conn.execute(sql, params).fetchall()
    return {
        str(row[0]): AgentAggregate(
            agent_type=str(row[0]),
            n_spawns=int(row[1]),
            n_failures=int(row[2]),
            n_denial_spawns=int(row[3]),
            n_errors=int(row[4]),
            n_duplicate_tool_calls=int(row[5]),
            n_tool_calls=int(row[6]),
            total_tokens=int(row[7]),
            avg_duration_sec=float(row[8]),
        )
        for row in rows
    }


def _query_parent_lens(
    conn: sqlite3.Connection,
    *,
    window: WindowRange,
    agent_type: str | None,
) -> list[ParentLensRow]:
    sql = """
        SELECT
            parent_session_id,
            COUNT(*) AS n_spawns,
            SUM(CASE WHEN n_errors > 0 OR final_report_flagged_partial = 1
                     THEN 1 ELSE 0 END) AS n_failures,
            SUM(CASE WHEN n_permission_denials > 0 THEN 1 ELSE 0 END) AS n_denial_spawns
        FROM fact_session
        WHERE session_kind = 'subagent'
          AND parent_session_id IS NOT NULL
          AND session_date >= ?
          AND session_date < ?
    """
    params: list[str] = [window.start.isoformat(), window.end.isoformat()]
    if agent_type is not None:
        sql += " AND agent_type = ?"
        params.append(agent_type)
    sql += " GROUP BY parent_session_id ORDER BY parent_session_id"

    rows = conn.execute(sql, params).fetchall()
    return [
        ParentLensRow(
            parent_session_id=str(row[0]),
            n_spawns=int(row[1]),
            n_failures=int(row[2]),
            n_denial_spawns=int(row[3]),
        )
        for row in rows
    ]


def _query_verdicts(
    conn: sqlite3.Connection,
    *,
    window: WindowRange,
    agent_type: str | None,
) -> tuple[dict[str, dict[str, Any]], dict[str, list[float]]]:
    """LEFT JOIN `fact_verdict` onto subagent sessions in `window`.

    Returns the per-session verdict payload (parsed `verdict_json`, keyed
    by `session_id`) plus each `agent_type`'s list of `overall_score`s, used
    by `build_report` to compute the average score shown in the terminal
    summary. Verdict inclusion is opportunistic (windowed-reporting spec):
    a session with no verdict row simply contributes nothing to either
    return value. If a session has verdicts under more than one
    `(rubric_version, judge_model)`, the last one returned by the query
    wins — the store doesn't track a "current" verdict beyond that key.
    """
    sql = """
        SELECT fs.session_id, fs.agent_type, fv.verdict_json
        FROM fact_session fs
        LEFT JOIN fact_verdict fv ON fv.session_id = fs.session_id
        WHERE fs.session_kind = 'subagent'
          AND fs.session_date >= ?
          AND fs.session_date < ?
    """
    params: list[str] = [window.start.isoformat(), window.end.isoformat()]
    if agent_type is not None:
        sql += " AND fs.agent_type = ?"
        params.append(agent_type)

    rows = conn.execute(sql, params).fetchall()

    verdicts: dict[str, dict[str, Any]] = {}
    scores_by_agent_type: dict[str, list[float]] = {}
    for session_id, session_agent_type, verdict_json in rows:
        if verdict_json is None:
            continue
        parsed = json.loads(verdict_json)
        verdicts[str(session_id)] = parsed
        if session_agent_type is not None:
            scores_by_agent_type.setdefault(str(session_agent_type), []).append(
                float(parsed["overall_score"])
            )
    return verdicts, scores_by_agent_type
