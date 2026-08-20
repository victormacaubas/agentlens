"""Pairing tool invocations with their results into one row per invocation.

Each unmatched ``tool_use`` is held in a buffer keyed by its id until the
matching ``tool_result`` arrives, or until end of file. The buffer is bounded
by however many calls are in flight at once, never by transcript length.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from agentlens.ingest.records import JsonRecord, content_blocks, parse_timestamp
from agentlens.models.facts import FactToolEvent
from agentlens.utils.hashing import canonical_json_fingerprint, file_identity

_FILE_PATH_INPUT_KEY = "file_path"


@dataclass
class _PendingInvocation:
    """Mutable accumulator for one tool_use, filled in as its result arrives.

    Mutated in place rather than replaced, since the pairing buffer holds
    these by id until either a matching ``tool_result`` updates them or end
    of file leaves them with their initial, empty result fields.
    """

    ordinal: int
    tool_name: str
    input_fingerprint: str
    file_identity: str | None
    timestamp: datetime
    is_error: bool = False
    denial_kind: str | None = None
    result_size: int | None = None


def pair_tool_events(
    records: Sequence[JsonRecord], *, session_id: str
) -> tuple[FactToolEvent, ...]:
    """Return one ``FactToolEvent`` per tool invocation found in ``records``.

    Invocations are ordered by when they were issued, not by when their
    result arrived. An invocation with no matching result by end of file is
    still returned, with empty result fields, rather than dropped.
    """
    pending: dict[str, _PendingInvocation] = {}
    order: list[str] = []
    for record in records:
        record_type = record.get("type")
        message = record.get("message")
        if record_type == "assistant":
            timestamp = parse_timestamp(record)
            for block in content_blocks(message):
                if block.get("type") != "tool_use":
                    continue
                tool_use_id = block.get("id")
                if not isinstance(tool_use_id, str):
                    continue
                tool_input = block.get("input")
                input_mapping: Mapping[str, object] = (
                    tool_input if isinstance(tool_input, Mapping) else {}
                )
                pending[tool_use_id] = _PendingInvocation(
                    ordinal=len(order),
                    tool_name=str(block.get("name", "")),
                    input_fingerprint=canonical_json_fingerprint(input_mapping),
                    file_identity=_resolve_file_identity(input_mapping),
                    timestamp=timestamp,
                )
                order.append(tool_use_id)
        elif record_type == "user":
            denial_kind = record.get("toolDenialKind")
            for block in content_blocks(message):
                if block.get("type") != "tool_result":
                    continue
                tool_use_id = block.get("tool_use_id")
                if not isinstance(tool_use_id, str):
                    continue
                invocation = pending.get(tool_use_id)
                if invocation is None:
                    continue
                invocation.is_error = bool(block.get("is_error", False))
                invocation.denial_kind = denial_kind if isinstance(denial_kind, str) else None
                invocation.result_size = _result_size(block.get("content"))

    return tuple(_to_fact_tool_event(pending[tool_use_id], session_id) for tool_use_id in order)


def _resolve_file_identity(input_mapping: Mapping[str, object]) -> str | None:
    path = input_mapping.get(_FILE_PATH_INPUT_KEY)
    return file_identity(path) if isinstance(path, str) else None


def _result_size(content: object) -> int:
    """Character length of a tool result: the string, or its text blocks summed.

    ``tool_result.content`` has two shapes in the wild, so one branch is not
    enough.
    """
    if isinstance(content, str):
        return len(content)
    if isinstance(content, Sequence):
        total = 0
        for block in content:
            if isinstance(block, Mapping):
                text = block.get("text")
                if isinstance(text, str):
                    total += len(text)
        return total
    return 0


def _to_fact_tool_event(invocation: _PendingInvocation, session_id: str) -> FactToolEvent:
    return FactToolEvent(
        session_id=session_id,
        ordinal=invocation.ordinal,
        tool_name=invocation.tool_name,
        input_fingerprint=invocation.input_fingerprint,
        file_identity=invocation.file_identity,
        timestamp=invocation.timestamp,
        is_error=invocation.is_error,
        denial_kind=invocation.denial_kind,
        result_size=invocation.result_size,
    )
