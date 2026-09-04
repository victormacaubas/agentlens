import json
from collections.abc import Mapping
from dataclasses import asdict

from agentlens.models.facts import FactVerdict
from agentlens.models.judging import RubricDimension
from agentlens.models.protocols import Clock
from agentlens.models.report_aggregates import AgentRollup
from agentlens.models.report_document import ReportDocument, ReportSpawn
from agentlens.models.scoring import (
    ScoringOutcome,
    ScoringStatus,
    SpawnJudgeUsage,
    WindowJudgeUsage,
    WindowScoringOutcome,
    WindowScoringPreview,
)
from agentlens.models.session_facts import SessionFacts
from agentlens.models.skill_signals import SessionSkillSignal
from agentlens.models.windows import ResolvedWindow

SCHEMA_VERSION = 3
SCORING_STATUS_UNSCORED = "unscored"


def build_session_document(
    facts: SessionFacts,
    *,
    clock: Clock,
    scoring_outcome: ScoringOutcome | None = None,
) -> dict[str, object]:
    """Build the JSON-serializable report document for one analyzed spawn.

    Carries a schema version, a UTC generation timestamp read from ``clock``,
    one row per qualified spawn, and an explicit scoring-status marker.
    ``scoring_outcome`` is ``None`` for a run that did not score its spawn,
    which keeps the row's ``"verdict"`` key absent rather than present and
    empty.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": clock.now().isoformat(),
        "scoring_status": (
            scoring_outcome.status.value if scoring_outcome is not None else SCORING_STATUS_UNSCORED
        ),
        "spawns": [_build_spawn_row(facts, scoring_outcome=scoring_outcome)],
    }


def render_document_json(document: Mapping[str, object]) -> str:
    """Serialize a session document to indented JSON text.

    Mirrors the serialization ``render.artifact.write_session_artifact`` uses
    for the file case, so the stream and file outputs are formatted alike.
    """
    return json.dumps(document, indent=2)


def _build_spawn_row(
    facts: SessionFacts,
    *,
    scoring_outcome: ScoringOutcome | None,
) -> dict[str, object]:
    session = facts.session
    identity = session.identity
    row: dict[str, object] = {
        "session_id": identity.session_id,
        "source_project": identity.source_project,
        "session_kind": identity.session_kind,
        "raw_session_id": identity.raw_session_id,
        "agent_type": session.agent_type,
        "name_source": session.name_source,
        "task_description": session.task_description,
        "spawning_tool_use_id": session.spawning_tool_use_id,
        "spawn_depth": session.spawn_depth,
        "n_turns": session.n_turns,
        "n_invocations": session.n_invocations,
        "n_reads": session.n_reads,
        "n_edits": session.n_edits,
        "n_writes": session.n_writes,
        "n_bash": session.n_bash,
        "n_distinct_files": session.n_distinct_files,
        "n_errors": session.n_errors,
        "n_denials": session.n_denials,
        "n_repeated_invocations": session.n_repeated_invocations,
        "duration_ms": session.duration_ms,
        "input_tokens": session.input_tokens,
        "output_tokens": session.output_tokens,
        "cache_read_tokens": session.cache_read_tokens,
        "cache_creation_tokens": session.cache_creation_tokens,
        "unreadable_line_count": session.unreadable_line_count,
    }
    if scoring_outcome is not None:
        row["run_judge_usage"] = _build_spawn_judge_usage(scoring_outcome.spawn_judge_usage)
        if scoring_outcome.status is ScoringStatus.REUSED:
            row["is_reused"] = True
        if scoring_outcome.is_behind_current_input:
            row["is_behind_current_input"] = True
        if scoring_outcome.verdict is not None:
            row["verdict"] = _build_verdict_row(scoring_outcome.verdict)
    return row


def _build_spawn_judge_usage(spawn_judge_usage: SpawnJudgeUsage) -> dict[str, float | int]:
    return {
        "cost_usd": spawn_judge_usage.cost_usd,
        "input_tokens": spawn_judge_usage.input_tokens,
        "output_tokens": spawn_judge_usage.output_tokens,
    }


def _build_verdict_row(fact_verdict: FactVerdict) -> dict[str, object]:
    """Convert one persisted verdict into its JSON-safe mapping.

    ``provenance`` is sourced from the verdict's own recorded split rather
    than the field names below, so a consumer can tell what to escape without
    already knowing this module's naming.
    """
    verdict = fact_verdict.verdict
    return {
        "rubric_version": fact_verdict.rubric_version,
        "judge_model": fact_verdict.judge_model,
        "judge_cost_usd": fact_verdict.judge_cost_usd,
        "judge_input_tokens": fact_verdict.judge_input_tokens,
        "judge_output_tokens": fact_verdict.judge_output_tokens,
        "scored_at": fact_verdict.scored_at.isoformat(),
        "overall_score": verdict.overall_score,
        "dimensions": {
            dimension.value: {
                "score": verdict.dimensions[dimension].score,
                "evidence": list(verdict.dimensions[dimension].evidence),
            }
            for dimension in RubricDimension
        },
        "suggested_fixes": [
            {
                "dimension": fix.dimension.value,
                "target": fix.target,
                "recommendation": fix.recommendation,
                "rationale": fix.rationale,
            }
            for fix in verdict.suggested_fixes
        ],
        "provenance": {
            "locally_derived": list(verdict.provenance.locally_derived),
            "untrusted_model_output": list(verdict.provenance.untrusted_model_output),
        },
    }


def build_report_document_json(document: ReportDocument) -> dict[str, object]:
    """Convert a typed ``ReportDocument`` into its JSON-safe mapping.

    Every field on ``document`` is already resolved, so this is a pure
    conversion: no clock read, no query, no aggregation. Serialize the
    result with :func:`render_document_json`, the same function the
    single-session document uses, so both surfaces stay formatted alike.
    """
    return {
        "schema_version": document.schema_version,
        "generated_at": document.generated_at.isoformat(),
        "window": _build_window_row(document.window),
        "agent_filter": document.agent_filter,
        "spawns": [_build_report_spawn_row(spawn) for spawn in document.spawns],
        "agent_rollups": [_build_agent_rollup_row(rollup) for rollup in document.agent_rollups],
    }


def _build_window_row(window: ResolvedWindow) -> dict[str, object]:
    return {
        "selector": {
            "since_duration": window.selector.since_duration,
            "named_window": window.selector.named_window,
            "range_from": window.selector.range_from,
            "range_to": window.selector.range_to,
        },
        "current_start": window.current_start.isoformat(),
        "current_end": window.current_end.isoformat(),
        "prior_start": window.prior_start.isoformat(),
        "prior_end": window.prior_end.isoformat(),
        "local_timezone": window.local_timezone,
        "min_sessions_for_trend": window.min_sessions_for_trend,
    }


def _build_report_spawn_row(spawn: ReportSpawn) -> dict[str, object]:
    session = spawn.session
    identity = session.identity
    return {
        "session_id": identity.session_id,
        "source_project": identity.source_project,
        "session_kind": identity.session_kind,
        "raw_session_id": identity.raw_session_id,
        "agent_id": session.agent_id,
        "agent_definition_id": session.agent_definition_id,
        "parent_session_id": session.parent_session_id,
        "agent_type": session.agent_type,
        "name_source": session.name_source,
        "task_description": session.task_description,
        "task_prompt_len": session.task_prompt_len,
        "spawning_tool_use_id": session.spawning_tool_use_id,
        "spawn_depth": session.spawn_depth,
        "started_at": session.started_at.isoformat(),
        "n_turns": session.n_turns,
        "n_invocations": session.n_invocations,
        "n_reads": session.n_reads,
        "n_edits": session.n_edits,
        "n_writes": session.n_writes,
        "n_bash": session.n_bash,
        "n_distinct_files": session.n_distinct_files,
        "n_errors": session.n_errors,
        "n_denials": session.n_denials,
        "n_repeated_invocations": session.n_repeated_invocations,
        "n_skills_fired": session.n_skills_fired,
        "duration_ms": session.duration_ms,
        "input_tokens": session.input_tokens,
        "output_tokens": session.output_tokens,
        "cache_read_tokens": session.cache_read_tokens,
        "cache_creation_tokens": session.cache_creation_tokens,
        "unreadable_line_count": session.unreadable_line_count,
        "skill_signals": [_build_skill_signal_row(signal) for signal in spawn.skill_signals],
    }


def _build_skill_signal_row(signal: SessionSkillSignal) -> dict[str, object]:
    return {
        "skill_name": signal.skill_name,
        "declared": signal.declared,
        "available": signal.available,
        "fired": signal.fired,
    }


def build_window_scoring_document_json(outcome: WindowScoringOutcome) -> dict[str, object]:
    """Convert one window scoring run's outcome into its JSON-safe mapping.

    ``stop_reason`` is present only when the run stopped before covering its
    whole window, so a completed run's mapping never carries the key with a
    ``null`` value. Serialize the result with :func:`render_document_json`.
    """
    document: dict[str, object] = {
        "scored": outcome.scored,
        "reused": outcome.reused,
        "skipped": outcome.skipped,
        "failed": outcome.failed,
        "judge_usage": _build_window_judge_usage_row(outcome.judge_usage),
        "unattempted": outcome.unattempted,
    }
    if outcome.stop_reason is not None:
        document["stop_reason"] = outcome.stop_reason.value
    return document


def _build_window_judge_usage_row(judge_usage: WindowJudgeUsage) -> dict[str, float | int]:
    return {
        "cost_usd": judge_usage.cost_usd,
        "input_tokens": judge_usage.input_tokens,
        "output_tokens": judge_usage.output_tokens,
    }


def build_window_scoring_preview_document_json(preview: WindowScoringPreview) -> dict[str, object]:
    """Convert one window scoring dry run's preview into its JSON-safe mapping.

    ``cost_upper_bound_usd`` is named as a bound rather than an estimate, to
    match the wording the terminal surface uses for the same figure.
    """
    return {
        "would_score": preview.would_score,
        "would_reuse": preview.would_reuse,
        "cost_upper_bound_usd": preview.cost_bound_usd,
    }


def _build_agent_rollup_row(rollup: AgentRollup) -> dict[str, object]:
    return {
        "agent_type": rollup.agent_type,
        "n_spawns": rollup.n_spawns,
        "n_spawns_prior": rollup.n_spawns_prior,
        "trend_status": rollup.trend_status,
        "totals": asdict(rollup.totals),
        "averages": asdict(rollup.averages),
        "prior_averages": (
            asdict(rollup.prior_averages) if rollup.prior_averages is not None else None
        ),
        "average_deltas": (
            asdict(rollup.average_deltas) if rollup.average_deltas is not None else None
        ),
        "cache_read_proportion": asdict(rollup.cache_read_proportion),
    }
