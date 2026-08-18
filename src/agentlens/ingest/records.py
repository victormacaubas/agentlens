"""Small helpers for reading fields out of a transcript's already-parsed records.

No soundness or pairing logic lives here: this module only knows how to reach
into a record or a ``message`` object for a value every other module in this
package needs, so that knowledge is defined once.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime

from agentlens.errors import MalformedSourceError

JsonRecord = dict[str, object]


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
