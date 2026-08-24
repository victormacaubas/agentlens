import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from agentlens.ingest.agent_definitions import content_addressed_definition_id
from agentlens.ingest.context import SubagentContextCache
from agentlens.ingest.derivation import derive_session_derivation, transcript_derivation_input
from agentlens.ingest.identity import SubagentSourceBundle
from agentlens.ingest.identity import build_subagent_source_bundle as _build_subagent_source_bundle
from agentlens.ingest.sidecar import Sidecar
from agentlens.models.agent_definitions import (
    AgentDefinition,
    AgentDefinitionConfig,
    DefinitionScope,
)
from agentlens.models.facts import FactSession, FactToolEvent
from agentlens.models.identity import NameSource, SessionIdentity, SessionKind, SourceRevision
from agentlens.models.judging import (
    VERDICT_PROVENANCE,
    DimensionScore,
    RubricDimension,
    SuggestedFix,
    Verdict,
    VerdictProvenance,
)
from agentlens.models.narrative import SpawnNarrative, ToolNarrativeEvent
from agentlens.models.report_aggregates import (
    AgentRollup,
    MetricTotals,
    PerSpawnAverages,
    TrendStatus,
    WeightedProportion,
)
from agentlens.models.report_document import REPORT_SCHEMA_VERSION, ReportDocument, ReportSpawn
from agentlens.models.session_facts import SessionFacts
from agentlens.models.skill_signals import KnownState, SessionSkillSignal
from agentlens.models.windows import DEFAULT_MIN_SESSIONS_FOR_TREND, ResolvedWindow, WindowSelector

DEFAULT_AGENT_ID = "agent-0000000000000000000000000000000000"
DEFAULT_PARENT_SESSION_ID = "parent-session-1111111111111111111111"
DEFAULT_TIMESTAMP = "2026-01-01T00:00:00.000Z"
_NO_SUCH_CLAUDE_ROOT = Path("/no-such-claude-root-for-tests")


def build_context_cache(claude_root: Path | None = None) -> SubagentContextCache:
    """A ``SubagentContextCache`` rooted at ``claude_root``, or nowhere at all.

    Defaults to a root that never exists on disk, which every discovery
    function treats the same as an empty directory: no agent definitions, no
    skills. Callers that only care about parsing succeeding, not about
    definition binding or skill signals, can pass this without arguments.
    """
    return SubagentContextCache(claude_root if claude_root is not None else _NO_SUCH_CLAUDE_ROOT)


def build_session_identity(
    *,
    session_id: str = "session-abc123",
    source_project: str = "project-one",
    session_kind: SessionKind = SessionKind.SUBAGENT,
    raw_session_id: str = "raw-abc123",
) -> SessionIdentity:
    return SessionIdentity(
        session_id=session_id,
        source_project=source_project,
        session_kind=session_kind,
        raw_session_id=raw_session_id,
    )


def build_source_revision(
    *,
    mtime_ns: int = 1_700_000_000_000_000_000,
    size: int = 256,
    content_hash: str = "content-hash-abc123",
) -> SourceRevision:
    return SourceRevision(mtime_ns=mtime_ns, size=size, content_hash=content_hash)


def build_fact_tool_event(
    *,
    session_id: str = "session-abc123",
    ordinal: int = 0,
    tool_name: str = "Read",
    input_fingerprint: str = "input-fingerprint-abc123",
    file_identity: str | None = None,
    timestamp: datetime = datetime(2026, 1, 1, tzinfo=UTC),
    is_error: bool = False,
    denial_kind: str | None = None,
    result_size: int | None = 42,
) -> FactToolEvent:
    return FactToolEvent(
        session_id=session_id,
        ordinal=ordinal,
        tool_name=tool_name,
        input_fingerprint=input_fingerprint,
        file_identity=file_identity,
        timestamp=timestamp,
        is_error=is_error,
        denial_kind=denial_kind,
        result_size=result_size,
    )


