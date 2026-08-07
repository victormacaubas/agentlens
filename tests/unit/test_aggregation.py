"""Tests for `agentlens.aggregation`: fact_session derivation and the
skill bridge (session-aggregation / skill-usage-bridge specs).

Synthetic-only per ADR 0001: `ParsedSession` instances are hand-built here,
not read from real transcripts.
"""

from __future__ import annotations

from agentlens.aggregation.derivation import (
    count_duplicate_tool_calls,
    derive_fact_session,
    derive_skill_bridge,
)
from agentlens.parser.session import SESSION_KIND_SUBAGENT, ParsedSession
from agentlens.store.models import ToolEventRecord


def _event(
    tool_name: str,
    *,
    input_hash: str | None = "hash-a",
    is_error: bool = False,
    denial_kind: str | None = None,
    seq: int = 1,
    file_path_hash: str | None = None,
) -> ToolEventRecord:
    return ToolEventRecord(
        session_id="s1",
        seq=seq,
        tool_name=tool_name,
        is_error=is_error,
        denial_kind=denial_kind,
        ts="2026-07-06T18:00:00.000Z",
        input_hash=input_hash,
        output_bytes=10,
        file_path_hash=(
            file_path_hash
            if file_path_hash is not None
            else input_hash if tool_name in {"Read", "Edit", "Write"} else None
        ),
    )


def _parsed_session(
    *,
    events: list[ToolEventRecord] | None = None,
    fired_skills: list[str] | None = None,
    **overrides: object,
) -> ParsedSession:
    defaults: dict[str, object] = {
        "session_id": "s1",
        "session_kind": SESSION_KIND_SUBAGENT,
        "agent_id": "s1",
        "name": "implementer",
        "name_source": "meta_agent_type",
        "ambiguous": False,
        "parent_session_id": "parent-sid",
        "spawn_tool_use_id": "toolu_1",
        "task_description": "fix the bug",
        "spawn_depth": 1,
        "events": events if events is not None else [],
        "n_turns": 3,
        "duration_sec": 12.5,
        "first_ts": "2026-07-06T18:00:00.000Z",
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_read_tokens": 10,
        "cache_creation_tokens": 5,
        "fired_skills": fired_skills if fired_skills is not None else [],
        "final_report_flagged_partial": False,
    }
    defaults.update(overrides)
    return ParsedSession(**defaults)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# 6.1 — fact_session derivation
# --------------------------------------------------------------------------


def test_derive_fact_session_counts_tools_from_events() -> None:
    events = [
        _event("Read", input_hash="h1", seq=1),
        _event("Edit", input_hash="h2", seq=2),
        _event("Write", input_hash="h3", seq=3),
        _event("Bash", input_hash="h4", seq=4),
        _event("Read", input_hash="h5", is_error=True, seq=5),
        _event("Bash", input_hash="h6", denial_kind="permission-rule", seq=6),
    ]
    record = derive_fact_session(_parsed_session(events=events))

    assert record.n_tool_calls == 6
    assert record.n_reads == 2
    assert record.n_edits == 1
    assert record.n_writes == 1
    assert record.n_bash == 2
    assert record.n_errors == 1
    assert record.n_permission_denials == 1


def test_derive_fact_session_files_touched_is_distinct_path_hash_of_file_tools() -> None:
    events = [
        _event("Read", input_hash="h1", seq=1),
        _event("Read", input_hash="h1", seq=2),  # same file re-read
        _event("Edit", input_hash="h2", seq=3),
        _event("Bash", input_hash="h3", seq=4),  # not a file-touching tool
    ]
    record = derive_fact_session(_parsed_session(events=events))
    assert record.n_files_touched == 2  # h1, h2 — bash's h3 not counted


def test_derive_fact_session_carries_usage_turns_and_duration_directly() -> None:
    record = derive_fact_session(
        _parsed_session(n_turns=7, duration_sec=42.0, input_tokens=1, output_tokens=2,
                         cache_read_tokens=3, cache_creation_tokens=4)
    )
    assert record.n_turns == 7
    assert record.duration_sec == 42.0
    assert record.input_tokens == 1
    assert record.output_tokens == 2
    assert record.cache_read_tokens == 3
    assert record.cache_creation_tokens == 4


def test_derive_fact_session_missing_usage_tolerated_as_zero() -> None:
    record = derive_fact_session(
        _parsed_session(
            input_tokens=0, output_tokens=0, cache_read_tokens=0, cache_creation_tokens=0
        )
    )
    assert record.input_tokens == 0
    assert record.output_tokens == 0


def test_derive_fact_session_identity_and_lineage_persisted() -> None:
    record = derive_fact_session(_parsed_session())
    assert record.agent_id == "s1"
    assert record.agent_type == "implementer"
    assert record.name_source == "meta_agent_type"
    assert record.session_kind == SESSION_KIND_SUBAGENT
    assert record.parent_session_id == "parent-sid"
    assert record.spawn_tool_use_id == "toolu_1"


def test_derive_fact_session_session_date_from_first_ts() -> None:
    record = derive_fact_session(_parsed_session(first_ts="2026-07-06T18:00:00.000Z"))
    assert record.session_date == "2026-07-06"


def test_derive_fact_session_no_first_ts_yields_no_session_date() -> None:
    record = derive_fact_session(_parsed_session(first_ts=None))
    assert record.session_date is None


