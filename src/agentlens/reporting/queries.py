"""Read-only store queries and aggregate rollups for the report command."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from statistics import mean
from typing import Any, Final, cast

from agentlens.judge.rubric import MODEL_ALIASES, RUBRIC_VERSION
from agentlens.reporting.date_window import WindowRange

DEFAULT_MIN_SESSIONS_FOR_TREND: Final[int] = 5

_DELTA_FIELDS: Final[tuple[str, ...]] = (
    "n_spawns",
    "n_spawns_with_errors",
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

    `avg_verdict_score` is `None` when no spawn has a verdict in the selected
    comparable cohort.
    """

    agent_type: str
    n_spawns: int
    n_spawns_with_errors: int
    n_denial_spawns: int
    n_errors: int
    n_duplicate_tool_calls: int
    n_tool_calls: int
    total_tokens: int
    avg_duration_sec: float
    avg_verdict_score: float | None = None


@dataclass(frozen=True)
class VerdictCohort:
    """The comparable modeled-output identity selected for a report."""

    rubric_version: str
    judge_model: str | None
    judge_input_policy: str = "current"


@dataclass(frozen=True)
class ReportSessionRow:
    """One qualified subagent spawn and its selected-cohort verdict, if any."""

    session_id: str
    raw_session_id: str
    source_project: str
    session_kind: str
    agent_id: str | None
    agent_type: str | None
    parent_session_id: str | None
    task_description: str | None
    session_date: str | None
    n_tool_calls: int
    n_errors: int
    n_permission_denials: int
    n_duplicate_tool_calls: int
    final_report_flagged_partial: bool
    total_tokens: int
    duration_sec: float
    verdict: dict[str, Any] | None

    def to_payload(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "raw_session_id": self.raw_session_id,
            "source_project": self.source_project,
            "session_kind": self.session_kind,
            "agent_id": self.agent_id,
            "agent_type": self.agent_type,
            "parent_session_id": self.parent_session_id,
            "task_description": self.task_description,
            "session_date": self.session_date,
            "n_tool_calls": self.n_tool_calls,
            "n_errors": self.n_errors,
            "n_permission_denials": self.n_permission_denials,
            "n_duplicate_tool_calls": self.n_duplicate_tool_calls,
            "final_report_flagged_partial": self.final_report_flagged_partial,
            "total_tokens": self.total_tokens,
            "duration_sec": self.duration_sec,
            "verdict": self.verdict,
        }


@dataclass(frozen=True)
class ParentLensRow:
    """Intra-session rollup: one parent session's fan-out and health."""

    parent_session_id: str
    n_spawns: int
    n_spawns_with_errors: int
    n_denial_spawns: int


@dataclass(frozen=True)
class AgentWindowResult:
    """One agent_type's current-window aggregate plus its trend, guarded
    by `min_sessions_for_trend`, the low-volume guard.
    """

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
    sessions: list[ReportSessionRow]
    verdict_cohort: VerdictCohort

    @property
    def verdicts(self) -> dict[str, dict[str, Any]]:
        """Selected verdicts keyed by qualified session ID."""
        return {row.session_id: row.verdict for row in self.sessions if row.verdict is not None}

    def to_verdict_slice(self) -> dict[str, Any]:
        """Return the deterministic per-spawn JSON slice and selected verdicts."""
        return {
            "window": {
                "start": self.window.start.isoformat(),
                "end": self.window.end.isoformat(),
                "days": self.window.n_days,
            },
            "agent_type_filter": self.agent_type_filter,
            "min_sessions_for_trend": self.min_sessions_for_trend,
            "verdict_cohort": {
                "rubric_version": self.verdict_cohort.rubric_version,
                "judge_model": self.verdict_cohort.judge_model,
                "judge_input_policy": self.verdict_cohort.judge_input_policy,
            },
            "agents": [
                {
                    "agent_type": result.aggregate.agent_type,
                    "n_spawns": result.aggregate.n_spawns,
                    "n_spawns_with_errors": result.aggregate.n_spawns_with_errors,
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
                    "n_spawns_with_errors": row.n_spawns_with_errors,
                    "n_denial_spawns": row.n_denial_spawns,
                }
                for row in self.parent_lens
            ],
            "sessions": [row.to_payload() for row in self.sessions],
            "verdicts": self.verdicts,
        }


