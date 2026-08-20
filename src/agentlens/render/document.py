import json
from collections.abc import Mapping
from dataclasses import asdict

from agentlens.models.protocols import Clock
from agentlens.models.report_aggregates import AgentRollup
from agentlens.models.report_document import ReportDocument, ReportSpawn
from agentlens.models.session_facts import SessionFacts
from agentlens.models.skill_signals import SessionSkillSignal
from agentlens.models.windows import ResolvedWindow

SCHEMA_VERSION = 1
SCORING_STATUS_UNSCORED = "unscored"


def build_session_document(facts: SessionFacts, *, clock: Clock) -> dict[str, object]:
    """Build the JSON-serializable report document for one analyzed spawn.

    Carries a schema version, a UTC generation timestamp read from ``clock``,
    one row per qualified spawn, and an explicit unscored marker. This slice
    never runs a judge, so no score, verdict, or fix field appears anywhere in
    the result.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": clock.now().isoformat(),
        "scoring_status": SCORING_STATUS_UNSCORED,
        "spawns": [_build_spawn_row(facts)],
    }


def render_document_json(document: Mapping[str, object]) -> str:
    """Serialize a session document to indented JSON text.

    Mirrors the serialization ``render.artifact.write_session_artifact`` uses
    for the file case, so the stream and file outputs are formatted alike.
    """
    return json.dumps(document, indent=2)


def _build_spawn_row(facts: SessionFacts) -> dict[str, object]:
    session = facts.session
    identity = session.identity
    return {
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
