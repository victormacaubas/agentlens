"""Terminal rendering for the `report` command's output."""

from __future__ import annotations

from agentlens.reporting.queries import ReportResult


def render_terminal_summary(result: ReportResult) -> str:
    """A thin, human-readable terminal summary of `result`."""
    lines = [
        f"window: {result.window.start.isoformat()} to {result.window.end.isoformat()} "
        f"({result.window.n_days}d)"
    ]
    if result.agent_type_filter is not None:
        lines.append(f"agent filter: {result.agent_type_filter}")
    if result.verdict_cohort.judge_model is None:
        lines.append(
            f"verdict cohort: rubric {result.verdict_cohort.rubric_version}, "
            "no concrete model available"
        )
    else:
        lines.append(
            f"verdict cohort: rubric {result.verdict_cohort.rubric_version}, "
            f"model {result.verdict_cohort.judge_model}, current judge input"
        )

    if not result.agents:
        lines.append("no spawns in this window")
    for agent_result in result.agents:
        agg = agent_result.aggregate
        line = f"{agg.agent_type}: {agg.n_spawns} spawns, {agg.n_spawns_with_errors} had errors"
        if agg.avg_verdict_score is not None:
            line += f", avg score: {agg.avg_verdict_score:.1f}/5"
        if agg.n_denial_spawns:
            line += f", {agg.n_denial_spawns} hit denials"
        if agent_result.insufficient_data:
            line += f" (n={agg.n_spawns}, insufficient data for trend)"
        elif agent_result.delta is not None:
            line += f" (Δ spawns vs prior window: {agent_result.delta['n_spawns']:+.0f})"
        lines.append(line)

    for parent_row in result.parent_lens:
        lines.append(
            f"parent {parent_row.parent_session_id}: {parent_row.n_spawns} spawns, "
            f"{parent_row.n_spawns_with_errors} had errors, "
            f"{parent_row.n_denial_spawns} hit denials"
        )

    for session_row in result.sessions:
        score = (
            f"score {float(session_row.verdict['overall_score']):.1f}/5"
            if session_row.verdict is not None
            else "unscored"
        )
        lines.append(
            f"spawn {session_row.source_project}/{session_row.session_kind}/"
            f"{session_row.raw_session_id} [{session_row.session_id}] "
            f"{session_row.agent_type or 'unknown'}: {score}"
        )

    return "\n".join(lines)
