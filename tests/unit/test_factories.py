"""Smoke tests for the synthetic-transcript builders themselves.

These check that the fixtures are shaped the way ``design.md`` says real
transcripts are shaped. They do not test any ingest behavior; that belongs to
the tests written alongside the parser that consumes these builders.
"""

import json

from tests.factories import (
    build_denied_invocation,
    build_fact_session,
    build_fact_tool_event,
    build_fragmented_turn,
    build_session_facts,
    build_sidecar,
    build_tool_invocation_pair,
    build_tool_result_block,
    build_transcript_text,
    build_unmatched_invocation,
    build_unparseable_line,
)


def test_build_fact_tool_event_and_fact_session_round_trip_defaults() -> None:
    session = build_fact_session()
    event = build_fact_tool_event(session_id=session.identity.session_id)
    parsed = build_session_facts(session=session, tool_events=(event,))
    assert parsed.session is session
    assert parsed.tool_events == (event,)


def test_fragmented_turn_shares_one_message_id_across_records() -> None:
    fragments = build_fragmented_turn()
    message_ids = {fragment["message"]["id"] for fragment in fragments}  # type: ignore[index]
    assert message_ids == {"msg_fragmented"}
    assert len(fragments) == 3


def test_fragmented_turn_trailing_fragment_carries_the_resolved_usage() -> None:
    fragments = build_fragmented_turn()
    interior_usages = [fragment["message"]["usage"] for fragment in fragments[:-1]]  # type: ignore[index]
    trailing_usage = fragments[-1]["message"]["usage"]  # type: ignore[index]
    assert all(usage == interior_usages[0] for usage in interior_usages)
    assert trailing_usage != interior_usages[0]
    assert fragments[-1]["message"]["stop_reason"] == "tool_use"  # type: ignore[index]
    assert all(fragment["message"]["stop_reason"] is None for fragment in fragments[:-1])  # type: ignore[index]


def test_tool_result_block_omits_is_error_when_false() -> None:
    block = build_tool_result_block(is_error=False)
    assert "is_error" not in block


def test_tool_result_block_includes_is_error_when_true() -> None:
    block = build_tool_result_block(is_error=True)
    assert block["is_error"] is True


def test_tool_result_block_supports_both_content_shapes() -> None:
    string_block = build_tool_result_block(content="plain text")
    array_block = build_tool_result_block(content=[{"type": "text", "text": "chunked"}])
    assert isinstance(string_block["content"], str)
    assert isinstance(array_block["content"], list)


def test_tool_invocation_pair_links_result_to_its_invocation() -> None:
    assistant, result = build_tool_invocation_pair(tool_use_id="toolu_x")
    tool_use_block = assistant["message"]["content"][0]  # type: ignore[index]
    result_block = result["message"]["content"][0]  # type: ignore[index]
    assert tool_use_block["id"] == "toolu_x"
    assert result_block["tool_use_id"] == "toolu_x"


def test_denied_invocation_carries_tool_denial_kind_at_result_root() -> None:
    _assistant, result = build_denied_invocation(denial_kind="automode-blocked")
    assert result["toolDenialKind"] == "automode-blocked"
    assert "toolDenialKind" not in result["message"]["content"][0]  # type: ignore[index]


def test_unmatched_invocation_has_no_paired_result() -> None:
    record = build_unmatched_invocation()
    assert record["message"]["content"][0]["type"] == "tool_use"  # type: ignore[index]


def test_unparseable_line_fails_json_parsing() -> None:
    line = build_unparseable_line()
    try:
        json.loads(line)
    except json.JSONDecodeError:
        return
    raise AssertionError("expected the fixture line to fail JSON parsing")


def test_sidecar_omits_optional_keys_by_default() -> None:
    sidecar = build_sidecar()
    assert "parentAgentId" not in sidecar
    assert "model" not in sidecar


def test_sidecar_includes_optional_keys_when_given() -> None:
    sidecar = build_sidecar(parent_agent_id="agent-parent", model="claude-opus")
    assert sidecar["parentAgentId"] == "agent-parent"
    assert sidecar["model"] == "claude-opus"


def test_transcript_text_is_one_json_object_per_line() -> None:
    records = [build_unmatched_invocation(), build_unmatched_invocation(uuid="uuid-2")]
    text = build_transcript_text(records)
    lines = text.splitlines()
    assert len(lines) == len(records)
    for line in lines:
        json.loads(line)


def test_transcript_text_of_no_records_is_empty() -> None:
    assert build_transcript_text([]) == ""