def build_fact_session(
    *,
    identity: SessionIdentity | None = None,
    revision: SourceRevision | None = None,
    agent_id: str | None = None,
    agent_definition_id: str | None = None,
    parent_session_id: str | None = None,
    agent_type: str = "implementer",
    name_source: NameSource = NameSource.META_JSON,
    task_description: str = "Implement the ingest pipeline",
    task_prompt_len: int | None = None,
    spawning_tool_use_id: str | None = "toolu_spawn",
    spawn_depth: int = 1,
    started_at: datetime = datetime(2026, 1, 1, tzinfo=UTC),
    n_turns: int = 1,
    n_invocations: int = 1,
    n_reads: int = 0,
    n_edits: int = 0,
    n_writes: int = 0,
    n_bash: int = 0,
    n_distinct_files: int = 0,
    n_errors: int = 0,
    n_denials: int = 0,
    n_repeated_invocations: int = 0,
    n_skills_fired: int = 0,
    duration_ms: int = 1_000,
    input_tokens: int = 100,
    output_tokens: int = 50,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
    unreadable_line_count: int = 0,
    derivation_fingerprint: str | None = None,
    derivation_observed_mtime_ns: int | None = None,
) -> FactSession:
    """Build a ``FactSession`` with every field defaulted to a distinct value.

    ``derivation_fingerprint`` and ``derivation_observed_mtime_ns`` default to
    the values the real derivation would produce for ``revision`` alone, so a
    caller that only varies ``revision`` (as the store's staleness tests do)
    gets a fingerprint that changes exactly when the real one would.
    """
    resolved_identity = identity if identity is not None else build_session_identity()
    resolved_revision = revision if revision is not None else build_source_revision()
    if derivation_fingerprint is None or derivation_observed_mtime_ns is None:
        computed_fingerprint, computed_mtime_ns = derive_session_derivation(
            [transcript_derivation_input(resolved_revision)]
        )
        derivation_fingerprint = (
            derivation_fingerprint if derivation_fingerprint is not None else computed_fingerprint
        )
        derivation_observed_mtime_ns = (
            derivation_observed_mtime_ns
            if derivation_observed_mtime_ns is not None
            else computed_mtime_ns
        )
    return FactSession(
        identity=resolved_identity,
        revision=resolved_revision,
        agent_id=agent_id if agent_id is not None else resolved_identity.raw_session_id,
        agent_definition_id=agent_definition_id,
        parent_session_id=parent_session_id,
        agent_type=agent_type,
        name_source=name_source,
        task_description=task_description,
        task_prompt_len=task_prompt_len if task_prompt_len is not None else len(task_description),
        spawning_tool_use_id=spawning_tool_use_id,
        spawn_depth=spawn_depth,
        started_at=started_at,
        n_turns=n_turns,
        n_invocations=n_invocations,
        n_reads=n_reads,
        n_edits=n_edits,
        n_writes=n_writes,
        n_bash=n_bash,
        n_distinct_files=n_distinct_files,
        n_errors=n_errors,
        n_denials=n_denials,
        n_repeated_invocations=n_repeated_invocations,
        n_skills_fired=n_skills_fired,
        duration_ms=duration_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_creation_tokens=cache_creation_tokens,
        unreadable_line_count=unreadable_line_count,
        derivation_fingerprint=derivation_fingerprint,
        derivation_observed_mtime_ns=derivation_observed_mtime_ns,
    )


def build_session_facts(
    *,
    session: FactSession | None = None,
    tool_events: tuple[FactToolEvent, ...] = (),
    skill_signals: tuple[SessionSkillSignal, ...] = (),
) -> SessionFacts:
    return SessionFacts(
        session=session if session is not None else build_fact_session(),
        tool_events=tool_events,
        skill_signals=skill_signals,
    )


def build_session_skill_signal(
    *,
    session_id: str = "session-abc123",
    skill_name: str = "python-engineering-standards",
    declared: KnownState = KnownState.UNKNOWN,
    available: KnownState = KnownState.UNKNOWN,
    fired: bool = False,
) -> SessionSkillSignal:
    return SessionSkillSignal(
        session_id=session_id,
        skill_name=skill_name,
        declared=declared,
        available=available,
        fired=fired,
    )


def build_dimension_score(
    *,
    score: int = 4,
    evidence: tuple[str, ...] = ("The transcript shows the file was read before it was edited.",),
) -> DimensionScore:
    return DimensionScore(score=score, evidence=evidence)


def build_suggested_fix(
    *,
    dimension: RubricDimension = RubricDimension.EFFICIENCY,
    target: str = "the retry loop in ingest/session.py",
    recommendation: str = "Cap the retry count instead of retrying unconditionally.",
    rationale: str = "The transcript shows five retries of the same read.",
) -> SuggestedFix:
    return SuggestedFix(
        dimension=dimension,
        target=target,
        recommendation=recommendation,
        rationale=rationale,
    )


def build_verdict_provenance(
    *,
    locally_derived: tuple[str, ...] = VERDICT_PROVENANCE.locally_derived,
    untrusted_model_output: tuple[str, ...] = VERDICT_PROVENANCE.untrusted_model_output,
) -> VerdictProvenance:
    return VerdictProvenance(
        locally_derived=locally_derived, untrusted_model_output=untrusted_model_output
    )


