from pathlib import Path

from agentlens.models.facts import FactSession, FactVerdict
from agentlens.models.judging import RubricDimension
from agentlens.models.report_document import ReportDocument
from agentlens.models.session_facts import SessionFacts

UNSCORED_NOTICE = "scoring: unscored, no judge has run for this spawn"


def build_session_summary(
    facts: SessionFacts, *, artifact_path: Path, verdict: FactVerdict | None = None
) -> str:
    """Build the readable summary of one analyzed spawn.

    Names the agent type and which source named it, the task, the volume and
    health counts, the cache-read proportion, and the artifact path. When
    ``verdict`` is ``None``, states the spawn is unscored, unchanged from
    before scoring existed. When it is not ``None``, names the overall score,
    each dimension score, where the suggested fixes were recorded, the
    judge's own cost and token counts, and the analyzed spawn's own token
    usage. Never reads a verdict's evidence, recommendation, rationale, or fix
    target: those are untrusted model output and this surface does not
    render them.
    """
    session = facts.session
    proportion = _cache_read_proportion(
        cache_read_tokens=session.cache_read_tokens,
        cache_creation_tokens=session.cache_creation_tokens,
        input_tokens=session.input_tokens,
    )
    lines = [
        f"agent_type: {session.agent_type} (name_source={session.name_source})",
        f"task: {session.task_description}",
        (
            f"turns={session.n_turns} invocations={session.n_invocations} "
            f"reads={session.n_reads} edits={session.n_edits} "
            f"writes={session.n_writes} bash={session.n_bash}"
        ),
        (
            f"errors={session.n_errors} denials={session.n_denials} "
            f"unreadable_lines={session.unreadable_line_count}"
        ),
        f"cache_read_proportion={proportion:.1%}",
    ]
    if verdict is None:
        lines.append(UNSCORED_NOTICE)
    else:
        lines.extend(_scored_lines(verdict, artifact_path=artifact_path, session=session))
    lines.append(f"artifact: {artifact_path}")
    return "\n".join(lines)


def _scored_lines(
    fact_verdict: FactVerdict, *, artifact_path: Path, session: FactSession
) -> list[str]:
    scored = fact_verdict.verdict
    lines = [f"overall_score: {scored.overall_score}"]
    lines.extend(
        f"{dimension.value}: {scored.dimensions[dimension].score}" for dimension in RubricDimension
    )
    lines.append(f"suggested_fixes: recorded at {artifact_path}")
    lines.append(
        f"judge_cost=${fact_verdict.judge_cost_usd} "
        f"judge_input_tokens={fact_verdict.judge_input_tokens} "
        f"judge_output_tokens={fact_verdict.judge_output_tokens}"
    )
    lines.append(
        f"analyzed_tokens: input={session.input_tokens} "
        f"output={session.output_tokens} "
        f"cache_read={session.cache_read_tokens} "
        f"cache_creation={session.cache_creation_tokens}"
    )
    return lines


def _cache_read_proportion(
    *, cache_read_tokens: int, cache_creation_tokens: int, input_tokens: int
) -> float:
    """Cache reads as a share of every input-side token the run consumed.

    The three token fields are disjoint: ``input_tokens`` counts uncached tokens
    only, so the denominator is all three summed. Leaving cache creation out would
    inflate the figure exactly when a run kept rewriting its cache, which is the
    instability this signal exists to surface.
    """
    total = cache_read_tokens + cache_creation_tokens + input_tokens
    if total == 0:
        return 0.0
    return cache_read_tokens / total


def build_report_summary(document: ReportDocument, *, artifact_path: Path) -> str:
    """Build the readable terminal summary for one report window.

    Names the resolved window, the agent scope, the total spawn count, each
    covered agent type's current and prior population and trend status, and
    the artifact path. Carries no score and no task-description text: a
    report window covers many spawns, not one task, and Phase 2 never runs a
    judge.
    """
    window = document.window
    lines = [
        (
            f"window: {window.current_start.isoformat()} to "
            f"{window.current_end.isoformat()} (local_timezone={window.local_timezone})"
        ),
        f"agent_scope: {document.agent_filter if document.agent_filter is not None else 'all'}",
        f"total_spawns: {len(document.spawns)}",
    ]
    lines.extend(
        f"{rollup.agent_type}: n_spawns={rollup.n_spawns} "
        f"n_spawns_prior={rollup.n_spawns_prior} trend={rollup.trend_status}"
        for rollup in document.agent_rollups
    )
    lines.append(f"artifact: {artifact_path}")
    return "\n".join(lines)
