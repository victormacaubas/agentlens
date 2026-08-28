"""Extracting ``SpawnNarrative`` from parsed transcript records.

Covers what a judge is rendered from: a fragmented turn counted once, the
absence cases (no text, no tools, a textless turn), a malformed content
block tolerated rather than raised, and that repeated extraction of the same
records is stable.
"""

from agentlens.ingest.narrative import build_spawn_narrative
from tests.factories import (
    build_assistant_record,
    build_denied_invocation,
    build_fragmented_turn,
    build_parsed_sidecar,
    build_tool_invocation_pair,
    build_tool_narrative_event,
    build_unmatched_invocation,
)


def test_fragmented_turn_contributes_one_message_and_one_tool_event() -> None:
    """A turn split across three records sharing one ``message.id`` counts once."""
    records = build_fragmented_turn()

    narrative = build_spawn_narrative(records=records, sidecar=None)

    assert narrative.messages == ("I will read the file.",)
    assert len(narrative.tool_events) == 1
    assert narrative.tool_events[0].tool_name == "Read"


def test_no_assistant_text_yields_no_messages() -> None:
    records = build_tool_invocation_pair()

    narrative = build_spawn_narrative(records=records, sidecar=None)

    assert narrative.messages == ()
    assert len(narrative.tool_events) == 1


def test_no_tool_use_yields_no_tool_events() -> None:
    records = [
        build_assistant_record(
            content=[{"type": "text", "text": "All done."}], stop_reason="end_turn"
        )
    ]

    narrative = build_spawn_narrative(records=records, sidecar=None)

    assert narrative.messages == ("All done.",)
    assert narrative.tool_events == ()


def test_assistant_message_with_no_text_block_contributes_no_message() -> None:
    records = [
        build_assistant_record(
            content=[{"type": "thinking", "thinking": "Considering the request."}],
            stop_reason=None,
        )
    ]

    narrative = build_spawn_narrative(records=records, sidecar=None)

    assert narrative.messages == ()


def test_malformed_content_blocks_are_skipped_rather_than_raised() -> None:
    non_mapping_block_record = build_assistant_record(content=[{"type": "text", "text": "ok"}])
    non_mapping_block_record["message"]["content"].append("not-a-mapping")  # type: ignore[index]
    text_key_missing_record = build_assistant_record(content=[{"type": "text"}])
    tool_use_missing_id_record = build_assistant_record(
        content=[{"type": "tool_use", "name": "Read", "input": {}}], stop_reason="tool_use"
    )

    assert build_spawn_narrative(records=[non_mapping_block_record], sidecar=None).messages == (
        "ok",
    )
    assert build_spawn_narrative(records=[text_key_missing_record], sidecar=None).messages == ()
    assert (
        build_spawn_narrative(records=[tool_use_missing_id_record], sidecar=None).tool_events == ()
    )


def test_extraction_is_stable_across_repeated_calls_on_the_same_records() -> None:
    records = [
        *build_tool_invocation_pair(),
        *build_denied_invocation(),
        build_unmatched_invocation(),
    ]

    first = build_spawn_narrative(records=records, sidecar=None)
    second = build_spawn_narrative(records=records, sidecar=None)

    assert first == second


def test_denied_invocation_carries_its_denial_kind() -> None:
    records = build_denied_invocation(denial_kind="automode-blocked")

    narrative = build_spawn_narrative(records=records, sidecar=None)

    assert narrative.tool_events[0].is_error is True
    assert narrative.tool_events[0].denial_kind == "automode-blocked"


def test_unmatched_invocation_has_empty_result_fields() -> None:
    record = build_unmatched_invocation()

    narrative = build_spawn_narrative(records=[record], sidecar=None)

    assert narrative.tool_events[0].is_error is False
    assert narrative.tool_events[0].denial_kind is None


def test_narrative_matches_the_prompt_messages_and_tool_sequence_the_factory_built() -> None:
    """A synthetic transcript's narrative round-trips exactly what was built into it."""
    records = [
        build_assistant_record(
            uuid="uuid-turn-1",
            parent_uuid=None,
            message_id="msg_1",
            content=[{"type": "text", "text": "I will read the file."}],
            stop_reason="tool_use",
        ),
        *build_tool_invocation_pair(
            tool_use_id="toolu_1",
            tool_name="Read",
            result_content="file contents",
            assistant_uuid="uuid-turn-2",
            parent_uuid="uuid-turn-1",
            result_uuid="uuid-result-1",
            message_id="msg_2",
        ),
        build_assistant_record(
            uuid="uuid-turn-3",
            parent_uuid="uuid-result-1",
            message_id="msg_3",
            content=[{"type": "text", "text": "Done."}],
            stop_reason="end_turn",
        ),
    ]
    sidecar = build_parsed_sidecar(description="Ship the fix")

    narrative = build_spawn_narrative(records=records, sidecar=sidecar)

    assert narrative.task_prompt == "Ship the fix"
    assert narrative.messages == ("I will read the file.", "Done.")
    assert narrative.tool_events == (
        build_tool_narrative_event(tool_name="Read", is_error=False, denial_kind=None),
    )


def test_task_prompt_is_empty_when_there_is_no_sidecar() -> None:
    narrative = build_spawn_narrative(records=[], sidecar=None)

    assert narrative.task_prompt == ""
