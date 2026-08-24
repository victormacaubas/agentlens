"""Rendering a bounded, honest-about-its-own-truncation prompt from a spawn narrative.

``render_prompt`` never silently drops a whole assistant message to fit its
byte ceiling: a message is shortened head-and-tail instead, at whatever cap
the message count leaves room for, because the sentence a budget would cut
is often the one the judge most needs to see. Only when even a minimal
per-message representation cannot fit every message does this module drop
messages from the middle, and it always states how many, in band, rather
than trimming raw bytes across message boundaries. Every place this module
shortens or drops anything, it marks that spot with the word ``ELIDED``, so
the judge is told it is looking at a partial view rather than scoring a
truncated run as though it were complete.
"""

import json
from collections.abc import Sequence
from typing import Final

from agentlens.models.narrative import SpawnNarrative, ToolNarrativeEvent

PER_MESSAGE_HEAD_BYTES: Final = 3000
PER_MESSAGE_TAIL_BYTES: Final = 1000
PROJECTION_CEILING_BYTES: Final = 400_000
TOOL_EVENT_CAP: Final = 200

_MIN_MESSAGE_BUDGET_BYTES: Final = 200
_PER_MESSAGE_RENDER_OVERHEAD_BYTES: Final = 100
_MESSAGE_HEAD_RATIO_NUMERATOR: Final = 3
_MESSAGE_HEAD_RATIO_DENOMINATOR: Final = 4

_SECTION_SEPARATOR: Final = "\n\n"
_MESSAGES_HEADER: Final = "Assistant messages:"
_MESSAGE_ELISION_MARKER: Final = "\n[ELIDED: message shortened]\n"
_TOOL_EVENT_ELISION_MARKER: Final = "[ELIDED: {count} additional tool event(s) omitted]"
_DROPPED_MESSAGES_MARKER: Final = "[ELIDED: {count} messages omitted]"


def render_prompt(narrative: SpawnNarrative) -> str:
    """Render ``narrative`` into the prepared prompt a judge backend is sent.

    The result is deterministic: the same narrative always renders to the
    same string, which is what lets its hash serve as a stable verdict cache
    key. The byte ceiling is enforced by shortening every assistant message
    harder as their count grows, not by trimming the assembled projection:
    a raw trim of the whole string can delete a message entirely if it
    happens to fall in the cut region, which is exactly the failure mode
    per-message capping exists to prevent.
    """
    task_prompt_section = _render_task_prompt(narrative.task_prompt)
    tool_events_section = _render_tool_events(narrative.tool_events)
    other_sections_bytes = len(task_prompt_section.encode("utf-8")) + len(
        tool_events_section.encode("utf-8")
    )
    separators_bytes = 2 * len(_SECTION_SEPARATOR.encode("utf-8"))
    available_for_messages = max(
        PROJECTION_CEILING_BYTES - other_sections_bytes - separators_bytes, 0
    )
    messages_section = _render_messages(narrative.messages, available_bytes=available_for_messages)
    return _SECTION_SEPARATOR.join((task_prompt_section, messages_section, tool_events_section))


def _render_task_prompt(task_prompt: str) -> str:
    capped = _cap_text(
        task_prompt, head_bytes=PER_MESSAGE_HEAD_BYTES, tail_bytes=PER_MESSAGE_TAIL_BYTES
    )
    body = capped if capped else "(empty)"
    return f"Task prompt: {body}"


def _render_messages(messages: tuple[str, ...], *, available_bytes: int) -> str:
    if not messages:
        return "Assistant messages: (none)"

    unshrunk = _render_message_lines(
        messages, head_bytes=PER_MESSAGE_HEAD_BYTES, tail_bytes=PER_MESSAGE_TAIL_BYTES
    )
    if _fits(unshrunk, available_bytes):
        return unshrunk

    content_budget = max(
        available_bytes - len(_MESSAGES_HEADER.encode("utf-8")) - 1,
        0,
    )
    per_message_budget = content_budget // len(messages) - _PER_MESSAGE_RENDER_OVERHEAD_BYTES
    if per_message_budget >= _MIN_MESSAGE_BUDGET_BYTES:
        head_bytes, tail_bytes = _split_budget(per_message_budget)
        shrunk = _render_message_lines(messages, head_bytes=head_bytes, tail_bytes=tail_bytes)
        if _fits(shrunk, available_bytes):
            return shrunk

    return _render_messages_with_drops(messages, content_budget=content_budget)


