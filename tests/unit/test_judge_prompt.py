"""Rendering a spawn narrative into a bounded, self-describing judge prompt.

Covers the caps' tiers (per-message, ceiling-driven shrink, and the
last-resort message-count drop), that nothing under those caps is altered,
that every message survives shrinking rather than being silently trimmed
away, that elision is always marked in-band, and that rendering is
deterministic.
"""

import re

from agentlens.judge.prompt import (
    PER_MESSAGE_HEAD_BYTES,
    PER_MESSAGE_TAIL_BYTES,
    PROJECTION_CEILING_BYTES,
    TOOL_EVENT_CAP,
    render_prompt,
)
from agentlens.utils.hashing import hash_text
from tests.factories import build_spawn_narrative, build_tool_narrative_event

_ELISION_WORD = "ELIDED"
_MESSAGE_LINE = re.compile(r"(?m)^(\d+)\. ")


def _kept_message_indices(prompt: str) -> list[int]:
    return [int(match.group(1)) for match in _MESSAGE_LINE.finditer(prompt)]


def test_narrative_under_every_cap_renders_unchanged() -> None:
    narrative = build_spawn_narrative(
        task_prompt="Implement the ingest pipeline.",
        messages=("I will read the file.", "Done."),
        tool_events=(build_tool_narrative_event(),),
    )

    prompt = render_prompt(narrative)

    assert "Implement the ingest pipeline." in prompt
    assert "I will read the file." in prompt
    assert "Done." in prompt
    assert _ELISION_WORD not in prompt


def test_long_message_is_shortened_head_and_tail_rather_than_dropped() -> None:
    head = "HEAD" * 2000
    tail = "TAIL" * 2000
    long_message = f"{head}{'x' * 10000}{tail}"
    narrative = build_spawn_narrative(messages=(long_message,))

    prompt = render_prompt(narrative)

    assert _ELISION_WORD in prompt
    assert "HEAD" in prompt
    assert "TAIL" in prompt
    assert long_message not in prompt


def test_message_within_the_per_message_cap_is_not_shortened() -> None:
    message = "y" * (PER_MESSAGE_HEAD_BYTES + PER_MESSAGE_TAIL_BYTES)
    narrative = build_spawn_narrative(messages=(message,))

    prompt = render_prompt(narrative)

    assert message in prompt
    assert _ELISION_WORD not in prompt


def test_tool_event_cap_engages_and_marks_itself() -> None:
    events = tuple(
        build_tool_narrative_event(tool_input={"index": index})
        for index in range(TOOL_EVENT_CAP + 5)
    )
    narrative = build_spawn_narrative(tool_events=events)

    prompt = render_prompt(narrative)

    assert _ELISION_WORD in prompt
    assert "5 additional tool event(s) omitted" in prompt


def test_tool_events_at_the_cap_are_not_marked_as_omitted() -> None:
    events = tuple(
        build_tool_narrative_event(tool_input={"index": index}) for index in range(TOOL_EVENT_CAP)
    )
    narrative = build_spawn_narrative(tool_events=events)

    prompt = render_prompt(narrative)

    assert "omitted" not in prompt


def test_many_long_messages_all_survive_shortened_and_within_the_ceiling() -> None:
    """A message count that would overflow the ceiling under standard caps.

    Every message must still appear, in shortened form, rather than the
    middle ones vanishing under a raw trim of the assembled projection.
    """
    messages = tuple(f"msg-{index}-" + "z" * 5000 for index in range(150))
    narrative = build_spawn_narrative(messages=messages)

    prompt = render_prompt(narrative)

    assert len(prompt.encode("utf-8")) <= PROJECTION_CEILING_BYTES
    assert _ELISION_WORD in prompt
    assert "messages omitted" not in prompt
    assert _kept_message_indices(prompt) == list(range(1, 151))


def test_message_count_alone_forces_a_ceiling_driven_shrink() -> None:
    """Messages individually within the standard per-message cap can still overflow in bulk.

    Each message here is short enough that the ordinary per-message cap
    alone would never touch it; only their combined count exceeds the
    ceiling, which is what should trigger the shrink.
    """
    short_but_many = tuple(f"m{index}-" + "a" * 3900 for index in range(110))
    narrative = build_spawn_narrative(messages=short_but_many)

    prompt = render_prompt(narrative)

    assert len(prompt.encode("utf-8")) <= PROJECTION_CEILING_BYTES
    assert _ELISION_WORD in prompt
    assert _kept_message_indices(prompt) == list(range(1, 111))


def test_last_resort_drop_path_names_the_omitted_count_and_stays_within_the_ceiling() -> None:
    """When even a minimal per-message budget cannot fit every message, some are dropped.

    The dropped count is stated in band, never left to a byte trim to
    imply, and the head and tail of the run are still represented.
    """
    messages = tuple(f"msg-{index}-" + "z" * 5000 for index in range(2000))
    narrative = build_spawn_narrative(messages=messages)

    prompt = render_prompt(narrative)

    assert len(prompt.encode("utf-8")) <= PROJECTION_CEILING_BYTES
    match = re.search(r"\[ELIDED: (\d+) messages omitted\]", prompt)
    assert match is not None
    dropped = int(match.group(1))
    assert dropped > 0

    kept = _kept_message_indices(prompt)
    assert len(kept) == 2000 - dropped
    assert kept[0] == 1
    assert kept[-1] == 2000


def test_two_runs_differing_only_in_elided_content_hash_differently() -> None:
    first = build_spawn_narrative(messages=("a" * 10000 + "shared-tail",))
    second = build_spawn_narrative(messages=("b" * 10000 + "shared-tail",))

    first_prompt = render_prompt(first)
    second_prompt = render_prompt(second)

    assert first_prompt != second_prompt
    assert hash_text(first_prompt) != hash_text(second_prompt)


def test_empty_narrative_renders_without_markers() -> None:
    narrative = build_spawn_narrative(task_prompt="", messages=(), tool_events=())

    prompt = render_prompt(narrative)

    assert "(empty)" in prompt
    assert "(none)" in prompt
    assert _ELISION_WORD not in prompt


def test_rendering_the_same_narrative_twice_is_byte_identical() -> None:
    narrative = build_spawn_narrative(
        messages=("first message", "second message" * 500),
        tool_events=(build_tool_narrative_event(),),
    )

    assert render_prompt(narrative) == render_prompt(narrative)


def test_rendering_a_large_narrative_twice_is_byte_identical() -> None:
    messages = tuple(f"msg-{index}-" + "z" * 5000 for index in range(2000))
    narrative = build_spawn_narrative(messages=messages)

    assert render_prompt(narrative) == render_prompt(narrative)
