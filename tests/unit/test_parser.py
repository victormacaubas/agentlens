"""Tests for `agentlens.parser`: name resolution, defensive reads, event pairing.

These are fed plain dicts / hand-built fixtures under `tmp_path` — no
dependency on real logs and no strict row-content assertions against real
subagent transcripts (that validation is deferred to v2).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentlens.parser.extraction import (
    extract_task_subagent_types,
    extract_transcript_facts,
    flags_partial,
    read_jsonl_records,
)
from agentlens.parser.name_resolution import (
    NAME_SOURCE_AGENT_ID_HASH,
    NAME_SOURCE_ATTRIBUTION,
    NAME_SOURCE_META,
    NAME_SOURCE_PARENT_TASK,
    resolve_name,
)
from agentlens.parser.session import (
    SESSION_KIND_MAIN,
    SESSION_KIND_SUBAGENT,
    parse_agent_definition,
    parse_main_session,
    parse_subagent_run,
)


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

    monkeypatch.setattr("agentlens.parser.extraction._hash_input", _boom)

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


# --------------------------------------------------------------------------
# Usage, turns, duration (session-parser spec: "Extract usage, turns, and
# duration")
# --------------------------------------------------------------------------


def _assistant_message(
    *,
    text: str | None = None,
    usage: dict[str, object] | None = None,
    timestamp: str | None = None,
    attribution_agent: str = "implementer",
) -> dict[str, object]:
    content: list[dict[str, object]] = []
    if text is not None:
        content.append({"type": "text", "text": text})
    message: dict[str, object] = {"role": "assistant", "content": content}
    if usage is not None:
        message["usage"] = usage
    record: dict[str, object] = {
        "type": "assistant",
        "attributionAgent": attribution_agent,
        "message": message,
    }
    if timestamp is not None:
        record["timestamp"] = timestamp
    return record


def _meta_injection_record(command_name: str, *, timestamp: str | None = None) -> dict[str, object]:
    text = (
        f"<command-message>{command_name}</command-message>\n"
        f"<command-name>{command_name}</command-name>\n"
        "<skill-format>true</skill-format>rest of the injected content"
    )
    record: dict[str, object] = {
        "type": "user",
        "isMeta": True,
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
    }
    if timestamp is not None:
        record["timestamp"] = timestamp
    return record


def test_extract_transcript_facts_sums_usage_across_turns() -> None:
    records = [
        _assistant_message(
            usage={
                "input_tokens": 10,
                "output_tokens": 5,
                "cache_read_input_tokens": 100,
                "cache_creation_input_tokens": 20,
            }
        ),
        _assistant_message(
            usage={
                "input_tokens": 3,
                "output_tokens": 1,
                "cache_read_input_tokens": 50,
                "cache_creation_input_tokens": 0,
            }
        ),
    ]
    facts = extract_transcript_facts(records, session_id="s1")
    assert facts.input_tokens == 13
    assert facts.output_tokens == 6
    assert facts.cache_read_tokens == 150
    assert facts.cache_creation_tokens == 20
    assert facts.n_turns == 2


def test_extract_transcript_facts_tolerates_missing_usage() -> None:
    records: list[dict[str, object]] = [
        _assistant_message(usage={"input_tokens": 10}),  # missing other usage fields
        {"type": "assistant", "message": {"role": "assistant", "content": []}},  # no usage key
    ]
    facts = extract_transcript_facts(records, session_id="s1")
    assert facts.input_tokens == 10
    assert facts.output_tokens == 0
    assert facts.cache_read_tokens == 0


def test_extract_transcript_facts_clamps_negative_usage_to_zero() -> None:
    # BUG-02: a corrupted JSONL can carry a negative usage field; it must
    # not flow through as a negative sum.
    records = [_assistant_message(usage={"input_tokens": -5, "output_tokens": 3})]
    facts = extract_transcript_facts(records, session_id="s1")
    assert facts.input_tokens == 0
    assert facts.output_tokens == 3


def test_extract_transcript_facts_computes_duration_from_first_and_last_timestamp() -> None:
    records = [
        _assistant_message(text="start", timestamp="2026-07-06T18:00:00.000Z"),
        _assistant_message(text="end", timestamp="2026-07-06T18:05:30.000Z"),
    ]
    facts = extract_transcript_facts(records, session_id="s1")
    assert facts.duration_sec == 330.0
    assert facts.first_ts == "2026-07-06T18:00:00.000Z"


def test_extract_transcript_facts_duration_zero_without_timestamps() -> None:
    facts = extract_transcript_facts([_assistant_message(text="hi")], session_id="s1")
    assert facts.duration_sec == 0.0
    assert facts.first_ts is None


# --------------------------------------------------------------------------
# Skill-fire signals (session-parser spec: "Extract skill-fire signals")
# --------------------------------------------------------------------------


def test_extract_transcript_facts_injection_marker_yields_fired_skill() -> None:
    facts = extract_transcript_facts([_meta_injection_record("code-audit")], session_id="s1")
    assert facts.fired_skills == ["code-audit"]


def test_extract_transcript_facts_skill_tool_use_yields_fired_skill() -> None:
    records = [_assistant_tool_use("t1", "Skill", {"name": "openspec-sync-specs"})]
    facts = extract_transcript_facts(records, session_id="s1")
    assert facts.fired_skills == ["openspec-sync-specs"]


def test_extract_transcript_facts_skill_md_read_does_not_fire() -> None:
    records = [
        _assistant_tool_use("t1", "Read", {"file_path": "/skills/code-audit/SKILL.md"}),
        _user_tool_result("t1"),
    ]
    facts = extract_transcript_facts(records, session_id="s1")
    assert facts.fired_skills == []


# --------------------------------------------------------------------------
# `final_report_flagged_partial` marker matching (session-aggregation
# spec: "Final-report partial marker")
# --------------------------------------------------------------------------


def test_flags_partial_matches_unchecked_checkbox() -> None:
    assert flags_partial("Summary\n- [ ] follow-up task not done") is True


def test_flags_partial_matches_blocked_phrase_case_insensitively() -> None:
    assert flags_partial("I was BLOCKED by a permission denial.") is True


def test_flags_partial_clean_completion_is_false() -> None:
    assert flags_partial("All done. Every task is complete and verified.") is False


def test_flags_partial_none_or_empty_text_is_false() -> None:
    assert flags_partial(None) is False
    assert flags_partial("") is False


def test_flags_partial_does_not_match_partially_as_substring() -> None:
    # BUG-01: bare substring matching made "partial" false-positive on
    # "partially" (and "blocked" on "unblocked"); word-boundary matching
    # must not fire on containing words.
    assert flags_partial("I partially rewrote the code.") is False
    assert flags_partial("The fix left the module unblocked for reviewers.") is False


def test_flags_partial_still_matches_true_positives_after_word_boundary_fix() -> None:
    assert flags_partial("The task was blocked.") is True
    assert flags_partial("Summary\n- [ ] follow-up task not done") is True
    assert flags_partial("I was unable to finish the last step.") is True


def test_extract_transcript_facts_flags_partial_from_final_assistant_text_only() -> None:
    records = [
        _assistant_message(text="blocked earlier but recovered"),
        _assistant_message(text="Finished successfully, nothing pending."),
    ]
    facts = extract_transcript_facts(records, session_id="s1")
    assert facts.final_report_flagged_partial is False  # only the final text block counts


def test_extract_transcript_facts_flags_partial_true_on_final_text() -> None:
    records = [
        _assistant_message(text="Finished successfully."),
        _assistant_message(text="Actually I was unable to finish the last step."),
    ]
    facts = extract_transcript_facts(records, session_id="s1")
    assert facts.final_report_flagged_partial is True


def test_parse_main_session_exposes_usage_turns_and_duration(tmp_path: Path) -> None:
    path = tmp_path / "main-sid.jsonl"
    path.write_text(
        '{"type": "assistant", "timestamp": "2026-07-06T18:00:00.000Z", "message": '
        '{"role": "assistant", "content": [], "usage": {"input_tokens": 5, "output_tokens": 2}}}\n'
        '{"type": "assistant", "timestamp": "2026-07-06T18:01:00.000Z", "message": '
        '{"role": "assistant", "content": [], "usage": {"input_tokens": 1, "output_tokens": 1}}}\n'
    )
    parsed = parse_main_session(path, session_id="main-sid")

    assert parsed.n_turns == 2
    assert parsed.input_tokens == 6
    assert parsed.output_tokens == 3
    assert parsed.duration_sec == 60.0
    assert parsed.first_ts == "2026-07-06T18:00:00.000Z"


def test_parse_subagent_run_extracts_task_description_and_spawn_depth(tmp_path: Path) -> None:
    path = tmp_path / "agent-a1.jsonl"
    path.write_text('{"type": "assistant", "message": {"role": "assistant", "content": []}}\n')

    parsed = parse_subagent_run(
        path,
        agent_id="a1",
        parent_session_id="parent-sid",
        meta={
            "agentType": "implementer",
            "toolUseId": "toolu_1",
            "description": "fix the bug",
            "spawnDepth": 2,
        },
    )

    assert parsed.task_description == "fix the bug"
    assert parsed.spawn_depth == 2


def test_parse_subagent_run_uses_precomputed_parent_task_subagent_types(tmp_path: Path) -> None:
    path = tmp_path / "agent-a1.jsonl"
    path.write_text('{"type": "assistant", "message": {"role": "assistant", "content": []}}\n')

    parsed = parse_subagent_run(
        path,
        agent_id="a1",
        parent_session_id="parent-sid",
        meta={"toolUseId": "toolu_1"},
        parent_task_subagent_types={"toolu_1": "researcher"},
    )

    assert parsed.name == "researcher"
    assert parsed.name_source == NAME_SOURCE_PARENT_TASK


def test_parse_subagent_run_precomputed_map_skips_parent_records_extraction(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Bulk ingest passes a precomputed map so `parse_subagent_run` needn't
    re-derive it (or touch `parent_records`) per sibling spawn.
    """

    def _boom(_: object) -> dict[str, str]:
        raise AssertionError("extract_task_subagent_types must not run when a map is given")

    monkeypatch.setattr("agentlens.parser.session.extract_task_subagent_types", _boom)

    path = tmp_path / "agent-a1.jsonl"
    path.write_text('{"type": "assistant", "message": {"role": "assistant", "content": []}}\n')

    parsed = parse_subagent_run(
        path,
        agent_id="a1",
        parent_session_id="parent-sid",
        meta={"toolUseId": "toolu_1"},
        parent_records=[{"bogus": "would trigger re-derivation if used"}],
        parent_task_subagent_types={"toolu_1": "researcher"},
    )

    assert parsed.name == "researcher"
