"""Parsing a transcript into tool-event rows and one session row."""

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentlens.errors import MalformedSourceError
from agentlens.ingest.context import SubagentContextCache
from agentlens.ingest.name_resolution import resolve_agent_type
from agentlens.ingest.transcript import parse_transcript
from agentlens.models.identity import NameSource, SessionKind
from agentlens.models.skill_signals import KnownState
from agentlens.utils.hashing import canonical_json_fingerprint, hash_text
from tests.factories import (
    build_agent_definition_text,
    build_agent_tool_use_block,
    build_assistant_record,
    build_context_cache,
    build_fragmented_turn,
    build_main_session_path,
    build_sidecar,
    build_skill_md_text,
    build_subagent_source_bundle,
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

    facts = parse_transcript(
        build_subagent_source_bundle(transcript_path=path), context_cache=build_context_cache()
    )

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

    facts = parse_transcript(
        build_subagent_source_bundle(transcript_path=path), context_cache=build_context_cache()
    )

    assert len(facts.tool_events) == facts.session.n_invocations == 3


def test_sidecar_agent_type_wins_and_is_recorded(tmp_path: Path) -> None:
    path = build_transcript_path(tmp_path)
    _write(path, build_transcript_text(build_tool_invocation_pair()))
    sidecar_path = path.with_suffix(".meta.json")
    sidecar_path.write_text(json.dumps(build_sidecar(agent_type="implementer")))

    facts = parse_transcript(
        build_subagent_source_bundle(transcript_path=path), context_cache=build_context_cache()
    )

    assert facts.session.agent_type == "implementer"
    assert facts.session.name_source == NameSource.META_JSON


def test_fallback_used_and_recorded_when_sidecar_is_absent_and_session_is_not_dropped(
    tmp_path: Path,
) -> None:
    path = build_transcript_path(tmp_path)
    _write(path, build_transcript_text(build_tool_invocation_pair()))

    facts = parse_transcript(
        build_subagent_source_bundle(transcript_path=path), context_cache=build_context_cache()
    )

    assert facts.session.name_source == NameSource.AGENT_ID_HASH
    assert facts.session.agent_type != ""


def test_some_unreadable_lines_are_ingested_with_the_count_reported(tmp_path: Path) -> None:
    path = build_transcript_path(tmp_path)
    text = build_transcript_text(build_tool_invocation_pair()) + build_unparseable_line() + "\n"
    _write(path, text)

    facts = parse_transcript(
        build_subagent_source_bundle(transcript_path=path), context_cache=build_context_cache()
    )

    assert facts.session.unreadable_line_count == 1
    assert facts.session.n_invocations == 1


def test_transcript_with_nothing_usable_is_rejected(tmp_path: Path) -> None:
    path = build_transcript_path(tmp_path)
    _write(path, build_unparseable_line() + "\n")

    with pytest.raises(MalformedSourceError):
        parse_transcript(
            build_subagent_source_bundle(transcript_path=path), context_cache=build_context_cache()
        )


def test_empty_file_is_rejected(tmp_path: Path) -> None:
    path = build_transcript_path(tmp_path)
    _write(path, "")

    with pytest.raises(MalformedSourceError):
        parse_transcript(
            build_subagent_source_bundle(transcript_path=path), context_cache=build_context_cache()
        )


def test_transcript_with_exactly_one_invocation(tmp_path: Path) -> None:
    path = build_transcript_path(tmp_path)
    _write(path, build_transcript_text(build_tool_invocation_pair()))

    facts = parse_transcript(
        build_subagent_source_bundle(transcript_path=path), context_cache=build_context_cache()
    )

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

    facts = parse_transcript(
        build_subagent_source_bundle(transcript_path=path), context_cache=build_context_cache()
    )

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

    facts = parse_transcript(
        build_subagent_source_bundle(transcript_path=path), context_cache=build_context_cache()
    )

    assert facts.session.n_turns == 1
    assert facts.session.input_tokens == 500
    assert facts.session.output_tokens == 120
    assert facts.session.cache_read_tokens == 300


def test_is_error_present_and_true_is_counted_as_an_error(tmp_path: Path) -> None:
    path = build_transcript_path(tmp_path)
    records = build_tool_invocation_pair(is_error=True, result_content="boom")
    _write(path, build_transcript_text(records))

    facts = parse_transcript(
        build_subagent_source_bundle(transcript_path=path), context_cache=build_context_cache()
    )

    assert facts.tool_events[0].is_error is True
    assert facts.session.n_errors == 1


def test_successful_result_with_is_error_absent_is_not_counted_as_an_error(
    tmp_path: Path,
) -> None:
    path = build_transcript_path(tmp_path)
    records = build_tool_invocation_pair(is_error=False, result_content="ok")
    _write(path, build_transcript_text(records))

    facts = parse_transcript(
        build_subagent_source_bundle(transcript_path=path), context_cache=build_context_cache()
    )

    assert facts.tool_events[0].is_error is False
    assert facts.session.n_errors == 0


def test_result_size_for_string_content(tmp_path: Path) -> None:
    path = build_transcript_path(tmp_path)
    records = build_tool_invocation_pair(result_content="hello world")
    _write(path, build_transcript_text(records))

    facts = parse_transcript(
        build_subagent_source_bundle(transcript_path=path), context_cache=build_context_cache()
    )

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

    facts = parse_transcript(
        build_subagent_source_bundle(transcript_path=path), context_cache=build_context_cache()
    )

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

    facts = parse_transcript(
        build_subagent_source_bundle(transcript_path=path), context_cache=build_context_cache()
    )

    assert facts.session.duration_ms == 5500


def test_result_size_for_array_of_text_blocks(tmp_path: Path) -> None:
    path = build_transcript_path(tmp_path)
    blocks: list[dict[str, object]] = [
        {"type": "text", "text": "hello"},
        {"type": "text", "text": "world!"},
    ]
    records = build_tool_invocation_pair(result_content=blocks)
    _write(path, build_transcript_text(records))

    facts = parse_transcript(
        build_subagent_source_bundle(transcript_path=path), context_cache=build_context_cache()
    )

    assert facts.tool_events[0].result_size == len("hello") + len("world!")


def test_agent_id_is_read_from_the_agentid_record_field(tmp_path: Path) -> None:
    path = build_transcript_path(tmp_path, raw_session_id="raw-id-on-filename")
    records = [
        {**record, "agentId": "agent-id-from-record"} for record in build_tool_invocation_pair()
    ]
    _write(path, build_transcript_text(records))

    facts = parse_transcript(
        build_subagent_source_bundle(transcript_path=path), context_cache=build_context_cache()
    )

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

    facts = parse_transcript(
        build_subagent_source_bundle(transcript_path=path), context_cache=build_context_cache()
    )

    assert facts.session.agent_id == "fallback-agent"


def test_parent_session_id_is_qualified_from_the_directory_above_subagents(
    tmp_path: Path,
) -> None:
    path = build_transcript_path(
        tmp_path, project="project-one", parent_session_id="raw-parent-xyz"
    )
    _write(path, build_transcript_text(build_tool_invocation_pair()))

    facts = parse_transcript(
        build_subagent_source_bundle(transcript_path=path), context_cache=build_context_cache()
    )

    expected = canonical_json_fingerprint(["project-one", SessionKind.MAIN.value, "raw-parent-xyz"])
    assert facts.session.parent_session_id == expected


def test_task_prompt_len_is_the_character_length_of_the_sidecar_description(
    tmp_path: Path,
) -> None:
    path = build_transcript_path(tmp_path)
    _write(path, build_transcript_text(build_tool_invocation_pair()))
    sidecar_path = path.with_suffix(".meta.json")
    sidecar_path.write_text(json.dumps(build_sidecar(description="Ship the fix")))

    facts = parse_transcript(
        build_subagent_source_bundle(transcript_path=path), context_cache=build_context_cache()
    )

    assert facts.session.task_prompt_len == len("Ship the fix")


def test_task_prompt_len_is_zero_when_no_sidecar_supplies_a_description(tmp_path: Path) -> None:
    path = build_transcript_path(tmp_path)
    _write(path, build_transcript_text(build_tool_invocation_pair()))

    facts = parse_transcript(
        build_subagent_source_bundle(transcript_path=path), context_cache=build_context_cache()
    )

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

    facts = parse_transcript(
        build_subagent_source_bundle(transcript_path=path), context_cache=build_context_cache()
    )

    assert facts.session.started_at == datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)