def _render_message_lines(messages: Sequence[str], *, head_bytes: int, tail_bytes: int) -> str:
    lines = [_MESSAGES_HEADER]
    for index, message in enumerate(messages, start=1):
        lines.append(f"{index}. {_cap_text(message, head_bytes=head_bytes, tail_bytes=tail_bytes)}")
    return "\n".join(lines)


def _render_messages_with_drops(messages: Sequence[str], *, content_budget: int) -> str:
    """Keep as many messages as the budget measurably allows, dropping the rest from the middle.

    This is the genuine last resort: reached only when even the smallest
    per-message representation of every message cannot fit. Messages are
    kept greedily from the head and the tail in alternation, measuring each
    candidate's actual rendered size rather than assuming a worst case, so a
    run with many short messages among the long ones loses as few as
    possible. Whatever remains in the middle is dropped, and the dropped
    count is stated in band rather than left to a byte-level trim to imply.
    """
    head_bytes, tail_bytes = _split_budget(_MIN_MESSAGE_BUDGET_BYTES)
    head_index = 0
    tail_index = len(messages) - 1
    take_from_head = True
    used_bytes = 0
    kept_head: list[str] = []
    kept_tail: list[str] = []
    while head_index <= tail_index:
        index = head_index if take_from_head else tail_index
        capped = _cap_text(messages[index], head_bytes=head_bytes, tail_bytes=tail_bytes)
        rendered = f"{index + 1}. {capped}"
        cost = len(rendered.encode("utf-8")) + 1
        if used_bytes + cost > content_budget:
            break
        used_bytes += cost
        if take_from_head:
            kept_head.append(rendered)
            head_index += 1
        else:
            kept_tail.append(rendered)
            tail_index -= 1
        take_from_head = not take_from_head

    dropped = len(messages) - len(kept_head) - len(kept_tail)
    lines = [_MESSAGES_HEADER, *kept_head]
    if dropped:
        lines.append(_DROPPED_MESSAGES_MARKER.format(count=dropped))
    lines.extend(reversed(kept_tail))
    return "\n".join(lines)


def _split_budget(budget: int) -> tuple[int, int]:
    head_bytes = budget * _MESSAGE_HEAD_RATIO_NUMERATOR // _MESSAGE_HEAD_RATIO_DENOMINATOR
    return head_bytes, budget - head_bytes


def _fits(rendered: str, available_bytes: int) -> bool:
    return len(rendered.encode("utf-8")) <= available_bytes


def _render_tool_events(tool_events: tuple[ToolNarrativeEvent, ...]) -> str:
    if not tool_events:
        return "Tool events: (none)"
    capped_events = tool_events[:TOOL_EVENT_CAP]
    lines = ["Tool events:"]
    for index, event in enumerate(capped_events, start=1):
        lines.append(f"{index}. {_render_tool_event(event)}")
    omitted = len(tool_events) - len(capped_events)
    if omitted:
        lines.append(_TOOL_EVENT_ELISION_MARKER.format(count=omitted))
    return "\n".join(lines)


def _render_tool_event(event: ToolNarrativeEvent) -> str:
    input_json = json.dumps(event.tool_input, sort_keys=True, separators=(",", ":"))
    denial = event.denial_kind if event.denial_kind is not None else "none"
    return f"{event.tool_name} input={input_json} is_error={event.is_error} denial_kind={denial}"


def _cap_text(text: str, *, head_bytes: int, tail_bytes: int) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= head_bytes + tail_bytes:
        return text
    head = encoded[:head_bytes].decode("utf-8", errors="replace")
    tail = encoded[-tail_bytes:].decode("utf-8", errors="replace") if tail_bytes else ""
    return f"{head}{_MESSAGE_ELISION_MARKER}{tail}"
