"""Tests for `agentlens.parser`: name resolution, defensive reads, event pairing.

Per design D7, these are fed plain dicts / hand-built fixtures under
`tmp_path` — no dependency on real logs and no strict row-content assertions
against real subagent transcripts (that validation is deferred to v2).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentlens.parser import (
    NAME_SOURCE_AGENT_ID_HASH,
    NAME_SOURCE_ATTRIBUTION,
    NAME_SOURCE_META,
    NAME_SOURCE_PARENT_TASK,
    SESSION_KIND_MAIN,
    SESSION_KIND_SUBAGENT,
    extract_task_subagent_types,
    extract_transcript_facts,
    parse_agent_definition,
    parse_main_session,
    parse_subagent_run,
    read_jsonl_records,
    resolve_name,
)

# --------------------------------------------------------------------------
# 5.1 / 5.2 — resolve_name fallback chain (plain dicts)
# --------------------------------------------------------------------------


def test_resolve_name_meta_agent_type_wins() -> None:
    resolution = resolve_name(
        meta_agent_type="researcher",
        attribution_agents=[],
        parent_task_subagent_type=None,
        agent_id="a1",
    )
    assert resolution.name == "researcher"
    assert resolution.name_source == NAME_SOURCE_META
    assert resolution.ambiguous is False


def test_resolve_name_falls_back_to_attribution_agent() -> None:
    resolution = resolve_name(
        meta_agent_type=None,
        attribution_agents=["implementer"],
        parent_task_subagent_type=None,
        agent_id="a1",
    )
    assert resolution.name == "implementer"
    assert resolution.name_source == NAME_SOURCE_ATTRIBUTION
    assert resolution.ambiguous is False


def test_resolve_name_falls_back_to_parent_task_subagent_type() -> None:
    resolution = resolve_name(
        meta_agent_type=None,
        attribution_agents=[],
        parent_task_subagent_type="code-reviewer",
        agent_id="a1",
    )
    assert resolution.name == "code-reviewer"
    assert resolution.name_source == NAME_SOURCE_PARENT_TASK
    assert resolution.ambiguous is False


def test_resolve_name_falls_back_to_agent_id_hash_and_never_drops_session() -> None:
    resolution = resolve_name(
        meta_agent_type=None,
        attribution_agents=[],
        parent_task_subagent_type=None,
        agent_id="a1deadbeef",
    )
    assert resolution.name == "a1deadbeef"
    assert resolution.name_source == NAME_SOURCE_AGENT_ID_HASH
    assert resolution.ambiguous is False


def test_resolve_name_conflicting_signals_flagged_ambiguous() -> None:
    resolution = resolve_name(
        meta_agent_type="researcher",
        attribution_agents=["implementer"],
        parent_task_subagent_type=None,
        agent_id="a1",
    )
    # meta still wins per priority, but the conflict is recorded.
    assert resolution.name == "researcher"
    assert resolution.name_source == NAME_SOURCE_META
    assert resolution.ambiguous is True


def test_resolve_name_consistent_signals_not_ambiguous() -> None:
    resolution = resolve_name(
        meta_agent_type="researcher",
        attribution_agents=["researcher"],
        parent_task_subagent_type="researcher",
        agent_id="a1",
    )
    assert resolution.ambiguous is False


# --------------------------------------------------------------------------
# 4.1 / 4.2 — defensive reads and tool_use/tool_result pairing
# --------------------------------------------------------------------------


def _assistant_tool_use(
    tool_use_id: str, tool_name: str, tool_input: dict[str, object]
) -> dict[str, object]:
    return {
        "type": "assistant",
        "attributionAgent": "implementer",
        "message": {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": tool_use_id, "name": tool_name, "input": tool_input}
            ],
        },
    }


def _user_tool_result(
    tool_use_id: str,
    *,
    content: str = "ok",
    is_error: bool = False,
    denial_kind: str | None = None,
    timestamp: str = "2026-07-06T18:56:19.617Z",
) -> dict[str, object]:
    record: dict[str, object] = {
        "type": "user",
        "timestamp": timestamp,
        "message": {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": content,
                    "is_error": is_error,
                }
            ],
        },
    }
    if denial_kind is not None:
        record["toolDenialKind"] = denial_kind
    return record


def test_extract_transcript_facts_pairs_tool_use_and_result_in_order() -> None:
    records = [
        _assistant_tool_use("t1", "Read", {"file_path": "a.py"}),
        _user_tool_result("t1"),
        _assistant_tool_use("t2", "Bash", {"command": "ls"}),
        _user_tool_result("t2", is_error=True),
    ]
    facts = extract_transcript_facts(records, session_id="s1")

    assert [e.seq for e in facts.tool_events] == [1, 2]
    assert [e.tool_name for e in facts.tool_events] == ["Read", "Bash"]
    assert facts.tool_events[0].is_error is False
    assert facts.tool_events[1].is_error is True


def test_extract_transcript_facts_records_denial_kind() -> None:
    records = [
        _assistant_tool_use("t1", "Bash", {"command": "rm -rf /"}),
        _user_tool_result("t1", is_error=True, denial_kind="permission-rule"),
    ]
    facts = extract_transcript_facts(records, session_id="s1")

    assert facts.tool_events[0].denial_kind == "permission-rule"


def test_extract_transcript_facts_skips_unpaired_tool_result() -> None:
    records = [_user_tool_result("orphan")]
    facts = extract_transcript_facts(records, session_id="s1")
    assert facts.tool_events == []


def test_extract_transcript_facts_collects_task_subagent_types() -> None:
    records = [_assistant_tool_use("t1", "Task", {"subagent_type": "researcher"})]
    facts = extract_transcript_facts(records, session_id="parent-sid")
    assert facts.task_subagent_types == {"t1": "researcher"}


def test_extract_task_subagent_types_returns_mapping() -> None:
    records = [
        _assistant_tool_use("t1", "Task", {"subagent_type": "researcher"}),
        _assistant_tool_use("t2", "Read", {"file_path": "a.py"}),
        _assistant_tool_use("t3", "Task", {"subagent_type": "code-reviewer"}),
    ]
    assert extract_task_subagent_types(records) == {
        "t1": "researcher",
        "t3": "code-reviewer",
    }


def test_extract_task_subagent_types_ignores_non_string_and_missing_subagent_type() -> None:
    records: list[dict[str, object]] = [
        _assistant_tool_use("t1", "Task", {"subagent_type": 123}),
        _assistant_tool_use("t2", "Task", {}),
        {"type": "assistant", "message": {"role": "assistant", "content": []}},
    ]
    assert extract_task_subagent_types(records) == {}


def test_extract_task_subagent_types_never_hashes_inputs(monkeypatch: pytest.MonkeyPatch) -> None:
    """`extract_task_subagent_types` must never build a `ToolEventRecord`,

    which would require hashing the tool_use input. Force `_hash_input` to
    blow up if it's called, then run the helper over a transcript that has
    both a spawning `Task` and ordinary paired tool_use/tool_result events
    (the path that would otherwise trigger hashing in
    `extract_transcript_facts`).
    """

    def _boom(_: object) -> str:
        raise AssertionError("extract_task_subagent_types must not hash tool inputs")

    monkeypatch.setattr("agentlens.parser._hash_input", _boom)

    records = [
        _assistant_tool_use("t1", "Task", {"subagent_type": "researcher"}),
        _assistant_tool_use("t2", "Read", {"file_path": "a.py"}),
        _user_tool_result("t2"),
    ]

    assert extract_task_subagent_types(records) == {"t1": "researcher"}


def test_extract_transcript_facts_collects_distinct_attribution_agents() -> None:
    records = [
        _assistant_tool_use("t1", "Read", {}),
        _assistant_tool_use("t2", "Read", {}),
    ]
    facts = extract_transcript_facts(records, session_id="s1")
    assert facts.attribution_agents == ["implementer"]


def test_read_jsonl_records_skips_malformed_lines(tmp_path: Path) -> None:
    path = tmp_path / "transcript.jsonl"
    path.write_text(
        '{"type": "user", "message": {"role": "user"}}\n'
        "not json at all\n"
        '["also", "not", "an", "object"]\n'
        '{"type": "assistant", "message": {"role": "assistant", "content": []}}\n'
    )
    records = read_jsonl_records(path)
    assert len(records) == 2
    assert records[0]["type"] == "user"
    assert records[1]["type"] == "assistant"


def test_read_jsonl_records_skips_unknown_record_types(tmp_path: Path) -> None:
    path = tmp_path / "transcript.jsonl"
    path.write_text(
        '{"type": "mode", "mode": "plan"}\n'
        '{"type": "attachment", "attachment": {}}\n'
        '{"type": "assistant", "message": {"role": "assistant", "content": []}}\n'
    )
    records = read_jsonl_records(path)
    facts = extract_transcript_facts(records, session_id="s1")
    assert facts.tool_events == []  # never aborts, just yields nothing to pair


# --------------------------------------------------------------------------
# 4.3 / 4.4 — session-level parsing: lineage and session_kind
# --------------------------------------------------------------------------


def test_parse_main_session_has_no_lineage(tmp_path: Path) -> None:
    path = tmp_path / "main-sid.jsonl"
    path.write_text('{"type": "assistant", "message": {"role": "assistant", "content": []}}\n')

    parsed = parse_main_session(path, session_id="main-sid")

    assert parsed.session_kind == SESSION_KIND_MAIN
    assert parsed.session_id == "main-sid"
    assert parsed.parent_session_id is None
    assert parsed.spawn_tool_use_id is None


def test_parse_subagent_run_resolves_lineage_from_args_and_meta(tmp_path: Path) -> None:
    path = tmp_path / "agent-a1.jsonl"
    path.write_text('{"type": "assistant", "message": {"role": "assistant", "content": []}}\n')

    parsed = parse_subagent_run(
        path,
        agent_id="a1",
        parent_session_id="parent-sid",
        meta={"agentType": "implementer", "toolUseId": "toolu_1", "spawnDepth": 1},
    )

    assert parsed.session_kind == SESSION_KIND_SUBAGENT
    assert parsed.session_id == "a1"
    assert parsed.parent_session_id == "parent-sid"
    assert parsed.spawn_tool_use_id == "toolu_1"
    assert parsed.name == "implementer"
    assert parsed.name_source == NAME_SOURCE_META


def test_parse_subagent_run_falls_back_to_parent_task_lookup(tmp_path: Path) -> None:
    path = tmp_path / "agent-a1.jsonl"
    path.write_text('{"type": "assistant", "message": {"role": "assistant", "content": []}}\n')

    parent_records = [_assistant_tool_use("toolu_1", "Task", {"subagent_type": "code-reviewer"})]

    parsed = parse_subagent_run(
        path,
        agent_id="a1",
        parent_session_id="parent-sid",
        meta={"toolUseId": "toolu_1"},
        parent_records=parent_records,
    )

    assert parsed.name == "code-reviewer"
    assert parsed.name_source == NAME_SOURCE_PARENT_TASK


def test_parse_subagent_run_never_drops_session_with_no_signals(tmp_path: Path) -> None:
    path = tmp_path / "agent-deadbeef.jsonl"
    path.write_text("")  # empty transcript, no meta, no parent

    parsed = parse_subagent_run(path, agent_id="deadbeef", parent_session_id="parent-sid")

    assert parsed.session_id == "deadbeef"
    assert parsed.name == "deadbeef"
    assert parsed.name_source == NAME_SOURCE_AGENT_ID_HASH


# --------------------------------------------------------------------------
# 4.5 — agent definition parsing
# --------------------------------------------------------------------------


def test_parse_agent_definition_extracts_frontmatter(tmp_path: Path) -> None:
    path = tmp_path / "implementer.md"
    path.write_text(
        "---\n"
        "name: implementer\n"
        "tools: Read, Write, Bash\n"
        "model: claude-sonnet-5\n"
        "effort: high\n"
        "skills: python-engineering-standards\n"
        "---\n\n"
        "Body text.\n"
    )

    record = parse_agent_definition(path)

    assert record is not None
    assert record.agent_type == "implementer"
    assert record.model == "claude-sonnet-5"
    assert record.effort == "high"
    assert record.declared_tools == ["Read", "Write", "Bash"]
    assert record.declared_skills == ["python-engineering-standards"]
    assert len(record.definition_hash) == 64  # sha256 hex digest


def test_parse_agent_definition_hash_changes_when_definition_changes(tmp_path: Path) -> None:
    path = tmp_path / "researcher.md"
    path.write_text("---\nname: researcher\n---\n")
    first = parse_agent_definition(path)

    path.write_text("---\nname: researcher\nmodel: claude-opus-4-6\n---\n")
    second = parse_agent_definition(path)

    assert first is not None
    assert second is not None
    assert first.definition_hash != second.definition_hash


def test_parse_agent_definition_returns_none_without_frontmatter(tmp_path: Path) -> None:
    path = tmp_path / "no-frontmatter.md"
    path.write_text("# Just a heading\n")
    assert parse_agent_definition(path) is None


def test_parse_agent_definition_skips_unreadable_file(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist.md"
    assert parse_agent_definition(missing) is None