def test_transcript_with_no_timestamped_record_is_rejected(tmp_path: Path) -> None:
    path = build_transcript_path(tmp_path)
    _write(path, build_transcript_text([{"type": "summary", "uuid": "uuid-summary"}]))

    with pytest.raises(MalformedSourceError):
        parse_transcript(
            build_subagent_source_bundle(transcript_path=path), context_cache=build_context_cache()
        )


@pytest.mark.parametrize(
    (
        "sidecar_agent_type",
        "attribution_agent_types",
        "parent_subagent_type",
        "expected_source",
    ),
    [
        pytest.param("implementer", frozenset(), None, NameSource.META_JSON, id="sidecar_only"),
        pytest.param(
            None,
            frozenset({"pathfinder"}),
            None,
            NameSource.ATTRIBUTION_AGENT,
            id="attribution_only",
        ),
        pytest.param(None, frozenset(), "researcher", NameSource.PARENT_TASK, id="parent_only"),
        pytest.param(
            "implementer",
            frozenset({"implementer"}),
            None,
            NameSource.META_JSON,
            id="sidecar_and_attribution_agree",
        ),
    ],
)
def test_resolve_agent_type_credits_the_highest_priority_supplying_link(
    sidecar_agent_type: str | None,
    attribution_agent_types: frozenset[str],
    parent_subagent_type: str | None,
    expected_source: NameSource,
) -> None:
    resolution = resolve_agent_type(
        sidecar_agent_type=sidecar_agent_type,
        attribution_agent_types=attribution_agent_types,
        parent_subagent_type=parent_subagent_type,
        raw_session_id="raw-id",
    )

    assert resolution.name_source == expected_source