def build_report(
    conn: sqlite3.Connection,
    *,
    window: WindowRange,
    agent_type: str | None = None,
    min_sessions_for_trend: int = DEFAULT_MIN_SESSIONS_FOR_TREND,
    rubric_version: str = RUBRIC_VERSION,
    judge_model: str | None = None,
) -> ReportResult:
    """Query the store for one window and assemble the report.

    The report attaches at most one verdict to each spawn. When `judge_model`
    is omitted, one concrete model is resolved only if the current-input
    verdicts in the requested rubric and window identify it unambiguously.

    Raises:
        ValueError: If the rubric/model selection is invalid or ambiguous.
    """
    if not rubric_version.strip():
        raise ValueError("rubric_version must not be empty")
    selected_model = _resolve_judge_model(
        conn,
        window=window,
        agent_type=agent_type,
        rubric_version=rubric_version,
        judge_model=judge_model,
    )
    cohort = VerdictCohort(rubric_version=rubric_version, judge_model=selected_model)
    sessions = _query_report_sessions(
        conn,
        window=window,
        agent_type=agent_type,
        cohort=cohort,
    )
    current = _aggregate_sessions(sessions)
    prior = _query_agent_aggregates(conn, window=window.prior(), agent_type=agent_type)
    parent_lens = _aggregate_parent_lens(sessions)

    agents: list[AgentWindowResult] = []
    for name in sorted(current):
        aggregate = current[name]
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
        sessions=sessions,
        verdict_cohort=cohort,
    )


def _resolve_judge_model(
    conn: sqlite3.Connection,
    *,
    window: WindowRange,
    agent_type: str | None,
    rubric_version: str,
    judge_model: str | None,
) -> str | None:
    if judge_model is not None:
        if not judge_model.strip():
            raise ValueError("report judge model must not be empty")
        if judge_model in MODEL_ALIASES:
            raise ValueError(
                f"report judge model must be concrete, not floating alias {judge_model!r}"
            )
        return judge_model

    sql = """
        SELECT DISTINCT fv.judge_model
        FROM fact_session fs
        JOIN fact_verdict fv
          ON fv.session_id = fs.session_id
         AND fv.judge_input_hash = fs.judge_input_hash
         AND fv.rubric_version = ?
        WHERE fs.session_kind = 'subagent'
          AND fs.session_date >= ?
          AND fs.session_date < ?
    """
    params = [
        rubric_version,
        window.start.isoformat(),
        window.end.isoformat(),
    ]
    if agent_type is not None:
        sql += " AND fs.agent_type = ?"
        params.append(agent_type)
    sql += " ORDER BY fv.judge_model"

    cursor = conn.cursor()
    cursor.row_factory = sqlite3.Row
    models = [str(row["judge_model"]) for row in cursor.execute(sql, params).fetchall()]
    aliases = [model for model in models if model in MODEL_ALIASES]
    if aliases:
        names = ", ".join(aliases)
        raise ValueError(
            "stored report cohort uses a floating model alias; score with a concrete "
            f"model or select a concrete cohort explicitly (aliases: {names})"
        )
    if len(models) > 1:
        names = ", ".join(models)
        raise ValueError(
            "multiple concrete judge models are available for this report; "
            f"pass --judge-model to select one (available: {names})"
        )
    return models[0] if models else None


def _query_report_sessions(
    conn: sqlite3.Connection,
    *,
    window: WindowRange,
    agent_type: str | None,
    cohort: VerdictCohort,
) -> list[ReportSessionRow]:
    sql = """
        SELECT
            fs.session_id,
            fs.raw_session_id,
            fs.source_project,
            fs.session_kind,
            fs.agent_id,
            fs.agent_type,
            fs.parent_session_id,
            fs.task_description,
            fs.session_date,
            COALESCE(fs.n_tool_calls, 0) AS n_tool_calls,
            COALESCE(fs.n_errors, 0) AS n_errors,
            COALESCE(fs.n_permission_denials, 0) AS n_permission_denials,
            COALESCE(fs.n_duplicate_tool_calls, 0) AS n_duplicate_tool_calls,
            fs.final_report_flagged_partial,
            COALESCE(fs.input_tokens, 0)
                + COALESCE(fs.output_tokens, 0)
                + COALESCE(fs.cache_read_tokens, 0)
                + COALESCE(fs.cache_creation_tokens, 0) AS total_tokens,
            COALESCE(fs.duration_sec, 0.0) AS duration_sec,
            fv.verdict_json
        FROM fact_session fs
        LEFT JOIN fact_verdict fv
          ON fv.session_id = fs.session_id
         AND fv.judge_input_hash = fs.judge_input_hash
         AND fv.rubric_version = ?
         AND fv.judge_model = ?
        WHERE fs.session_kind = 'subagent'
          AND fs.session_date >= ?
          AND fs.session_date < ?
    """
    params: list[str | None] = [
        cohort.rubric_version,
        cohort.judge_model,
        window.start.isoformat(),
        window.end.isoformat(),
    ]
    if agent_type is not None:
        sql += " AND fs.agent_type = ?"
        params.append(agent_type)
    sql += " ORDER BY fs.session_id"

    cursor = conn.cursor()
    cursor.row_factory = sqlite3.Row
    rows = cursor.execute(sql, params).fetchall()
    return [
        ReportSessionRow(
            session_id=str(row["session_id"]),
            raw_session_id=str(row["raw_session_id"]),
            source_project=str(row["source_project"]),
            session_kind=str(row["session_kind"]),
            agent_id=str(row["agent_id"]) if row["agent_id"] is not None else None,
            agent_type=str(row["agent_type"]) if row["agent_type"] is not None else None,
            parent_session_id=(
                str(row["parent_session_id"]) if row["parent_session_id"] is not None else None
            ),
            task_description=(
                str(row["task_description"]) if row["task_description"] is not None else None
            ),
            session_date=str(row["session_date"]) if row["session_date"] is not None else None,
            n_tool_calls=int(row["n_tool_calls"]),
            n_errors=int(row["n_errors"]),
            n_permission_denials=int(row["n_permission_denials"]),
            n_duplicate_tool_calls=int(row["n_duplicate_tool_calls"]),
            final_report_flagged_partial=bool(row["final_report_flagged_partial"]),
            total_tokens=int(row["total_tokens"]),
            duration_sec=float(row["duration_sec"]),
            verdict=_parse_verdict(row["verdict_json"]),
        )
        for row in rows
    ]