def build_verdict(
    *,
    overall_score: int = 4,
    dimensions: Mapping[RubricDimension, DimensionScore] | None = None,
    suggested_fixes: tuple[SuggestedFix, ...] | None = None,
    provenance: VerdictProvenance | None = None,
) -> Verdict:
    """Build a fully populated ``Verdict``: every rubric dimension scored, one fix, keyword-only.

    ``dimensions`` defaults to every ``RubricDimension`` scored with
    :func:`build_dimension_score`, so a caller that only wants a validly
    shaped verdict does not have to name all four itself.
    """
    resolved_dimensions = (
        dimensions
        if dimensions is not None
        else {dimension: build_dimension_score() for dimension in RubricDimension}
    )
    return Verdict(
        overall_score=overall_score,
        dimensions=resolved_dimensions,
        suggested_fixes=(
            suggested_fixes if suggested_fixes is not None else (build_suggested_fix(),)
        ),
        provenance=provenance if provenance is not None else build_verdict_provenance(),
    )


def build_tool_narrative_event(
    *,
    tool_name: str = "Read",
    tool_input: Mapping[str, object] | None = None,
    is_error: bool = False,
    denial_kind: str | None = None,
) -> ToolNarrativeEvent:
    return ToolNarrativeEvent(
        tool_name=tool_name,
        tool_input=(
            dict(tool_input) if tool_input is not None else {"file_path": "/workspace/example.txt"}
        ),
        is_error=is_error,
        denial_kind=denial_kind,
    )


def build_spawn_narrative(
    *,
    task_prompt: str = "Implement the ingest pipeline",
    messages: tuple[str, ...] = ("I will read the file.",),
    tool_events: tuple[ToolNarrativeEvent, ...] = (),
) -> SpawnNarrative:
    return SpawnNarrative(task_prompt=task_prompt, messages=messages, tool_events=tool_events)


def build_metric_totals(
    *,
    n_turns: int = 1,
    n_invocations: int = 1,
    n_reads: int = 0,
    n_edits: int = 0,
    n_writes: int = 0,
    n_bash: int = 0,
    n_distinct_files: int = 0,
    n_errors: int = 0,
    n_denials: int = 0,
    n_repeated_invocations: int = 0,
    n_skills_fired: int = 0,
    duration_ms: int = 1_000,
    input_tokens: int = 100,
    output_tokens: int = 50,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
    unreadable_line_count: int = 0,
) -> MetricTotals:
    return MetricTotals(
        n_turns=n_turns,
        n_invocations=n_invocations,
        n_reads=n_reads,
        n_edits=n_edits,
        n_writes=n_writes,
        n_bash=n_bash,
        n_distinct_files=n_distinct_files,
        n_errors=n_errors,
        n_denials=n_denials,
        n_repeated_invocations=n_repeated_invocations,
        n_skills_fired=n_skills_fired,
        duration_ms=duration_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_creation_tokens=cache_creation_tokens,
        unreadable_line_count=unreadable_line_count,
    )


def build_per_spawn_averages(
    *,
    n_turns: float = 1.0,
    n_invocations: float = 1.0,
    n_reads: float = 0.0,
    n_edits: float = 0.0,
    n_writes: float = 0.0,
    n_bash: float = 0.0,
    n_distinct_files: float = 0.0,
    n_errors: float = 0.0,
    n_denials: float = 0.0,
    n_repeated_invocations: float = 0.0,
    n_skills_fired: float = 0.0,
    duration_ms: float = 1_000.0,
    input_tokens: float = 100.0,
    output_tokens: float = 50.0,
    cache_read_tokens: float = 0.0,
    cache_creation_tokens: float = 0.0,
    unreadable_line_count: float = 0.0,
) -> PerSpawnAverages:
    """Build a ``PerSpawnAverages``, the same shape used for a signed delta between two windows."""
    return PerSpawnAverages(
        n_turns=n_turns,
        n_invocations=n_invocations,
        n_reads=n_reads,
        n_edits=n_edits,
        n_writes=n_writes,
        n_bash=n_bash,
        n_distinct_files=n_distinct_files,
        n_errors=n_errors,
        n_denials=n_denials,
        n_repeated_invocations=n_repeated_invocations,
        n_skills_fired=n_skills_fired,
        duration_ms=duration_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_creation_tokens=cache_creation_tokens,
        unreadable_line_count=unreadable_line_count,
    )


def build_weighted_proportion(
    *,
    current: float | None = 0.5,
    prior: float | None = None,
    delta: float | None = None,
) -> WeightedProportion:
    return WeightedProportion(current=current, prior=prior, delta=delta)


