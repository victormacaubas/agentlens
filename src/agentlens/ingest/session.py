"""Deriving one session row from a transcript's invocations and sidecar.

Several fields are not aggregations of any tool invocation and come from the
sidecar or its fallback instead: the agent type, task description, spawning
tool-use reference, and nesting depth. The counters and token totals are the
part that is a rollup over the tool-invocation rows and the assistant turns.
"""

from collections.abc import Mapping, Sequence

from agentlens.ingest.name_resolution import NameResolution
from agentlens.ingest.records import JsonRecord, assistant_message_groups, parse_timestamp
from agentlens.ingest.sidecar import Sidecar
from agentlens.models.facts import FactSession, FactToolEvent
from agentlens.models.identity import SessionIdentity, SourceRevision

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
    records: Sequence[JsonRecord],
    tool_events: Sequence[FactToolEvent],
    sidecar: Sidecar | None,
    name_resolution: NameResolution,
    unreadable_line_count: int,
) -> FactSession:
    """Derive one ``FactSession`` from a parsed transcript's contents."""
    turn_groups = assistant_message_groups(records)
    input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens = _sum_trailing_usage(
        turn_groups
    )
    category_counts = _count_by_category(tool_events)
    distinct_files = {
        event.file_identity for event in tool_events if event.file_identity is not None
    }

    return FactSession(
        identity=identity,
        revision=revision,
        agent_type=name_resolution.agent_type,
        name_source=name_resolution.name_source,
        task_description=sidecar.description if sidecar is not None else "",
        spawning_tool_use_id=sidecar.tool_use_id if sidecar is not None else None,
        spawn_depth=sidecar.spawn_depth if sidecar is not None else 0,
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
        duration_ms=_duration_ms(records),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_creation_tokens=cache_creation_tokens,
        unreadable_line_count=unreadable_line_count,
    )


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


def _duration_ms(records: Sequence[JsonRecord]) -> int:
    timestamps = [parse_timestamp(record) for record in records if "timestamp" in record]
    if not timestamps:
        return 0
    span = max(timestamps) - min(timestamps)
    return int(span.total_seconds() * _MILLISECONDS_PER_SECOND)