def _parse_verdict(value: object) -> dict[str, Any] | None:
    if value is None:
        return None
    parsed: object = json.loads(str(value))
    if not isinstance(parsed, dict):
        raise ValueError("stored verdict_json must be a JSON object")
    return cast(dict[str, Any], parsed)


def _aggregate_sessions(sessions: list[ReportSessionRow]) -> dict[str, AgentAggregate]:
    grouped: dict[str, list[ReportSessionRow]] = {}
    for row in sessions:
        grouped.setdefault(row.agent_type or "unknown", []).append(row)

    aggregates: dict[str, AgentAggregate] = {}
    for agent_type, rows in grouped.items():
        scores = [
            float(row.verdict["overall_score"])
            for row in rows
            if row.verdict is not None
        ]
        aggregates[agent_type] = AgentAggregate(
            agent_type=agent_type,
            n_spawns=len(rows),
            n_spawns_with_errors=sum(
                row.n_errors > 0 or row.final_report_flagged_partial for row in rows
            ),
            n_denial_spawns=sum(row.n_permission_denials > 0 for row in rows),
            n_errors=sum(row.n_errors for row in rows),
            n_duplicate_tool_calls=sum(row.n_duplicate_tool_calls for row in rows),
            n_tool_calls=sum(row.n_tool_calls for row in rows),
            total_tokens=sum(row.total_tokens for row in rows),
            avg_duration_sec=mean(row.duration_sec for row in rows),
            avg_verdict_score=mean(scores) if scores else None,
        )
    return aggregates


def _aggregate_parent_lens(sessions: list[ReportSessionRow]) -> list[ParentLensRow]:
    grouped: dict[str, list[ReportSessionRow]] = {}
    for row in sessions:
        if row.parent_session_id is not None:
            grouped.setdefault(row.parent_session_id, []).append(row)
    return [
        ParentLensRow(
            parent_session_id=parent_session_id,
            n_spawns=len(rows),
            n_spawns_with_errors=sum(
                row.n_errors > 0 or row.final_report_flagged_partial for row in rows
            ),
            n_denial_spawns=sum(row.n_permission_denials > 0 for row in rows),
        )
        for parent_session_id, rows in sorted(grouped.items())
    ]


def _query_agent_aggregates(
    conn: sqlite3.Connection,
    *,
    window: WindowRange,
    agent_type: str | None,
) -> dict[str, AgentAggregate]:
    sql = """
        SELECT
            COALESCE(agent_type, 'unknown') AS agent_type,
            COUNT(*) AS n_spawns,
            SUM(CASE WHEN n_errors > 0 OR final_report_flagged_partial = 1
                     THEN 1 ELSE 0 END) AS n_spawns_with_errors,
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
          AND session_date >= ?
          AND session_date < ?
    """
    params: list[str] = [window.start.isoformat(), window.end.isoformat()]
    if agent_type is not None:
        sql += " AND agent_type = ?"
        params.append(agent_type)
    sql += " GROUP BY COALESCE(agent_type, 'unknown')"

    cursor = conn.cursor()
    cursor.row_factory = sqlite3.Row
    rows = cursor.execute(sql, params).fetchall()
    return {
        str(row["agent_type"]): AgentAggregate(
            agent_type=str(row["agent_type"]),
            n_spawns=int(row["n_spawns"]),
            n_spawns_with_errors=int(row["n_spawns_with_errors"]),
            n_denial_spawns=int(row["n_denial_spawns"]),
            n_errors=int(row["n_errors"]),
            n_duplicate_tool_calls=int(row["n_duplicate_tool_calls"]),
            n_tool_calls=int(row["n_tool_calls"]),
            total_tokens=int(row["total_tokens"]),
            avg_duration_sec=float(row["avg_duration_sec"]),
        )
        for row in rows
    }
