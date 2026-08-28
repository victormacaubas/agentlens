"""Extracting one spawn's ``SpawnNarrative`` from its parsed transcript records.

Reuses ``assistant_message_groups`` for turn grouping, exactly so a logical
turn written as several consecutive records sharing one ``message.id`` is
never counted more than once. The tool-sequence extraction below pairs
invocations with their results the same way ``tool_events.pair_tool_events``
does, but keeps the tool's raw input rather than its fingerprint, since a
judge reading the projection needs to see what a tool was actually asked to
do, not a hash of it.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from agentlens.ingest.records import JsonRecord, assistant_message_groups, content_blocks
from agentlens.ingest.sidecar import Sidecar
from agentlens.models.narrative import SpawnNarrative, ToolNarrativeEvent

_TEXT_BLOCK_TYPE = "text"
_TOOL_USE_BLOCK_TYPE = "tool_use"
_TOOL_RESULT_BLOCK_TYPE = "tool_result"


@dataclass
class _PendingToolNarrativeEvent:
    """Mutable accumulator for one tool_use, filled in as its result arrives."""

    tool_name: str
    tool_input: Mapping[str, object]
    is_error: bool = False
    denial_kind: str | None = None


def build_spawn_narrative(
    records: Sequence[JsonRecord], *, sidecar: Sidecar | None
) -> SpawnNarrative:
    """Extract the ``SpawnNarrative`` a judge would be rendered from, for ``records``.

    ``task_prompt`` is the sidecar's ``description`` when one was read, and
    empty when there is none, the same rule ``ingest.session`` uses for
    ``task_description``. The result carries no capping or truncation of its
    own.
    """
    task_prompt = sidecar.description if sidecar is not None else ""
    messages = tuple(
        text for group in assistant_message_groups(records) if (text := _turn_text(group))
    )
    return SpawnNarrative(
        task_prompt=task_prompt,
        messages=messages,
        tool_events=_extract_tool_events(records),
    )


def _turn_text(group: Sequence[Mapping[str, object]]) -> str | None:
    """Return the concatenated text of every text block across one turn's fragments.

    ``None`` when the turn carries no text block at all, which keeps a
    purely tool-using or thinking-only turn from contributing an empty
    message entry.
    """
    texts: list[str] = []
    for message in group:
        for block in content_blocks(message):
            if block.get("type") != _TEXT_BLOCK_TYPE:
                continue
            text = block.get("text")
            if isinstance(text, str):
                texts.append(text)
    return "".join(texts) if texts else None


def _extract_tool_events(records: Sequence[JsonRecord]) -> tuple[ToolNarrativeEvent, ...]:
    pending: dict[str, _PendingToolNarrativeEvent] = {}
    order: list[str] = []
    for record in records:
        record_type = record.get("type")
        message = record.get("message")
        if record_type == "assistant":
            for block in content_blocks(message):
                if block.get("type") != _TOOL_USE_BLOCK_TYPE:
                    continue
                tool_use_id = block.get("id")
                if not isinstance(tool_use_id, str):
                    continue
                tool_input = block.get("input")
                pending[tool_use_id] = _PendingToolNarrativeEvent(
                    tool_name=str(block.get("name", "")),
                    tool_input=tool_input if isinstance(tool_input, Mapping) else {},
                )
                order.append(tool_use_id)
        elif record_type == "user":
            denial_kind = record.get("toolDenialKind")
            for block in content_blocks(message):
                if block.get("type") != _TOOL_RESULT_BLOCK_TYPE:
                    continue
                tool_use_id = block.get("tool_use_id")
                if not isinstance(tool_use_id, str):
                    continue
                pending_event = pending.get(tool_use_id)
                if pending_event is None:
                    continue
                pending_event.is_error = bool(block.get("is_error", False))
                pending_event.denial_kind = denial_kind if isinstance(denial_kind, str) else None

    return tuple(_to_tool_narrative_event(pending[tool_use_id]) for tool_use_id in order)


def _to_tool_narrative_event(pending: _PendingToolNarrativeEvent) -> ToolNarrativeEvent:
    return ToolNarrativeEvent(
        tool_name=pending.tool_name,
        tool_input=pending.tool_input,
        is_error=pending.is_error,
        denial_kind=pending.denial_kind,
    )