def test_derive_fact_session_malformed_short_timestamp_yields_no_session_date() -> None:
    record = derive_fact_session(_parsed_session(first_ts="2026-07"))
    assert record.session_date is None


def test_derive_fact_session_malformed_timestamp_suffix_yields_no_session_date() -> None:
    record = derive_fact_session(
        _parsed_session(first_ts="2026-07-06T18:00:00Z trailing")
    )
    assert record.session_date is None


def test_derive_fact_session_date_uses_utc_instant() -> None:
    record = derive_fact_session(
        _parsed_session(first_ts="2026-07-07T00:30:00+02:00")
    )
    assert record.session_date == "2026-07-06"


def test_files_touched_uses_path_hash_not_whole_input_hash() -> None:
    events = [
        _event("Read", input_hash="offset-1", file_path_hash="same-path", seq=1),
        _event("Read", input_hash="offset-50", file_path_hash="same-path", seq=2),
        _event("Edit", input_hash="content-a", file_path_hash="same-path", seq=3),
        _event("Edit", input_hash="content-b", file_path_hash="same-path", seq=4),
        _event("Read", input_hash="other", file_path_hash="other-path", seq=5),
    ]

    record = derive_fact_session(_parsed_session(events=events))

    assert record.n_files_touched == 2
    assert record.n_duplicate_tool_calls == 0


def test_files_touched_ignores_events_without_valid_path_hash() -> None:
    event = ToolEventRecord(
        session_id="s1",
        seq=1,
        tool_name="Read",
        is_error=False,
        denial_kind=None,
        ts=None,
        input_hash="whole-input",
        output_bytes=0,
        file_path_hash=None,
    )
    assert derive_fact_session(_parsed_session(events=[event])).n_files_touched == 0


def test_derive_fact_session_final_report_flagged_partial_passthrough() -> None:
    record = derive_fact_session(_parsed_session(final_report_flagged_partial=True))
    assert record.final_report_flagged_partial is True


def test_derive_fact_session_n_skills_fired_from_fired_skills() -> None:
    record = derive_fact_session(_parsed_session(fired_skills=["a", "b"]))
    assert record.n_skills_fired == 2


# --------------------------------------------------------------------------
# Duplicate tool-call count
# --------------------------------------------------------------------------


def test_count_duplicate_tool_calls_session_wide_not_consecutive() -> None:
    events = [
        _event("Read", input_hash="h1", seq=1),
        _event("Bash", input_hash="h2", seq=2),  # different tool/hash in between
        _event("Read", input_hash="h1", seq=3),  # duplicate, not consecutive
        _event("Read", input_hash="h1", seq=4),  # duplicate again
    ]
    assert count_duplicate_tool_calls(events) == 2  # 3 occurrences of (Read, h1) -> 2 beyond first


def test_count_duplicate_tool_calls_all_distinct_is_zero() -> None:
    events = [
        _event("Read", input_hash="h1", seq=1),
        _event("Edit", input_hash="h2", seq=2),
        _event("Bash", input_hash="h3", seq=3),
    ]
    assert count_duplicate_tool_calls(events) == 0


def test_count_duplicate_tool_calls_ignores_events_without_input_hash() -> None:
    events = [
        _event("Read", input_hash=None, seq=1),
        _event("Read", input_hash=None, seq=2),
    ]
    assert count_duplicate_tool_calls(events) == 0


def test_derive_fact_session_n_duplicate_tool_calls_uses_the_rule() -> None:
    events = [
        _event("Read", input_hash="h1", seq=1),
        _event("Read", input_hash="h1", seq=2),
        _event("Read", input_hash="h1", seq=3),
    ]
    record = derive_fact_session(_parsed_session(events=events))
    assert record.n_duplicate_tool_calls == 2


# --------------------------------------------------------------------------
# 6.2 — skill bridge (skill-usage-bridge spec)
# --------------------------------------------------------------------------


def test_derive_skill_bridge_union_of_declared_and_fired() -> None:
    records = derive_skill_bridge(
        _parsed_session(fired_skills=["skill-b"]),
        declared_skills=["skill-a"],
    )
    by_name = {r.skill_name: r for r in records}

    assert set(by_name) == {"skill-a", "skill-b"}
    assert by_name["skill-a"].declared is True
    assert by_name["skill-a"].fired is False
    assert by_name["skill-b"].declared is False
    assert by_name["skill-b"].fired is True


def test_derive_skill_bridge_declared_flag_from_agent_definition() -> None:
    records = derive_skill_bridge(_parsed_session(fired_skills=[]), declared_skills=["skill-a"])
    assert records[0].declared is True


def test_derive_skill_bridge_available_is_best_effort_default_false() -> None:
    records = derive_skill_bridge(
        _parsed_session(fired_skills=["skill-a"]), declared_skills=[], available_skills=()
    )
    assert records[0].available is False


def test_derive_skill_bridge_available_true_when_resolved() -> None:
    records = derive_skill_bridge(
        _parsed_session(fired_skills=["skill-a"]),
        declared_skills=[],
        available_skills=["skill-a"],
    )
    assert records[0].available is True


def test_derive_skill_bridge_no_declared_or_fired_skills_yields_no_rows() -> None:
    assert derive_skill_bridge(_parsed_session(fired_skills=[]), declared_skills=[]) == []
