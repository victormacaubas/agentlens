"""Shape checks on the transcript builders in ``tests/factories.py``.

Asserts the source-format details a builder can get wrong while staying
internally consistent: a fragmented assistant turn's shared ``message.id`` and
deferred usage, ``is_error`` present only when true, both
``tool_result.content`` shapes, ``toolDenialKind`` at the record root, an
unparseable line that really fails to parse, and newline-delimited
serialization.
"""

import json

import pytest

from tests.factories import (
    build_denied_invocation,
    build_fragmented_turn,
    build_tool_result_block,
    build_transcript_text,
    build_unmatched_invocation,
    build_unparseable_line,
)


def test_fragmented_turn_shares_one_message_id_and_defers_resolved_usage() -> None:
    """Interior fragments repeat one id and one usage; only the last one resolves.

    ``ingest`` counts turns by distinct ``message.id`` and reads token usage off
    the trailing fragment. A builder that varied the id, or that let an interior
    fragment carry the resolved usage, would make every turn-count and token
    assertion in the suite agree with a shape the real source never emits.
    """
    fragments = build_fragmented_turn()
    interior = [fragment["message"]["usage"] for fragment in fragments[:-1]]  # type: ignore[index]

    assert len(fragments) == 3
    assert {fragment["message"]["id"] for fragment in fragments} == {  # type: ignore[index]
        "msg_fragmented"
    }
    assert all(usage == interior[0] for usage in interior)
    assert fragments[-1]["message"]["usage"] != interior[0]  # type: ignore[index]
    assert fragments[-1]["message"]["stop_reason"] == "tool_use"  # type: ignore[index]
    assert all(fragment["message"]["stop_reason"] is None for fragment in fragments[:-1])  # type: ignore[index]


def test_tool_result_block_omits_is_error_unless_it_is_true() -> None:
    """``is_error`` is present-and-true or absent, never present-and-false.

    The parser treats the key's presence as the signal, so a builder that always
    emitted it would hide a parser that read presence instead of value.
    """
    assert "is_error" not in build_tool_result_block(is_error=False)
    assert build_tool_result_block(is_error=True)["is_error"] is True


@pytest.mark.parametrize(
    ("content", "expected"),
    [("plain text", str), ([{"type": "text", "text": "chunked"}], list)],
    ids=["string_content", "array_content"],
)
def test_tool_result_block_carries_both_real_content_shapes(
    content: str | list[dict[str, object]], expected: type[object]
) -> None:
    """``tool_result.content`` is a bare string or an array of blocks, per design.md."""
    assert isinstance(build_tool_result_block(content=content)["content"], expected)


def test_denied_invocation_carries_tool_denial_kind_at_the_result_root() -> None:
    """``toolDenialKind`` sits on the record, not inside the content block.

    Getting this backwards is the easy mistake, and it would silently zero every
    ``n_denials`` aggregate rather than fail loudly.
    """
    _assistant, result = build_denied_invocation(denial_kind="automode-blocked")

    assert result["toolDenialKind"] == "automode-blocked"
    assert "toolDenialKind" not in result["message"]["content"][0]  # type: ignore[index]


def test_unparseable_line_really_fails_json_parsing() -> None:
    """The unreadable-line fixture must be unreadable, or its counter test is vacuous."""
    with pytest.raises(json.JSONDecodeError):
        json.loads(build_unparseable_line())


def test_transcript_text_is_newline_delimited_json_and_empty_for_no_records() -> None:
    """One JSON object per line, and no stray blank line when there are no records."""
    records = [build_unmatched_invocation(), build_unmatched_invocation(uuid="uuid-2")]

    lines = build_transcript_text(records).splitlines()

    assert len(lines) == len(records)
    assert [json.loads(line) for line in lines] == records
    assert build_transcript_text([]) == ""