def test_resolve_agent_type_falls_back_to_the_raw_id_hash_when_every_link_is_silent() -> None:
    resolution = resolve_agent_type(
        sidecar_agent_type=None,
        attribution_agent_types=frozenset(),
        parent_subagent_type=None,
        raw_session_id="raw-id-xyz",
    )

    assert resolution.name_source == NameSource.AGENT_ID_HASH
    assert resolution.agent_type == hash_text("raw-id-xyz")


@pytest.mark.parametrize(
    (
        "sidecar_agent_type",
        "attribution_agent_types",
        "parent_subagent_type",
        "expected_agent_type",
    ),
    [
        pytest.param(
            "implementer",
            frozenset({"pathfinder"}),
            None,
            "implementer",
            id="sidecar_beats_conflicting_attribution",
        ),
        pytest.param(
            None,
            frozenset({"pathfinder", "implementer"}),
            None,
            "implementer",
            id="attribution_disagrees_with_itself",
        ),
        pytest.param(
            None,
            frozenset({"pathfinder"}),
            "researcher",
            "pathfinder",
            id="attribution_beats_conflicting_parent",
        ),
    ],
)
def test_resolve_agent_type_marks_conflicts_ambiguous_with_a_deterministic_value(
    sidecar_agent_type: str | None,
    attribution_agent_types: frozenset[str],
    parent_subagent_type: str | None,
    expected_agent_type: str,
) -> None:
    resolution = resolve_agent_type(
        sidecar_agent_type=sidecar_agent_type,
        attribution_agent_types=attribution_agent_types,
        parent_subagent_type=parent_subagent_type,
        raw_session_id="raw-id",
    )

    assert resolution.name_source == NameSource.AMBIGUOUS
    assert resolution.agent_type == expected_agent_type


