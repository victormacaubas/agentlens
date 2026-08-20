"""Deriving one session row from a transcript's invocations, sidecar, and context.

Several fields are not aggregations of any tool invocation and come from the
sidecar or its fallback instead: the agent type, task description, spawning
tool-use reference, and nesting depth. The counters and token totals are the
part that is a rollup over the tool-invocation rows and the assistant turns.

``agent_definition`` is the caller's already-resolved binding (or ``None``
when history could not prove one); this module only extracts its identity
for the row and folds it into the derivation. ``skill_inventory`` is the
caller's already-discovered skill directories; this module folds it, together
with the transcript's own firing evidence, into the session-skill bridge and
derives ``n_skills_fired`` from the result.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime

from agentlens.errors import MalformedSourceError
from agentlens.ingest.derivation import (
    DerivationInput,
    agent_definition_derivation_input,
    derive_session_derivation,
    name_resolution_derivation_input,
    sidecar_derivation_input,
    skill_inventory_derivation_input,
    transcript_derivation_input,
)
from agentlens.ingest.name_resolution import NameResolution
from agentlens.ingest.records import JsonRecord, assistant_message_groups, parse_timestamp
from agentlens.ingest.sidecar import Sidecar
from agentlens.ingest.skill_bridge import derive_skill_signals
from agentlens.ingest.skill_inventory import SkillInventoryEntry
from agentlens.models.agent_definitions import AgentDefinition
from agentlens.models.facts import FactSession, FactToolEvent
from agentlens.models.identity import SessionIdentity, SourceRevision
from agentlens.models.skill_signals import SessionSkillSignal

_TOOL_CATEGORY_COUNTERS = {
    "Read": "n_reads",
    "Edit": "n_edits",
    "MultiEdit": "n_edits",
    "Write": "n_writes",
    "Bash": "n_bash",
}
_MILLISECONDS_PER_SECOND = 1000
_INPUT_TOKENS_USAGE_KEY = "input_tokens"
_OUTPUT_TOKENS_USAGE_KEY = "output_tokens"
_CACHE_READ_USAGE_KEY = "cache_read_input_tokens"
_CACHE_CREATION_USAGE_KEY = "cache_creation_input_tokens"


def build_fact_session(
    *,
    identity: SessionIdentity,
    revision: SourceRevision,
    agent_id: str,
    agent_definition: AgentDefinition | None,
    parent_session_id: str | None,
    records: Sequence[JsonRecord],
    tool_events: Sequence[FactToolEvent],
    sidecar: Sidecar | None,
    name_resolution: NameResolution,
    attribution_agent_types: frozenset[str],
    parent_evidence_revision: SourceRevision | None,
    unreadable_line_count: int,
    skill_inventory: Sequence[SkillInventoryEntry],
) -> tuple[FactSession, tuple[SessionSkillSignal, ...]]:
    """Derive one ``FactSession`` and its session-skill bridge rows from a transcript.

    Raises:
        MalformedSourceError: No record in ``records`` carries a usable
            ``timestamp``, so a spawn start time cannot be established.
    """
    turn_groups = assistant_message_groups(records)
    input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens = _sum_trailing_usage(
        turn_groups
    )
    category_counts = _count_by_category(tool_events)
    distinct_files = {
        event.file_identity for event in tool_events if event.file_identity is not None
    }
    started_at, duration_ms = _earliest_timestamp_and_duration(records)
    task_description = sidecar.description if sidecar is not None else ""
    skill_signals = derive_skill_signals(
        session_id=identity.session_id,
        agent_definition=agent_definition,
        skill_inventory=skill_inventory,
        records=records,
        started_at=started_at,
    )
    derivation_fingerprint, derivation_observed_mtime_ns = derive_session_derivation(
        _derivation_inputs(
            revision=revision,
            sidecar=sidecar,
            agent_definition=agent_definition,
            skill_inventory=skill_inventory,
            bridge_skill_names=frozenset(signal.skill_name for signal in skill_signals),
            name_resolution=name_resolution,
            attribution_agent_types=attribution_agent_types,
            parent_evidence_revision=parent_evidence_revision,
        )
    )

    session = FactSession(
        identity=identity,
        revision=revision,
        agent_id=agent_id,
        agent_definition_id=(
            agent_definition.agent_definition_id if agent_definition is not None else None
        ),
        parent_session_id=parent_session_id,
        agent_type=name_resolution.agent_type,
        name_source=name_resolution.name_source,
        task_description=task_description,
        task_prompt_len=len(task_description),
        spawning_tool_use_id=sidecar.tool_use_id if sidecar is not None else None,
        spawn_depth=sidecar.spawn_depth if sidecar is not None else 0,
        started_at=started_at,
        n_turns=len(turn_groups),
        n_invocations=len(tool_events),
        n_reads=category_counts["n_reads"],
        n_edits=category_counts["n_edits"],
        n_writes=category_counts["n_writes"],
        n_bash=category_counts["n_bash"],
        n_distinct_files=len(distinct_files),
        n_errors=sum(1 for event in tool_events if event.is_error),
        n_denials=sum(1 for event in tool_events if event.denial_kind is not None),
        n_repeated_invocations=_count_repeated_invocations(tool_events),
        n_skills_fired=sum(1 for signal in skill_signals if signal.fired),
        duration_ms=duration_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_creation_tokens=cache_creation_tokens,
        unreadable_line_count=unreadable_line_count,
        derivation_fingerprint=derivation_fingerprint,
        derivation_observed_mtime_ns=derivation_observed_mtime_ns,
    )
    return session, skill_signals


def _derivation_inputs(
    *,
    revision: SourceRevision,
    sidecar: Sidecar | None,
    agent_definition: AgentDefinition | None,
    skill_inventory: Sequence[SkillInventoryEntry],
    bridge_skill_names: frozenset[str],
    name_resolution: NameResolution,
    attribution_agent_types: frozenset[str],
    parent_evidence_revision: SourceRevision | None,
) -> list[DerivationInput]:
    inputs = [transcript_derivation_input(revision)]
    if sidecar is not None:
        inputs.append(sidecar_derivation_input(sidecar))
    inputs.append(agent_definition_derivation_input(agent_definition))
    inputs.append(skill_inventory_derivation_input(skill_inventory, skill_names=bridge_skill_names))
    inputs.append(
        name_resolution_derivation_input(
            name_resolution,
            attribution_agent_types=attribution_agent_types,
            parent_revision=parent_evidence_revision,
        )
    )
    return inputs


def _count_by_category(tool_events: Sequence[FactToolEvent]) -> dict[str, int]:
    counts = {"n_reads": 0, "n_edits": 0, "n_writes": 0, "n_bash": 0}
    for event in tool_events:
        counter_name = _TOOL_CATEGORY_COUNTERS.get(event.tool_name)
        if counter_name is not None:
            counts[counter_name] += 1
    return counts


def _count_repeated_invocations(tool_events: Sequence[FactToolEvent]) -> int:
    seen: dict[tuple[str, str], int] = {}
    repeated = 0
    for event in tool_events:
        key = (event.tool_name, event.input_fingerprint)
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 1:
            repeated += 1
    return repeated


def _sum_trailing_usage(
    turn_groups: Sequence[Sequence[Mapping[str, object]]],
) -> tuple[int, int, int, int]:
    """Sum each turn's token figures from its trailing fragment, not every fragment.

    ``message.usage`` is re-emitted cumulatively on each fragment of a
    fragmented turn, so only the last fragment of each group carries the
    resolved totals; summing every fragment overcounts.
    """
    input_tokens = output_tokens = cache_read_tokens = cache_creation_tokens = 0
    for group in turn_groups:
        usage = group[-1].get("usage")
        if not isinstance(usage, Mapping):
            continue
        input_tokens += _usage_int(usage, _INPUT_TOKENS_USAGE_KEY)
        output_tokens += _usage_int(usage, _OUTPUT_TOKENS_USAGE_KEY)
        cache_read_tokens += _usage_int(usage, _CACHE_READ_USAGE_KEY)
        cache_creation_tokens += _usage_int(usage, _CACHE_CREATION_USAGE_KEY)
    return input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens


def _usage_int(usage: Mapping[str, object], key: str) -> int:
    value = usage.get(key)
    return value if isinstance(value, int) else 0


def _earliest_timestamp_and_duration(records: Sequence[JsonRecord]) -> tuple[datetime, int]:
    """Return the earliest usable timestamp and the record-timestamp span in ms.

    Raises:
        MalformedSourceError: No record carries a usable ``timestamp``, so a
            spawn start time cannot be established.
    """
    timestamps = [parse_timestamp(record) for record in records if "timestamp" in record]
    if not timestamps:
        raise MalformedSourceError("transcript has no record with a usable timestamp")
    earliest = min(timestamps)
    span = max(timestamps) - earliest
    return earliest, int(span.total_seconds() * _MILLISECONDS_PER_SECOND)
