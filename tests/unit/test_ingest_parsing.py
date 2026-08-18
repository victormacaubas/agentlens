"""Parsing a transcript into tool-event rows and one session row."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentlens.errors import MalformedSourceError
from agentlens.ingest.transcript import parse_transcript
from agentlens.models.identity import NameSource, SessionKind
from agentlens.utils.hashing import canonical_json_fingerprint
from tests.factories import (
    build_assistant_record,
    build_fragmented_turn,
    build_sidecar,
    build_tool_invocation_pair,
    build_tool_result_block,
    build_tool_use_block,
    build_transcript_path,
    build_transcript_text,
    build_unmatched_invocation,
    build_unparseable_line,
    build_user_record,
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_invocation_with_a_result_and_invocation_with_none_both_produce_rows(
    tmp_path: Path,
) -> None:
    path = build_transcript_path(tmp_path)
    records = [
        *build_tool_invocation_pair(
            tool_use_id="toolu_1", message_id="msg_1", result_content="file contents"
        ),
        build_unmatched_invocation(tool_use_id="toolu_2", message_id="msg_2"),
    ]
    _write(path, build_transcript_text(records))

    facts = parse_transcript(path)

    resolved = next(e for e in facts.tool_events if e.result_size is not None)
    unresolved = next(e for e in facts.tool_events if e.result_size is None)
    assert resolved.result_size == len("file contents")
    assert unresolved.is_error is False
    assert unresolved.denial_kind is None


def test_tool_event_row_count_equals_invocation_count_with_no_filtering(tmp_path: Path) -> None:
    path = build_transcript_path(tmp_path)
    records = [
        *build_tool_invocation_pair(
            tool_use_id="toolu_1",
            message_id="msg_1",
            assistant_uuid="uuid-a1",
            result_uuid="uuid-r1",
            is_error=True,
            result_content="boom",
        ),
        *build_tool_invocation_pair(
            tool_use_id="toolu_2",
            message_id="msg_2",
            assistant_uuid="uuid-a2",
            parent_uuid="uuid-r1",
            result_uuid="uuid-r2",
        ),
        build_unmatched_invocation(
            tool_use_id="toolu_3", message_id="msg_3", parent_uuid="uuid-r2"
        ),
    ]
    _write(path, build_transcript_text(records))

    facts = parse_transcript(path)

    assert len(facts.tool_events) == facts.session.n_invocations == 3


def test_sidecar_agent_type_wins_and_is_recorded(tmp_path: Path) -> None:
    path = build_transcript_path(tmp_path)
    _write(path, build_transcript_text(build_tool_invocation_pair()))
    sidecar_path = path.with_suffix(".meta.json")
    sidecar_path.write_text(json.dumps(build_sidecar(agent_type="implementer")))

    facts = parse_transcript(path)

    assert facts.session.agent_type == "implementer"
    assert facts.session.name_source == NameSource.META_JSON


def test_fallback_used_and_recorded_when_sidecar_is_absent_and_session_is_not_dropped(
    tmp_path: Path,
) -> None:
    path = build_transcript_path(tmp_path)
    _write(path, build_transcript_text(build_tool_invocation_pair()))

    facts = parse_transcript(path)

    assert facts.session.name_source == NameSource.AGENT_ID_HASH
    assert facts.session.agent_type != ""


def test_some_unreadable_lines_are_ingested_with_the_count_reported(tmp_path: Path) -> None:
    path = build_transcript_path(tmp_path)
    text = build_transcript_text(build_tool_invocation_pair()) + build_unparseable_line() + "\n"
    _write(path, text)

    facts = parse_transcript(path)

    assert facts.session.unreadable_line_count == 1
    assert facts.session.n_invocations == 1


def test_transcript_with_nothing_usable_is_rejected(tmp_path: Path) -> None:
    path = build_transcript_path(tmp_path)
    _write(path, build_unparseable_line() + "\n")

    with pytest.raises(MalformedSourceError):
        parse_transcript(path)


def test_empty_file_is_rejected(tmp_path: Path) -> None:
    path = build_transcript_path(tmp_path)
    _write(path, "")

    with pytest.raises(MalformedSourceError):
        parse_transcript(path)


def test_transcript_with_exactly_one_invocation(tmp_path: Path) -> None:
    path = build_transcript_path(tmp_path)
    _write(path, build_transcript_text(build_tool_invocation_pair()))

    facts = parse_transcript(path)

    assert facts.session.n_invocations == 1
    assert len(facts.tool_events) == 1


def test_repeated_identical_invocations_are_counted(tmp_path: Path) -> None:
    path = build_transcript_path(tmp_path)
    records = [
        *build_tool_invocation_pair(
            tool_use_id="toolu_1",
            message_id="msg_1",
            assistant_uuid="uuid-a1",
            result_uuid="uuid-r1",
        ),
        *build_tool_invocation_pair(
            tool_use_id="toolu_2",
            message_id="msg_2",
            assistant_uuid="uuid-a2",
            parent_uuid="uuid-r1",
            result_uuid="uuid-r2",
        ),
    ]
    _write(path, build_transcript_text(records))

    facts = parse_transcript(path)

    assert facts.session.n_repeated_invocations == 1


def test_fragmented_turn_counts_as_one_turn_with_trailing_fragment_token_totals(
    tmp_path: Path,
) -> None:
    path = build_transcript_path(tmp_path)
    fragments = build_fragmented_turn()
    result = build_user_record(
        parent_uuid="uuid-fragment-tool-use",
        content=[build_tool_result_block(tool_use_id="toolu_fragment", content="done")],
    )
    _write(path, build_transcript_text([*fragments, result]))

    facts = parse_transcript(path)

    assert facts.session.n_turns == 1
    assert facts.session.input_tokens == 500
    assert facts.session.output_tokens == 120
    assert facts.session.cache_read_tokens == 300


def test_is_error_present_and_true_is_counted_as_an_error(tmp_path: Path) -> None:
    path = build_transcript_path(tmp_path)
    records = build_tool_invocation_pair(is_error=True, result_content="boom")
    _write(path, build_transcript_text(records))

    facts = parse_transcript(path)

    assert facts.tool_events[0].is_error is True
    assert facts.session.n_errors == 1


def test_successful_result_with_is_error_absent_is_not_counted_as_an_error(
    tmp_path: Path,
) -> None:
    path = build_transcript_path(tmp_path)
    records = build_tool_invocation_pair(is_error=False, result_content="ok")
    _write(path, build_transcript_text(records))

    facts = parse_transcript(path)

    assert facts.tool_events[0].is_error is False
    assert facts.session.n_errors == 0


def test_result_size_for_string_content(tmp_path: Path) -> None:
    path = build_transcript_path(tmp_path)
    records = build_tool_invocation_pair(result_content="hello world")
    _write(path, build_transcript_text(records))

    facts = parse_transcript(path)

    assert facts.tool_events[0].result_size == len("hello world")


def test_distinct_files_and_duration_are_derived_across_varied_invocations(
    tmp_path: Path,
) -> None:
    path = build_transcript_path(tmp_path)
    first_assistant = build_assistant_record(
        uuid="uuid-a1",
        parent_uuid="uuid-user-0",
        message_id="msg_1",
        content=[
            build_tool_use_block(tool_use_id="toolu_1", input={"file_path": "/workspace/one.txt"})
        ],
        stop_reason="tool_use",
        timestamp="2026-01-01T00:00:00.000Z",
    )
    first_result = build_user_record(
        uuid="uuid-r1",
        parent_uuid="uuid-a1",
        content=[build_tool_result_block(tool_use_id="toolu_1", content="one")],
        timestamp="2026-01-01T00:00:01.000Z",
    )
    second_assistant = build_assistant_record(
        uuid="uuid-a2",
        parent_uuid="uuid-r1",
        message_id="msg_2",
        content=[
            build_tool_use_block(tool_use_id="toolu_2", input={"file_path": "/workspace/two.txt"})
        ],
        stop_reason="tool_use",
        timestamp="2026-01-01T00:00:30.000Z",
    )
    second_result = build_user_record(
        uuid="uuid-r2",
        parent_uuid="uuid-a2",
        content=[build_tool_result_block(tool_use_id="toolu_2", content="two")],
        timestamp="2026-01-01T00:01:30.000Z",
    )
    _write(
        path,
        build_transcript_text([first_assistant, first_result, second_assistant, second_result]),
    )

    facts = parse_transcript(path)

    assert facts.session.n_distinct_files == 2
    assert facts.session.duration_ms == 90_000


def test_duration_ms_spans_the_earliest_and_latest_record_timestamps(tmp_path: Path) -> None:
    path = build_transcript_path(tmp_path)
    assistant = build_assistant_record(
        uuid="uuid-a1",
        parent_uuid="uuid-user-0",
        message_id="msg_1",
        content=[build_tool_use_block(tool_use_id="toolu_1")],
        stop_reason="tool_use",
        timestamp="2026-01-01T00:00:00.000Z",
    )
    result = build_user_record(
        uuid="uuid-r1",
        parent_uuid="uuid-a1",
        content=[build_tool_result_block(tool_use_id="toolu_1", content="done")],
        timestamp="2026-01-01T00:00:05.500Z",
    )
    _write(path, build_transcript_text([assistant, result]))

    facts = parse_transcript(path)

    assert facts.session.duration_ms == 5500


def test_result_size_for_array_of_text_blocks(tmp_path: Path) -> None:
    path = build_transcript_path(tmp_path)
    blocks: list[dict[str, object]] = [
        {"type": "text", "text": "hello"},
        {"type": "text", "text": "world!"},
    ]
    records = build_tool_invocation_pair(result_content=blocks)
    _write(path, build_transcript_text(records))

    facts = parse_transcript(path)

    assert facts.tool_events[0].result_size == len("hello") + len("world!")


def test_agent_id_is_read_from_the_agentid_record_field(tmp_path: Path) -> None:
    path = build_transcript_path(tmp_path, raw_session_id="raw-id-on-filename")
    records = [
        {**record, "agentId": "agent-id-from-record"} for record in build_tool_invocation_pair()
    ]
    _write(path, build_transcript_text(records))

    facts = parse_transcript(path)

    assert facts.session.agent_id == "agent-id-from-record"


def test_agent_id_falls_back_to_the_filename_derived_raw_session_id_when_absent(
    tmp_path: Path,
) -> None:
    path = build_transcript_path(tmp_path, raw_session_id="fallback-agent")
    records = [
        {key: value for key, value in record.items() if key != "agentId"}
        for record in build_tool_invocation_pair()
    ]
    _write(path, build_transcript_text(records))

    facts = parse_transcript(path)

    assert facts.session.agent_id == "fallback-agent"


def test_parent_session_id_is_qualified_from_the_directory_above_subagents(
    tmp_path: Path,
) -> None:
    path = build_transcript_path(
        tmp_path, project="project-one", parent_session_id="raw-parent-xyz"
    )
    _write(path, build_transcript_text(build_tool_invocation_pair()))

    facts = parse_transcript(path)

    expected = canonical_json_fingerprint(["project-one", SessionKind.MAIN.value, "raw-parent-xyz"])
    assert facts.session.parent_session_id == expected


def test_task_prompt_len_is_the_character_length_of_the_sidecar_description(
    tmp_path: Path,
) -> None:
    path = build_transcript_path(tmp_path)
    _write(path, build_transcript_text(build_tool_invocation_pair()))
    sidecar_path = path.with_suffix(".meta.json")
    sidecar_path.write_text(json.dumps(build_sidecar(description="Ship the fix")))

    facts = parse_transcript(path)

    assert facts.session.task_prompt_len == len("Ship the fix")


def test_task_prompt_len_is_zero_when_no_sidecar_supplies_a_description(tmp_path: Path) -> None:
    path = build_transcript_path(tmp_path)
    _write(path, build_transcript_text(build_tool_invocation_pair()))

    facts = parse_transcript(path)

    assert facts.session.task_prompt_len == 0


def test_started_at_is_the_earliest_record_timestamp(tmp_path: Path) -> None:
    path = build_transcript_path(tmp_path)
    assistant = build_assistant_record(
        uuid="uuid-a1",
        parent_uuid="uuid-user-0",
        message_id="msg_1",
        content=[build_tool_use_block(tool_use_id="toolu_1")],
        stop_reason="tool_use",
        timestamp="2026-01-01T00:00:05.000Z",
    )
    result = build_user_record(
        uuid="uuid-r1",
        parent_uuid="uuid-a1",
        content=[build_tool_result_block(tool_use_id="toolu_1", content="done")],
        timestamp="2026-01-01T00:00:00.000Z",
    )
    _write(path, build_transcript_text([assistant, result]))

    facts = parse_transcript(path)

    assert facts.session.started_at == datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)


def test_transcript_with_no_timestamped_record_is_rejected(tmp_path: Path) -> None:
    path = build_transcript_path(tmp_path)
    _write(path, build_transcript_text([{"type": "summary", "uuid": "uuid-summary"}]))

    with pytest.raises(MalformedSourceError):
        parse_transcript(path)