def test_attribution_supplies_the_name_when_sidecar_is_absent(tmp_path: Path) -> None:
    path = build_transcript_path(tmp_path)
    records = [
        *build_tool_invocation_pair(),
        build_assistant_record(
            uuid="uuid-attrib", message_id="msg-attrib", attribution_agent="pathfinder"
        ),
    ]
    _write(path, build_transcript_text(records))

    facts = parse_transcript(
        build_subagent_source_bundle(transcript_path=path), context_cache=build_context_cache()
    )

    assert facts.session.agent_type == "pathfinder"
    assert facts.session.name_source == NameSource.ATTRIBUTION_AGENT


def test_conflicting_attribution_values_are_marked_ambiguous(tmp_path: Path) -> None:
    path = build_transcript_path(tmp_path)
    records = [
        *build_tool_invocation_pair(),
        build_assistant_record(
            uuid="uuid-attrib-1", message_id="msg-attrib-1", attribution_agent="pathfinder"
        ),
        build_assistant_record(
            uuid="uuid-attrib-2", message_id="msg-attrib-2", attribution_agent="implementer"
        ),
    ]
    _write(path, build_transcript_text(records))

    facts = parse_transcript(
        build_subagent_source_bundle(transcript_path=path), context_cache=build_context_cache()
    )

    assert facts.session.name_source == NameSource.AMBIGUOUS
    assert facts.session.agent_type == "implementer"


@pytest.mark.parametrize(
    "spawning_tool_name", ["Agent", "Task"], ids=["current_name", "historical_name"]
)
def test_parent_spawning_invocation_supplies_the_name_when_sidecar_and_attribution_are_silent(
    tmp_path: Path, spawning_tool_name: str
) -> None:
    path = build_transcript_path(tmp_path)
    _write(path, build_transcript_text(build_tool_invocation_pair()))
    sidecar_path = path.with_suffix(".meta.json")
    sidecar_path.write_text(json.dumps(build_sidecar(agent_type="", tool_use_id="toolu_spawn")))

    parent_path = build_main_session_path(tmp_path)
    parent_record = build_assistant_record(
        content=[
            build_agent_tool_use_block(
                tool_use_id="toolu_spawn", name=spawning_tool_name, subagent_type="pathfinder"
            )
        ],
        stop_reason="tool_use",
    )
    _write(parent_path, build_transcript_text([parent_record]))

    facts = parse_transcript(
        build_subagent_source_bundle(transcript_path=path), context_cache=build_context_cache()
    )

    assert facts.session.agent_type == "pathfinder"
    assert facts.session.name_source == NameSource.PARENT_TASK


def test_depth_two_parent_evidence_is_read_from_the_sibling_subagent_transcript(
    tmp_path: Path,
) -> None:
    path = build_transcript_path(tmp_path)
    _write(path, build_transcript_text(build_tool_invocation_pair()))
    sidecar_path = path.with_suffix(".meta.json")
    sidecar_path.write_text(
        json.dumps(
            build_sidecar(
                agent_type="",
                tool_use_id="toolu_spawn",
                parent_agent_id="parent-agent-9",
                spawn_depth=2,
            )
        )
    )
    sibling_path = build_transcript_path(tmp_path, raw_session_id="parent-agent-9")
    sibling_record = build_assistant_record(
        content=[build_agent_tool_use_block(tool_use_id="toolu_spawn", subagent_type="pathfinder")],
        stop_reason="tool_use",
    )
    _write(sibling_path, build_transcript_text([sibling_record]))

    facts = parse_transcript(
        build_subagent_source_bundle(transcript_path=path), context_cache=build_context_cache()
    )

    assert facts.session.agent_type == "pathfinder"
    assert facts.session.name_source == NameSource.PARENT_TASK


