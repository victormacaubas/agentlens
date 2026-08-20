"""Tests for agentlens.judge.transcript_view."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

import pytest

from agentlens.judge.transcript_view import (
    NO_FINAL_REPORT,
    TRUNCATION_MARKER,
    build_transcript_view,
)
from agentlens.parser.session import ParsedSession
from agentlens.store.models import ToolEventRecord

MAX_VIEW_SIZE_BYTES = 20 * 1024


def _write_jsonl(path: Path, records: list[dict[str, object]]) -> None:
    lines = [json.dumps(record) for record in records]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_jsonl_stream(path: Path, records: Iterable[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8") as destination:
        for record in records:
            destination.write(json.dumps(record))
            destination.write("\n")


def _parsed_session(**overrides: object) -> ParsedSession:
    defaults: dict[str, object] = {
        "session_id": "test-session",
        "session_kind": "subagent",
        "agent_id": "agent-abc123",
        "name": "implementer",
        "name_source": "meta_agent_type",
        "ambiguous": False,
        "parent_session_id": "parent-123",
        "spawn_tool_use_id": "toolu_xyz",
        "task_description": "Test task",
        "spawn_depth": 1,
        "events": [
            ToolEventRecord(
                session_id="test-session",
                seq=0,
                tool_name="Read",
                is_error=False,
                denial_kind=None,
                ts="2026-01-01T00:00:00Z",
                input_hash="abc",
                output_bytes=100,
            )
        ],
        "n_turns": 2,
        "duration_sec": 10.5,
        "first_ts": "2026-01-01T00:00:00Z",
        "input_tokens": 1000,
        "output_tokens": 500,
        "cache_read_tokens": 800,
        "cache_creation_tokens": 200,
        "fired_skills": ["code-review"],
        "final_report_flagged_partial": False,
    }
    defaults.update(overrides)
    return ParsedSession(**defaults)  # type: ignore[arg-type]


def _tool_use_record(
    tool_use_id: str, name: str, tool_input: dict[str, object]
) -> dict[str, object]:
    return {
        "type": "assistant",
        "message": {
            "content": [
                {"type": "tool_use", "id": tool_use_id, "name": name, "input": tool_input}
            ]
        },
    }


def _tool_result_record(
    tool_use_id: str,
    content: object,
    *,
    is_error: bool = False,
    denial_kind: str | None = None,
) -> dict[str, object]:
    record: dict[str, object] = {
        "type": "user",
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": content,
                    "is_error": is_error,
                }
            ]
        },
    }
    if denial_kind is not None:
        record["toolDenialKind"] = denial_kind
    return record


def _final_text_record(text: str) -> dict[str, object]:
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}


def _first_user_record(text: str) -> dict[str, object]:
    return {"type": "user", "message": {"content": [{"type": "text", "text": text}]}}


def test_view_from_synthetic_session(tmp_path: Path) -> None:
    jsonl_path = tmp_path / "session.jsonl"
    _write_jsonl(
        jsonl_path,
        [
            _first_user_record("Please refactor the parser module."),
            _tool_use_record("tu_1", "Read", {"file_path": "src/foo.py"}),
            _tool_result_record("tu_1", "file contents here"),
            _final_text_record("## Summary\nDone!"),
        ],
    )
    parsed = _parsed_session()

    view = build_transcript_view(parsed, jsonl_path)

    for header in (
        "## Task",
        "## Agent Identity",
        "## Deterministic Facts",
        "## Tool Sequence",
        "## Errors & Denials",
        "## Final Report",
    ):
        assert header in view


def test_task_description_truncation(tmp_path: Path) -> None:
    jsonl_path = tmp_path / "session.jsonl"
    long_task = "x" * 2500
    _write_jsonl(
        jsonl_path,
        [
            _first_user_record(long_task),
            _final_text_record("done"),
        ],
    )
    parsed = _parsed_session()

    view = build_transcript_view(parsed, jsonl_path)

    task_section = view.split("## Agent Identity")[0]
    assert ("x" * 2000) in task_section
    assert TRUNCATION_MARKER in task_section
    assert ("x" * 2001) not in task_section


def test_missing_final_report(tmp_path: Path) -> None:
    jsonl_path = tmp_path / "session.jsonl"
    _write_jsonl(
        jsonl_path,
        [
            _first_user_record("Do the thing."),
            _tool_use_record("tu_1", "Read", {"file_path": "src/foo.py"}),
            _tool_result_record("tu_1", "file contents here"),
        ],
    )
    parsed = _parsed_session()

    view = build_transcript_view(parsed, jsonl_path)

    final_report_section = view.split("## Final Report")[1]
    assert NO_FINAL_REPORT in final_report_section


def test_error_excerpts(tmp_path: Path) -> None:
    jsonl_path = tmp_path / "session.jsonl"
    error_output = "E" * 500
    _write_jsonl(
        jsonl_path,
        [
            _first_user_record("Do the thing."),
            _tool_use_record("tu_1", "Bash", {"command": "run-tests"}),
            _tool_result_record("tu_1", error_output, is_error=True),
            _final_text_record("done"),
        ],
    )
    parsed = _parsed_session()

    view = build_transcript_view(parsed, jsonl_path)

    errors_section = view.split("## Errors & Denials")[1].split("## Final Report")[0]
    assert ("E" * 300) in errors_section
    assert ("E" * 301) not in errors_section


def test_bash_command_truncation(tmp_path: Path) -> None:
    jsonl_path = tmp_path / "session.jsonl"
    long_command = "echo " + "a" * 200
    _write_jsonl(
        jsonl_path,
        [
            _first_user_record("Run a command."),
            _tool_use_record("tu_1", "Bash", {"command": long_command}),
            _tool_result_record("tu_1", "output", is_error=False),
            _final_text_record("done"),
        ],
    )
    parsed = _parsed_session()

    view = build_transcript_view(parsed, jsonl_path)

    tool_sequence_section = view.split("## Tool Sequence")[1].split("## Errors & Denials")[0]
    assert long_command[:120] in tool_sequence_section
    assert long_command not in tool_sequence_section


def test_large_final_report_stays_under_budget(tmp_path: Path) -> None:
    jsonl_path = tmp_path / "session.jsonl"
    huge_report = "x" * 1_000_000
    _write_jsonl(
        jsonl_path,
        [
            _first_user_record("Do the thing."),
            _tool_use_record("tu_1", "Read", {"file_path": "src/foo.py"}),
            _tool_result_record("tu_1", "file contents here"),
            _final_text_record(huge_report),
        ],
    )
    parsed = _parsed_session()

    view = build_transcript_view(parsed, jsonl_path)

    assert len(view.encode("utf-8")) <= MAX_VIEW_SIZE_BYTES
    assert TRUNCATION_MARKER in view
    for header in (
        "## Task",
        "## Agent Identity",
        "## Deterministic Facts",
        "## Tool Sequence",
        "## Errors & Denials",
        "## Final Report",
    ):
        assert header in view


def test_huge_tool_history_stays_under_budget(tmp_path: Path) -> None:
    n_tool_calls = 500
    jsonl_path = tmp_path / "session.jsonl"
    records: list[dict[str, object]] = [_first_user_record("Do a huge multi-step task.")]
    events: list[ToolEventRecord] = []
    for i in range(n_tool_calls):
        tool_use_id = f"tu_{i}"
        records.append(_tool_use_record(tool_use_id, "Read", {"file_path": f"src/file_{i}.py"}))
        if i == 250:
            records.append(
                _tool_result_record(tool_use_id, "boom: permission denied", is_error=True)
            )
        else:
            records.append(_tool_result_record(tool_use_id, f"contents of file {i}"))
        events.append(
            ToolEventRecord(
                session_id="test-session",
                seq=i,
                tool_name="Read",
                is_error=(i == 250),
                denial_kind=None,
                ts="2026-01-01T00:00:00Z",
                input_hash=f"hash{i}",
                output_bytes=100,
            )
        )
    records.append(_final_text_record("## Summary\nAll files reviewed."))
    _write_jsonl(jsonl_path, records)
    parsed = _parsed_session(events=events)

    view = build_transcript_view(parsed, jsonl_path)

    assert len(view.encode("utf-8")) <= MAX_VIEW_SIZE_BYTES
    for header in (
        "## Task",
        "## Agent Identity",
        "## Deterministic Facts",
        "## Tool Sequence",
        "## Errors & Denials",
        "## Final Report",
    ):
        assert header in view
    errors_section = view.split("## Errors & Denials")[1].split("## Final Report")[0]
    assert "boom: permission denied" in errors_section


def test_errors_preserved_in_truncated_view(tmp_path: Path) -> None:
    n_tool_calls = 500
    jsonl_path = tmp_path / "session.jsonl"
    records: list[dict[str, object]] = [_first_user_record("Do a huge multi-step task.")]
    events: list[ToolEventRecord] = []
    for i in range(n_tool_calls):
        tool_use_id = f"tu_{i}"
        records.append(_tool_use_record(tool_use_id, "Read", {"file_path": f"src/file_{i}.py"}))
        if i == 250:
            records.append(
                _tool_result_record(tool_use_id, "distinctive-error-marker-250", is_error=True)
            )
        else:
            records.append(_tool_result_record(tool_use_id, f"contents of file {i}"))
        events.append(
            ToolEventRecord(
                session_id="test-session",
                seq=i,
                tool_name="Read",
                is_error=(i == 250),
                denial_kind=None,
                ts="2026-01-01T00:00:00Z",
                input_hash=f"hash{i}",
                output_bytes=100,
            )
        )
    records.append(_final_text_record("## Summary\nAll files reviewed."))
    _write_jsonl(jsonl_path, records)
    parsed = _parsed_session(events=events)

    view = build_transcript_view(parsed, jsonl_path)

    errors_section = view.split("## Errors & Denials")[1].split("## Final Report")[0]
    assert "distinctive-error-marker-250" in errors_section


def test_view_size_reasonable(tmp_path: Path) -> None:
    n_tool_calls = 45
    jsonl_path = tmp_path / "session.jsonl"
    records: list[dict[str, object]] = [_first_user_record("Do a large multi-step task.")]
    events: list[ToolEventRecord] = []
    for i in range(n_tool_calls):
        tool_use_id = f"tu_{i}"
        records.append(_tool_use_record(tool_use_id, "Read", {"file_path": f"src/file_{i}.py"}))
        records.append(_tool_result_record(tool_use_id, f"contents of file {i}"))
        events.append(
            ToolEventRecord(
                session_id="test-session",
                seq=i,
                tool_name="Read",
                is_error=False,
                denial_kind=None,
                ts="2026-01-01T00:00:00Z",
                input_hash=f"hash{i}",
                output_bytes=100,
            )
        )
    records.append(_final_text_record("## Summary\nAll files reviewed."))
    _write_jsonl(jsonl_path, records)
    parsed = _parsed_session(events=events)

    view = build_transcript_view(parsed, jsonl_path)

    assert len(view.encode("utf-8")) < MAX_VIEW_SIZE_BYTES
    tool_sequence_section = view.split("## Tool Sequence")[1].split("## Errors & Denials")[0]
    assert tool_sequence_section.count("Read src/file_") == n_tool_calls


def test_hundreds_of_errors_keep_total_and_head_tail_steps(tmp_path: Path) -> None:
    error_count = 300
    jsonl_path = tmp_path / "errors.jsonl"

    def records() -> Iterable[dict[str, object]]:
        yield _first_user_record("Exercise error sampling.")
        for index in range(1, error_count + 1):
            tool_use_id = f"tu_{index}"
            yield _tool_use_record(tool_use_id, "Bash", {"command": f"command-{index}"})
            yield _tool_result_record(
                tool_use_id,
                f"error-{index}-" + ("x" * 1000),
                is_error=True,
                denial_kind="permission-rule" if index % 2 == 0 else None,
            )
        yield _final_text_record("Recorded all failures.")

    _write_jsonl_stream(jsonl_path, records())

    view = build_transcript_view(_parsed_session(), jsonl_path)

    errors = view.split("## Errors & Denials")[1].split("## Final Report")[0]
    assert "Total errors/denials: 300" in errors
    assert "- [step 1]" in errors
    assert "- [step 5]" in errors
    assert "- [step 296]" in errors
    assert "- [step 300]" in errors
    assert "- [step 6]" not in errors
    assert TRUNCATION_MARKER in errors
    assert len(view.encode("utf-8")) <= MAX_VIEW_SIZE_BYTES


def test_multibyte_text_and_oversized_fixed_sections_keep_headers(tmp_path: Path) -> None:
    jsonl_path = tmp_path / "unicode.jsonl"
    _write_jsonl(
        jsonl_path,
        [
            _first_user_record("界" * 3000),
            _final_text_record("🚀" * 100_000),
        ],
    )
    parsed = _parsed_session(
        name="界" * 100_000,
        parent_session_id="親" * 100_000,
    )

    view = build_transcript_view(parsed, jsonl_path)

    assert len(view.encode("utf-8")) <= MAX_VIEW_SIZE_BYTES
    assert TRUNCATION_MARKER in view
    for header in (
        "## Task",
        "## Agent Identity",
        "## Deterministic Facts",
        "## Tool Sequence",
        "## Errors & Denials",
        "## Final Report",
    ):
        assert view.count(header) == 1


def test_ten_thousand_tool_calls_and_large_result_body_are_sampled(tmp_path: Path) -> None:
    tool_count = 10_000
    jsonl_path = tmp_path / "many-tools.jsonl"

    def records() -> Iterable[dict[str, object]]:
        yield _first_user_record("Inspect many files.")
        for index in range(1, tool_count + 1):
            tool_use_id = f"tu_{index}"
            yield _tool_use_record(
                tool_use_id,
                "Read",
                {"file_path": f"src/file_{index}.py"},
            )
            body = "z" * 2_000_000 if index == 5000 else "ok"
            yield _tool_result_record(tool_use_id, body)
        yield _final_text_record("Inspection complete.")

    _write_jsonl_stream(jsonl_path, records())

    view = build_transcript_view(_parsed_session(), jsonl_path)

    tools = view.split("## Tool Sequence")[1].split("## Errors & Denials")[0]
    assert "Total tool calls: 10000" in tools
    assert "1. Read src/file_1.py" in tools
    assert "10000. Read src/file_10000.py" in tools
    assert f"{TRUNCATION_MARKER} (9950 tool calls omitted)" in tools
    assert len(view.encode("utf-8")) <= MAX_VIEW_SIZE_BYTES


def test_pending_tool_pair_state_is_bounded_and_reported(tmp_path: Path) -> None:
    jsonl_path = tmp_path / "pending-tools.jsonl"
    _write_jsonl_stream(
        jsonl_path,
        (
            _tool_use_record(
                f"tu_{index}",
                "Read",
                {"file_path": f"src/file_{index}.py"},
            )
            for index in range(4097)
        ),
    )

    view = build_transcript_view(_parsed_session(), jsonl_path)

    tools = view.split("## Tool Sequence")[1].split("## Errors & Denials")[0]
    assert "Total tool calls: 0" in tools
    assert "Pending tool pairs evicted: 1" in tools


def test_invalid_utf8_transcript_raises_unicode_error(tmp_path: Path) -> None:
    jsonl_path = tmp_path / "invalid-utf8.jsonl"
    jsonl_path.write_bytes(b"\x80\x81\x82")

    with pytest.raises(UnicodeError):
        build_transcript_view(_parsed_session(), jsonl_path)
