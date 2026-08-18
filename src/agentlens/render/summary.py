from pathlib import Path

from agentlens.models.session_facts import SessionFacts

UNSCORED_NOTICE = "scoring: unscored, no judge has run for this spawn"


def build_session_summary(facts: SessionFacts, *, artifact_path: Path) -> str:
    """Build the readable summary of one analyzed spawn.

    Names the agent type and which source named it, the task, the volume and
    health counts, the cache-read proportion, the unscored state, and the
    artifact path. Carries no score, verdict, or fix text: this slice never
    runs a judge.
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
        UNSCORED_NOTICE,
        f"artifact: {artifact_path}",
    ]
    return "\n".join(lines)


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