def test_unavailable_parent_transcript_still_lets_the_session_be_ingested(tmp_path: Path) -> None:
    path = build_transcript_path(tmp_path)
    _write(path, build_transcript_text(build_tool_invocation_pair()))
    sidecar_path = path.with_suffix(".meta.json")
    sidecar_path.write_text(json.dumps(build_sidecar(agent_type="")))

    facts = parse_transcript(
        build_subagent_source_bundle(transcript_path=path), context_cache=build_context_cache()
    )

    assert facts.session.name_source == NameSource.AGENT_ID_HASH
    assert facts.session.agent_type != ""


def _write_project_definition(project_root: Path, *, mtime_epoch_seconds: float) -> Path:
    definition_path = project_root / ".claude" / "agents" / "implementer.md"
    _write(definition_path, build_agent_definition_text(name="implementer"))
    os.utime(definition_path, (mtime_epoch_seconds, mtime_epoch_seconds))
    return definition_path


def test_agent_definition_binds_through_the_wired_context_when_it_predates_the_spawn(
    tmp_path: Path,
) -> None:
    claude_root = tmp_path / "home" / ".claude"
    project_root = tmp_path / "project"
    _write_project_definition(
        project_root, mtime_epoch_seconds=datetime(2025, 1, 1, tzinfo=UTC).timestamp()
    )
    path = build_transcript_path(tmp_path)
    _write(path, build_transcript_text([build_user_record(cwd=str(project_root))]))
    sidecar_path = path.with_suffix(".meta.json")
    sidecar_path.write_text(json.dumps(build_sidecar(agent_type="implementer")))
    context_cache = SubagentContextCache(claude_root)

    facts = parse_transcript(
        build_subagent_source_bundle(transcript_path=path), context_cache=context_cache
    )

    expected_id = (
        context_cache.resolve(str(project_root))
        .effective_definitions["implementer"]
        .agent_definition_id
    )
    assert facts.session.agent_definition_id == expected_id


def test_agent_definition_binding_is_null_when_the_definition_postdates_the_spawn(
    tmp_path: Path,
) -> None:
    claude_root = tmp_path / "home" / ".claude"
    project_root = tmp_path / "project"
    _write_project_definition(
        project_root, mtime_epoch_seconds=datetime(2027, 1, 1, tzinfo=UTC).timestamp()
    )
    path = build_transcript_path(tmp_path)
    _write(path, build_transcript_text([build_user_record(cwd=str(project_root))]))
    sidecar_path = path.with_suffix(".meta.json")
    sidecar_path.write_text(json.dumps(build_sidecar(agent_type="implementer")))

    facts = parse_transcript(
        build_subagent_source_bundle(transcript_path=path),
        context_cache=SubagentContextCache(claude_root),
    )

    assert facts.session.agent_definition_id is None


def test_skill_inventory_wired_through_context_produces_real_fired_count(
    tmp_path: Path,
) -> None:
    claude_root = tmp_path / "home" / ".claude"
    project_root = tmp_path / "project"
    skill_path = project_root / ".claude" / "skills" / "example-skill" / "SKILL.md"
    _write(skill_path, build_skill_md_text(name="example-skill"))
    predates_spawn = datetime(2025, 1, 1, tzinfo=UTC).timestamp()
    os.utime(skill_path, (predates_spawn, predates_spawn))
    path = build_transcript_path(tmp_path)
    _write(
        path,
        build_transcript_text(
            [
                build_user_record(cwd=str(project_root)),
                build_assistant_record(attribution_skill="example-skill"),
            ]
        ),
    )

    facts = parse_transcript(
        build_subagent_source_bundle(transcript_path=path),
        context_cache=SubagentContextCache(claude_root),
    )

    assert facts.session.n_skills_fired == 1
    assert [signal.skill_name for signal in facts.skill_signals] == ["example-skill"]
    fired_signal = facts.skill_signals[0]
    assert fired_signal.fired is True
    assert fired_signal.available == KnownState.TRUE