def build_agent_rollup(
    *,
    agent_type: str = "implementer",
    n_spawns: int = 1,
    n_spawns_prior: int = 0,
    trend_status: TrendStatus = TrendStatus.INSUFFICIENT_DATA,
    totals: MetricTotals | None = None,
    averages: PerSpawnAverages | None = None,
    prior_averages: PerSpawnAverages | None = None,
    average_deltas: PerSpawnAverages | None = None,
    cache_read_proportion: WeightedProportion | None = None,
) -> AgentRollup:
    return AgentRollup(
        agent_type=agent_type,
        n_spawns=n_spawns,
        n_spawns_prior=n_spawns_prior,
        trend_status=trend_status,
        totals=totals if totals is not None else build_metric_totals(),
        averages=averages if averages is not None else build_per_spawn_averages(),
        prior_averages=prior_averages,
        average_deltas=average_deltas,
        cache_read_proportion=(
            cache_read_proportion
            if cache_read_proportion is not None
            else build_weighted_proportion()
        ),
    )


def build_window_selector(
    *,
    since_duration: str | None = "7d",
    named_window: str | None = None,
    range_from: str | None = None,
    range_to: str | None = None,
) -> WindowSelector:
    return WindowSelector(
        since_duration=since_duration,
        named_window=named_window,
        range_from=range_from,
        range_to=range_to,
    )


def build_resolved_window(
    *,
    selector: WindowSelector | None = None,
    current_start: datetime = datetime(2026, 1, 8, tzinfo=UTC),
    current_end: datetime = datetime(2026, 1, 15, tzinfo=UTC),
    prior_start: datetime = datetime(2026, 1, 1, tzinfo=UTC),
    prior_end: datetime = datetime(2026, 1, 8, tzinfo=UTC),
    local_timezone: str = "UTC",
    min_sessions_for_trend: int = DEFAULT_MIN_SESSIONS_FOR_TREND,
) -> ResolvedWindow:
    return ResolvedWindow(
        selector=selector if selector is not None else build_window_selector(),
        current_start=current_start,
        current_end=current_end,
        prior_start=prior_start,
        prior_end=prior_end,
        local_timezone=local_timezone,
        min_sessions_for_trend=min_sessions_for_trend,
    )


def build_report_spawn(
    *,
    session: FactSession | None = None,
    skill_signals: tuple[SessionSkillSignal, ...] = (),
) -> ReportSpawn:
    return ReportSpawn(
        session=session if session is not None else build_fact_session(),
        skill_signals=skill_signals,
    )


def build_report_document(
    *,
    schema_version: int = REPORT_SCHEMA_VERSION,
    generated_at: datetime = datetime(2026, 1, 15, tzinfo=UTC),
    window: ResolvedWindow | None = None,
    agent_filter: str | None = None,
    spawns: tuple[ReportSpawn, ...] = (),
    agent_rollups: tuple[AgentRollup, ...] = (),
) -> ReportDocument:
    return ReportDocument(
        schema_version=schema_version,
        generated_at=generated_at,
        window=window if window is not None else build_resolved_window(),
        agent_filter=agent_filter,
        spawns=spawns,
        agent_rollups=agent_rollups,
    )


def build_root_fields(
    *,
    record_type: str,
    uuid: str,
    parent_uuid: str | None,
    agent_id: str = DEFAULT_AGENT_ID,
    parent_session_id: str = DEFAULT_PARENT_SESSION_ID,
    timestamp: str = DEFAULT_TIMESTAMP,
    cwd: str | None = None,
) -> dict[str, object]:
    """The record-root keys every transcript line carries, regardless of type.

    ``cwd`` models the real project root a spawn's transcript carries at its
    own root, a sibling of ``message``. Omitted entirely when not supplied,
    matching the observed shape where a field is absent rather than null.
    """
    fields: dict[str, object] = {
        "type": record_type,
        "uuid": uuid,
        "parentUuid": parent_uuid,
        "sessionId": parent_session_id,
        "agentId": agent_id,
        "timestamp": timestamp,
    }
    if cwd is not None:
        fields["cwd"] = cwd
    return fields


def build_tool_use_block(
    *,
    tool_use_id: str = "toolu_1",
    name: str = "Read",
    input: Mapping[str, object] | None = None,
) -> dict[str, object]:
    return {
        "type": "tool_use",
        "id": tool_use_id,
        "name": name,
        "input": dict(input) if input is not None else {"file_path": "/workspace/example.txt"},
    }


