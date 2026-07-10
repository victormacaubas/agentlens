"""Terminal rendering for the `report` command's output (D5)."""

from __future__ import annotations

from agentlens.reporting.queries import ReportResult


def render_terminal_summary(result: ReportResult) -> str:
    """A thin, human-readable terminal summary of `result` (D5)."""
    lines = [
        f"window: {result.window.start.isoformat()} to {result.window.end.isoformat()} "
        f"({result.window.n_days}d)"
    ]
    if result.agent_type_filter is not None:
        lines.append(f"agent filter: {result.agent_type_filter}")

    if not result.agents:
        lines.append("no spawns in this window")
    for agent_result in result.agents:
        agg = agent_result.aggregate
        line = f"{agg.agent_type}: {agg.n_spawns} spawns, {agg.n_failures} failures"
        if agg.n_denial_spawns:
            line += f", {agg.n_denial_spawns} hit denials"
        if agent_result.insufficient_data:
            line += f" (n={agg.n_spawns}, insufficient data for trend)"
        elif agent_result.delta is not None:
            line += f" (Δ spawns vs prior window: {agent_result.delta['n_spawns']:+.0f})"
        lines.append(line)

    for row in result.parent_lens:
        lines.append(
            f"parent {row.parent_session_id}: {row.n_spawns} spawns, "
            f"{row.n_failures} failures, {row.n_denial_spawns} hit denials"
        )

    return "\n".join(lines)
