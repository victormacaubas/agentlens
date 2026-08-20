"""Small helpers for reading fields out of a transcript's already-parsed records.

No soundness or pairing logic lives here: this module only knows how to reach
into a record or a ``message`` object for a value every other module in this
package needs, so that knowledge is defined once.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime

from agentlens.errors import MalformedSourceError

JsonRecord = dict[str, object]

_AGENT_ID_RECORD_KEY = "agentId"
_ATTRIBUTION_AGENT_RECORD_KEY = "attributionAgent"
_CWD_RECORD_KEY = "cwd"
_ASSISTANT_RECORD_TYPE = "assistant"
_TOOL_USE_BLOCK_TYPE = "tool_use"
_SPAWNING_TOOL_NAMES = frozenset({"Agent", "Task"})
_SUBAGENT_TYPE_INPUT_KEY = "subagent_type"


def resolve_cwd(records: Sequence[JsonRecord]) -> str | None:
    """Return the real, unencoded project root carried by the transcript's own ``cwd``.

    ``cwd`` sits at a record's root, a sibling of ``message``, and is expected
    to be the same value on every record in one transcript since it names the
    project the spawn ran in. Uses the first record that carries a non-empty
    string; ``None`` when no record does, which means no project scope can be
    resolved for this spawn at all.
    """
    for record in records:
        value = record.get(_CWD_RECORD_KEY)
        if isinstance(value, str) and value:
            return value
    return None


def earliest_timestamp(records: Sequence[JsonRecord]) -> datetime:
    """Return the earliest usable root-level ``timestamp`` carried by ``records``.

    Raises:
        MalformedSourceError: No record carries a usable ``timestamp``.
    """
    timestamps = [parse_timestamp(record) for record in records if "timestamp" in record]
    if not timestamps:
        raise MalformedSourceError("transcript has no record with a usable timestamp")
    return min(timestamps)


def resolve_agent_id(records: Sequence[JsonRecord], *, fallback: str) -> str:
    """Return the raw subagent identifier carried by ``records``.

    Uses the first record that carries an ``agentId`` string. Falls back to
    ``fallback`` — the id read off the transcript's own filename — when no
    record carries one; the two are expected to agree when both are present.
    """
    for record in records:
        agent_id = record.get(_AGENT_ID_RECORD_KEY)
        if isinstance(agent_id, str):
            return agent_id
    return fallback


def resolve_attribution_agent_types(records: Sequence[JsonRecord]) -> frozenset[str]:
    """Return the distinct ``attributionAgent`` values carried by assistant records.

    ``attributionAgent`` sits at a record's root, a sibling of ``message``,
    not inside it. Records missing the key contribute nothing, which is the
    normal case for the rare stub records that lack every attribution field.
    """
    values: set[str] = set()
    for record in records:
        if record.get("type") != _ASSISTANT_RECORD_TYPE:
            continue
        value = record.get(_ATTRIBUTION_AGENT_RECORD_KEY)
        if isinstance(value, str) and value:
            values.add(value)
    return frozenset(values)


def find_spawning_invocation_subagent_type(
    records: Sequence[JsonRecord], *, tool_use_id: str
) -> str | None:
    """Return the ``subagent_type`` of the spawning tool_use block matching ``tool_use_id``.

    Matches strictly on ``type == "tool_use"`` and ``id == tool_use_id`` inside
    an assistant record's ``message.content``, recognizing either tool name
    Claude Code has written for a subagent spawn: ``Agent``, the name written
    today, and ``Task``, the historical name a spec or an older archive can
    still carry. A substring search over the raw file is deliberately not
    used: unrelated record types echo a tool-use id inside other payloads, so
    a substring match would produce false positives.
    """
    for record in records:
        if record.get("type") != _ASSISTANT_RECORD_TYPE:
            continue
        for block in content_blocks(record.get("message")):
            if block.get("type") != _TOOL_USE_BLOCK_TYPE or block.get("id") != tool_use_id:
                continue
            if block.get("name") not in _SPAWNING_TOOL_NAMES:
                continue
            tool_input = block.get("input")
            if not isinstance(tool_input, Mapping):
                continue
            subagent_type = tool_input.get(_SUBAGENT_TYPE_INPUT_KEY)
            if isinstance(subagent_type, str) and subagent_type:
                return subagent_type
    return None


def parse_timestamp(record: Mapping[str, object]) -> datetime:
    """Parse a record's root-level ``timestamp`` as a timezone-aware instant.

    Raises:
        MalformedSourceError: ``record`` has no usable ``timestamp``, which
            every record type is expected to carry.
    """
    timestamp = record.get("timestamp")
    if not isinstance(timestamp, str):
        raise MalformedSourceError("record is missing a usable 'timestamp'")
    try:
        return datetime.fromisoformat(timestamp)
    except ValueError as exc:
        raise MalformedSourceError(f"record has an unparseable timestamp: {timestamp!r}") from exc


def content_blocks(message: object) -> Sequence[Mapping[str, object]]:
    """Return ``message.content`` as a sequence of mappings, or an empty one."""
    if not isinstance(message, Mapping):
        return ()
    content = message.get("content")
    if isinstance(content, str) or not isinstance(content, Sequence):
        return ()
    return tuple(block for block in content if isinstance(block, Mapping))


def assistant_message_groups(records: Sequence[JsonRecord]) -> list[list[Mapping[str, object]]]:
    """Group assistant records' ``message`` objects by ``message.id``.

    Groups are returned in first-seen order. A single logical turn is
    frequently written as several consecutive assistant records sharing one
    ``message.id``; grouping by that id, rather than counting records, is
    what keeps a fragmented turn from being counted more than once.
    """
    groups: dict[str, list[Mapping[str, object]]] = {}
    order: list[str] = []
    for record in records:
        if record.get("type") != "assistant":
            continue
        message = record.get("message")
        if not isinstance(message, Mapping):
            continue
        message_id = message.get("id")
        if not isinstance(message_id, str):
            continue
        if message_id not in groups:
            groups[message_id] = []
            order.append(message_id)
        groups[message_id].append(message)
    return [groups[key] for key in order]