def build_agent_tool_use_block(
    *,
    tool_use_id: str = "toolu_spawn",
    name: str = "Agent",
    subagent_type: str = "pathfinder",
    description: str = "Explore the codebase",
    prompt: str = "Find where sessions are parsed",
    run_in_background: bool | None = None,
    model: str | None = None,
) -> dict[str, object]:
    """A subagent-spawning ``tool_use`` block, as it sits in a parent's own transcript.

    ``name`` accepts either tool name Claude Code has written for this
    invocation: ``Agent``, the current name, or ``Task``, the historical one
    an older archive can still carry. ``run_in_background`` and ``model`` are
    omitted entirely when not supplied, matching the observed shape where an
    optional input key is absent rather than null.
    """
    input_fields: dict[str, object] = {
        "description": description,
        "prompt": prompt,
        "subagent_type": subagent_type,
    }
    if run_in_background is not None:
        input_fields["run_in_background"] = run_in_background
    if model is not None:
        input_fields["model"] = model
    return {"type": "tool_use", "id": tool_use_id, "name": name, "input": input_fields}


def build_tool_result_block(
    *,
    tool_use_id: str = "toolu_1",
    content: str | list[dict[str, object]] = "ok",
    is_error: bool = False,
) -> dict[str, object]:
    """A tool result content block.

    ``is_error`` is present only when true, matching the observed transcript
    shape where the key is omitted on success rather than written as ``false``.
    """
    block: dict[str, object] = {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": content,
    }
    if is_error:
        block["is_error"] = True
    return block


def build_assistant_record(
    *,
    uuid: str = "uuid-assistant-1",
    parent_uuid: str | None = "uuid-user-0",
    message_id: str = "msg_1",
    content: Sequence[Mapping[str, object]] = (),
    stop_reason: str | None = None,
    usage: Mapping[str, object] | None = None,
    agent_id: str = DEFAULT_AGENT_ID,
    parent_session_id: str = DEFAULT_PARENT_SESSION_ID,
    timestamp: str = DEFAULT_TIMESTAMP,
    attribution_skill: str | None = None,
    attribution_agent: str | None = None,
    cwd: str | None = None,
) -> dict[str, object]:
    """An assistant transcript record.

    ``attribution_skill`` and ``attribution_agent`` model the observed
    ``attributionSkill`` and ``attributionAgent`` keys, which sit at the
    record's root alongside ``message``, not nested inside it. Both are
    omitted entirely when not supplied, matching the observed shape where an
    inapplicable attribution key is absent rather than null.
    """
    record: dict[str, object] = build_root_fields(
        record_type="assistant",
        uuid=uuid,
        parent_uuid=parent_uuid,
        agent_id=agent_id,
        parent_session_id=parent_session_id,
        timestamp=timestamp,
        cwd=cwd,
    )
    record["message"] = {
        "id": message_id,
        "role": "assistant",
        "content": [dict(block) for block in content],
        "stop_reason": stop_reason,
        "usage": dict(usage) if usage is not None else {"input_tokens": 10, "output_tokens": 5},
    }
    if attribution_skill is not None:
        record["attributionSkill"] = attribution_skill
    if attribution_agent is not None:
        record["attributionAgent"] = attribution_agent
    return record


def build_user_record(
    *,
    uuid: str = "uuid-user-1",
    parent_uuid: str | None = "uuid-assistant-1",
    content: Sequence[Mapping[str, object]] = (),
    agent_id: str = DEFAULT_AGENT_ID,
    parent_session_id: str = DEFAULT_PARENT_SESSION_ID,
    timestamp: str = DEFAULT_TIMESTAMP,
    tool_denial_kind: str | None = None,
    cwd: str | None = None,
) -> dict[str, object]:
    record: dict[str, object] = build_root_fields(
        record_type="user",
        uuid=uuid,
        parent_uuid=parent_uuid,
        agent_id=agent_id,
        parent_session_id=parent_session_id,
        timestamp=timestamp,
        cwd=cwd,
    )
    record["message"] = {"role": "user", "content": [dict(block) for block in content]}
    if tool_denial_kind is not None:
        record["toolDenialKind"] = tool_denial_kind
    return record


def build_tool_invocation_pair(
    *,
    tool_use_id: str = "toolu_1",
    tool_name: str = "Read",
    tool_input: Mapping[str, object] | None = None,
    result_content: str | list[dict[str, object]] = "file contents",
    is_error: bool = False,
    assistant_uuid: str = "uuid-assistant-1",
    parent_uuid: str | None = "uuid-user-0",
    result_uuid: str = "uuid-user-1",
    message_id: str = "msg_1",
) -> list[dict[str, object]]:
    """A tool invocation followed by its matching result, as two records.

    ``result_content`` accepts either shape observed in the wild: a plain
    string, or a list of ``{"type": "text", "text": ...}`` blocks.
    """
    assistant = build_assistant_record(
        uuid=assistant_uuid,
        parent_uuid=parent_uuid,
        message_id=message_id,
        content=[build_tool_use_block(tool_use_id=tool_use_id, name=tool_name, input=tool_input)],
        stop_reason="tool_use",
    )
    result = build_user_record(
        uuid=result_uuid,
        parent_uuid=assistant_uuid,
        content=[
            build_tool_result_block(
                tool_use_id=tool_use_id, content=result_content, is_error=is_error
            )
        ],
    )
    return [assistant, result]


def build_denied_invocation(
    *,
    tool_use_id: str = "toolu_denied",
    tool_name: str = "Bash",
    denial_kind: str = "permission-rule",
    assistant_uuid: str = "uuid-denied-assistant",
    parent_uuid: str | None = "uuid-user-0",
    result_uuid: str = "uuid-denied-result",
    message_id: str = "msg_denied",
) -> list[dict[str, object]]:
    """An invocation whose result was a permission denial, ``toolDenialKind`` and all.

    ``toolDenialKind`` sits at the result record's root, a sibling of
    ``message``, not inside the ``tool_result`` content item.
    """
    assistant = build_assistant_record(
        uuid=assistant_uuid,
        parent_uuid=parent_uuid,
        message_id=message_id,
        content=[build_tool_use_block(tool_use_id=tool_use_id, name=tool_name)],
        stop_reason="tool_use",
    )
    result = build_user_record(
        uuid=result_uuid,
        parent_uuid=assistant_uuid,
        content=[
            build_tool_result_block(
                tool_use_id=tool_use_id, content="Permission denied", is_error=True
            )
        ],
        tool_denial_kind=denial_kind,
    )
    return [assistant, result]


def build_unmatched_invocation(
    *,
    tool_use_id: str = "toolu_unmatched",
    tool_name: str = "Bash",
    uuid: str = "uuid-unmatched",
    parent_uuid: str | None = None,
    message_id: str = "msg_unmatched",
) -> dict[str, object]:
    """A tool invocation that never receives a result, as at end of file."""
    return build_assistant_record(
        uuid=uuid,
        parent_uuid=parent_uuid,
        message_id=message_id,
        content=[build_tool_use_block(tool_use_id=tool_use_id, name=tool_name)],
        stop_reason="tool_use",
    )


def build_fragmented_turn(
    *,
    message_id: str = "msg_fragmented",
    tool_use_id: str = "toolu_fragment",
    tool_name: str = "Read",
    parent_uuid: str | None = "uuid-user-0",
    interior_usage: Mapping[str, object] | None = None,
    final_usage: Mapping[str, object] | None = None,
) -> list[dict[str, object]]:
    """One turn written as several assistant records sharing one ``message.id``.

    Models the observed shape where a response with thinking plus a tool call
    is written as one record per content block. Interior fragments carry
    ``stop_reason: null`` and re-emit ``usage`` cumulatively; the same
    ``output_tokens`` figure repeats across them, then jumps on the trailing
    fragment, which alone carries the resolved ``stop_reason``. A caller that
    counts turns per assistant record, or sums ``usage`` across fragments,
    overcounts both: this fixture is one turn, and its true token totals are
    ``final_usage``, not the sum of all three records' figures.
    """
    interior = (
        dict(interior_usage)
        if interior_usage is not None
        else {"input_tokens": 500, "output_tokens": 40, "cache_read_input_tokens": 300}
    )
    final = (
        dict(final_usage)
        if final_usage is not None
        else {"input_tokens": 500, "output_tokens": 120, "cache_read_input_tokens": 300}
    )
    thinking_fragment = build_assistant_record(
        uuid="uuid-fragment-thinking",
        parent_uuid=parent_uuid,
        message_id=message_id,
        content=[{"type": "thinking", "thinking": "Considering the request."}],
        stop_reason=None,
        usage=interior,
    )
    text_fragment = build_assistant_record(
        uuid="uuid-fragment-text",
        parent_uuid="uuid-fragment-thinking",
        message_id=message_id,
        content=[{"type": "text", "text": "I will read the file."}],
        stop_reason=None,
        usage=interior,
    )
    tool_use_fragment = build_assistant_record(
        uuid="uuid-fragment-tool-use",
        parent_uuid="uuid-fragment-text",
        message_id=message_id,
        content=[build_tool_use_block(tool_use_id=tool_use_id, name=tool_name)],
        stop_reason="tool_use",
        usage=final,
    )
    return [thinking_fragment, text_fragment, tool_use_fragment]


def build_unparseable_line() -> str:
    """A transcript line that fails JSON parsing outright."""
    return "{not valid json"


def build_parsed_sidecar(
    *,
    agent_type: str = "implementer",
    description: str = "Implement the ingest pipeline",
    tool_use_id: str = "toolu_spawn",
    spawn_depth: int = 1,
    parent_agent_id: str | None = None,
    model: str | None = None,
    revision: SourceRevision | None = None,
) -> Sidecar:
    """Build the parsed ``Sidecar`` dataclass a real ``.meta.json`` read would return.

    Distinct from :func:`build_sidecar`, which renders the on-disk JSON
    shape: this is the value ``ingest.sidecar.read_sidecar`` hands back,
    for callers that want a sidecar without writing one to disk and reading
    it back.
    """
    return Sidecar(
        agent_type=agent_type,
        description=description,
        tool_use_id=tool_use_id,
        spawn_depth=spawn_depth,
        parent_agent_id=parent_agent_id,
        model=model,
        revision=revision if revision is not None else build_source_revision(),
    )


def build_sidecar(
    *,
    agent_type: str = "implementer",
    description: str = "Implement the ingest pipeline",
    tool_use_id: str = "toolu_spawn",
    spawn_depth: int = 1,
    parent_agent_id: str | None = None,
    model: str | None = None,
) -> dict[str, object]:
    """A ``.meta.json`` sidecar. ``parentAgentId`` and ``model`` are optional keys.

    Both are omitted entirely when not supplied, matching the observed shape
    where an optional key is absent rather than present with a null value.
    """
    sidecar: dict[str, object] = {
        "agentType": agent_type,
        "description": description,
        "toolUseId": tool_use_id,
        "spawnDepth": spawn_depth,
    }
    if parent_agent_id is not None:
        sidecar["parentAgentId"] = parent_agent_id
    if model is not None:
        sidecar["model"] = model
    return sidecar


def build_transcript_path(
    base: Path,
    *,
    project: str = "project-one",
    parent_session_id: str = DEFAULT_PARENT_SESSION_ID,
    raw_session_id: str = "0000000000000000000000000000000000",
) -> Path:
    """Return where a subagent transcript would sit under ``base``.

    Models ``.claude/projects/<project>/<parent-session-uuid>/subagents/
    agent-<agentId>.jsonl``. Does not create anything; callers write the
    transcript and any sidecar themselves at the returned path.
    """
    return (
        base
        / ".claude"
        / "projects"
        / project
        / parent_session_id
        / "subagents"
        / f"agent-{raw_session_id}.jsonl"
    )


def build_main_session_path(
    base: Path,
    *,
    project: str = "project-one",
    raw_session_id: str = DEFAULT_PARENT_SESSION_ID,
) -> Path:
    """Return where a main-session transcript would sit under ``base``.

    Models ``.claude/projects/<project>/<session-uuid>.jsonl``, the file a
    subagent's ``parent_session_id`` derives from. ``raw_session_id`` here is
    a main session's own uuid, matching the directory name
    :func:`build_transcript_path` nests a subagent's ``subagents/`` folder
    under when the two calls share the same ``project`` and id.
    """
    return base / ".claude" / "projects" / project / f"{raw_session_id}.jsonl"


def build_subagent_source_bundle(*, transcript_path: Path) -> SubagentSourceBundle:
    """The bundle discovery would build for a transcript written at ``transcript_path``.

    Lets a test that writes a transcript directly, without going through
    :func:`agentlens.ingest.discovery.discover_subagent_sources`, still hand
    :func:`~agentlens.ingest.transcript.parse_transcript` the same bundle
    shape production code would, including its existence-checked sidecar
    path. Call this after writing every file the transcript depends on, so
    the sidecar check sees what the test actually wrote.
    """
    return _build_subagent_source_bundle(transcript_path)


def build_transcript_text(records: Sequence[Mapping[str, object]]) -> str:
    """Serialize records as newline-delimited JSON, one record per line."""
    lines = [json.dumps(record) for record in records]
    return "\n".join(lines) + "\n" if lines else ""


def build_agent_definition_config(
    *,
    name: str = "implementer",
    model: str | None = "claude-sonnet-5[1m]",
    effort: str | None = "high",
    tools: tuple[str, ...] = ("Read", "Write", "Edit", "Bash", "Grep", "Glob"),
    skills: tuple[str, ...] = ("craft:python-engineering-standards",),
) -> AgentDefinitionConfig:
    return AgentDefinitionConfig(name=name, model=model, effort=effort, tools=tools, skills=skills)


def build_agent_definition(
    *,
    scope: DefinitionScope = DefinitionScope.USER,
    source_project: str | None = None,
    config: AgentDefinitionConfig | None = None,
    revision: SourceRevision | None = None,
    agent_definition_id: str | None = None,
) -> AgentDefinition:
    """Build an ``AgentDefinition`` whose identity matches the real content-addressing rule.

    ``agent_definition_id`` defaults to what
    :func:`agentlens.ingest.agent_definitions.content_addressed_definition_id`
    would compute from ``scope``, ``source_project``, and ``revision``'s
    content hash, so a caller that only varies those inputs gets an identity
    that changes exactly when the real one would.
    """
    resolved_config = config if config is not None else build_agent_definition_config()
    resolved_revision = revision if revision is not None else build_source_revision()
    resolved_id = (
        agent_definition_id
        if agent_definition_id is not None
        else content_addressed_definition_id(
            scope=scope, source_project=source_project, content_hash=resolved_revision.content_hash
        )
    )
    return AgentDefinition(
        agent_definition_id=resolved_id,
        scope=scope,
        source_project=source_project,
        config=resolved_config,
        revision=resolved_revision,
    )


def build_agent_definition_text(
    *,
    name: str = "implementer",
    model: str | None = "claude-sonnet-5[1m]",
    effort: str | None = "high",
    tools: str | Sequence[str] | None = "Read, Write, Edit, Bash, Grep, Glob",
    skills: Sequence[str] | None = ("craft:python-engineering-standards",),
    unknown_fields: Mapping[str, str] | None = None,
    body: str = "You are an agent.",
) -> str:
    """Render one agent-definition Markdown file's frontmatter and body.

    ``tools`` accepts a pre-joined scalar string (the shape every real
    definition on this machine uses), a sequence rendered as a block list, or
    a literal string passed through verbatim so a caller can construct the
    unsupported ``tools: ["Read", "Grep"]`` shape. ``None`` omits the key
    entirely. ``skills`` is always rendered as a block list when given,
    matching every real skills-bearing definition; ``None`` omits the key
    entirely.
    """
    lines = ["---", f"name: {name}"]
    if model is not None:
        lines.append(f"model: {model}")
    if effort is not None:
        lines.append(f"effort: {effort}")
    if tools is not None:
        if isinstance(tools, str):
            lines.append(f"tools: {tools}")
        else:
            lines.append("tools:")
            lines.extend(f"  - {tool}" for tool in tools)
    for key, value in (unknown_fields or {}).items():
        lines.append(f"{key}: {value}")
    if skills is not None:
        lines.append("skills:")
        lines.extend(f"  - {skill}" for skill in skills)
    lines.append("---")
    lines.append("")
    lines.append(body)
    lines.append("")
    return "\n".join(lines)


def build_skill_md_text(*, name: str = "example-skill", body: str = "A skill.") -> str:
    """Render a minimal ``SKILL.md``, frontmatter ``name:`` and all.

    Matches every real ``SKILL.md`` on this machine, which opens with a
    frontmatter block naming the skill.
    """
    return f"---\nname: {name}\n---\n\n{body}\n"


def build_skill_md_path(base: Path, *, skill_name: str) -> Path:
    """Return where a user- or project-scoped ``SKILL.md`` would sit under ``base``.

    Models ``.claude/skills/<skill>/SKILL.md``. The same shape roots both
    scopes; the caller decides whether ``base`` plays the role of a home
    directory or a project's ``cwd``.
    """
    return base / ".claude" / "skills" / skill_name / "SKILL.md"


def build_plugin_cache_skill_path(
    base: Path,
    *,
    skill_name: str,
    marketplace: str = "marketplace-one",
    plugin: str = "plugin-one",
    plugin_hash: str = "0123456789ab",
    version: str = "1.0.0",
    shape: Literal[
        "marketplace_hash", "plugin_hash_skill", "plugin_version_skills"
    ] = "marketplace_hash",
) -> Path:
    """Return where a plugin-cached ``SKILL.md`` would sit, in one of three observed depth shapes.

    All three end in the skill's own leaf directory holding ``SKILL.md``:
    ``<marketplace>/<skill>/<hash>``, ``<marketplace>/<plugin>/<hash>/<skill>``,
    and ``<marketplace>/<plugin>/<version>/skills/<skill>``.
    """
    root = base / ".claude" / "plugins" / "cache" / marketplace
    if shape == "marketplace_hash":
        return root / skill_name / plugin_hash / "SKILL.md"
    if shape == "plugin_hash_skill":
        return root / plugin / plugin_hash / skill_name / "SKILL.md"
    return root / plugin / version / "skills" / skill_name / "SKILL.md"
