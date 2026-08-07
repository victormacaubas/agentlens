"""Tests for `agentlens.reporting`: window resolution, prior-window delta,
low-volume guard, spawns-not-sessions counting, and the intra-session
parent lens (windowed-reporting spec).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path
from statistics import mean

import pytest

from agentlens.errors import WindowResolutionError
from agentlens.judge.rubric import RUBRIC_VERSION
from agentlens.reporting.date_window import WindowRange, resolve_window
from agentlens.reporting.queries import (
    DEFAULT_MIN_SESSIONS_FOR_TREND,
    build_report,
)
from agentlens.reporting.rendering import render_terminal_summary
from agentlens.store.models import SessionRecord
from agentlens.store.operations import upsert_session
from agentlens.store.schema import create_store

_NOW = date(2026, 7, 10)


def _session(
    session_id: str,
    *,
    agent_type: str | None = "implementer",
    session_kind: str = "subagent",
    parent_session_id: str | None = "p1",
    session_date: str | None = "2026-07-05",
    n_errors: int = 0,
    n_permission_denials: int = 0,
    final_report_flagged_partial: bool = False,
    raw_session_id: str | None = None,
    source_project: str = "project-a",
    judge_input_hash: str | None = None,
) -> SessionRecord:
    return SessionRecord(
        session_id=session_id,
        agent_id=session_id,
        agent_type=agent_type,
        name_source="meta_agent_type",
        session_kind=session_kind,
        spawn_depth=1,
        parent_session_id=parent_session_id,
        spawn_tool_use_id="toolu_1",
        task_description="d",
        session_date=session_date,
        n_turns=1,
        n_tool_calls=1,
        n_reads=0,
        n_edits=0,
        n_writes=0,
        n_bash=0,
        n_files_touched=0,
        n_errors=n_errors,
        n_permission_denials=n_permission_denials,
        n_duplicate_tool_calls=0,
        final_report_flagged_partial=final_report_flagged_partial,
        duration_sec=1.0,
        input_tokens=1,
        output_tokens=1,
        cache_read_tokens=0,
        cache_creation_tokens=0,
        task_prompt_len=1,
        n_skills_fired=0,
        raw_session_id=raw_session_id or f"raw-{session_id}",
        source_project=source_project,
        judge_input_hash=judge_input_hash or f"input-{session_id}",
    )


# --------------------------------------------------------------------------
# Window resolution
# --------------------------------------------------------------------------


def test_resolve_window_defaults_to_seven_days() -> None:
    window = resolve_window(now=_NOW)
    assert window.start == date(2026, 7, 4)
    assert window.end == date(2026, 7, 11)
    assert window.n_days == 7


def test_resolve_window_since_relative_days() -> None:
    window = resolve_window(since="30d", now=_NOW)
    assert window.start == date(2026, 6, 11)
    assert window.end == date(2026, 7, 11)


def test_resolve_window_since_absolute_date() -> None:
    window = resolve_window(since="2026-07-01", now=_NOW)
    assert window.start == date(2026, 7, 1)
    assert window.end == date(2026, 7, 11)


def test_resolve_window_today_shortcut_equivalent_to_since_1d() -> None:
    window = resolve_window(today=True, now=_NOW)
    assert window.start == date(2026, 7, 10)
    assert window.end == date(2026, 7, 11)
    assert window.n_days == 1
    assert window == resolve_window(since="1d", now=_NOW)


def test_resolve_window_explicit_from_to() -> None:
    window = resolve_window(from_="2026-07-01", to="2026-07-03", now=_NOW)
    assert window.start == date(2026, 7, 1)
    assert window.end == date(2026, 7, 4)  # end exclusive: day after `to`


def test_resolve_window_from_without_to_raises() -> None:
    with pytest.raises(WindowResolutionError):
        resolve_window(from_="2026-07-01", now=_NOW)


def test_window_range_prior_is_immediately_preceding_equal_span() -> None:
    window = WindowRange(start=date(2026, 7, 4), end=date(2026, 7, 11))
    prior = window.prior()
    assert prior.start == date(2026, 6, 27)
    assert prior.end == date(2026, 7, 4)
    assert prior.n_days == window.n_days


# --------------------------------------------------------------------------
# build_report: spawns-not-sessions, prior-window delta, low-volume guard,
# parent lens, agent filter
# --------------------------------------------------------------------------


def _store(tmp_path: Path) -> sqlite3.Connection:
    return create_store(tmp_path / "store.db")


def test_build_report_counts_spawns_not_parent_sessions(tmp_path: Path) -> None:
    conn = _store(tmp_path)
    try:
        for parent in range(3):
            for spawn in range(4):
                upsert_session(
                    conn,
                    _session(f"s{parent}-{spawn}", parent_session_id=f"p{parent}"),
                )
        window = WindowRange(start=date(2026, 7, 4), end=date(2026, 7, 11))
        report = build_report(conn, window=window, min_sessions_for_trend=5)

        assert len(report.agents) == 1
        assert report.agents[0].aggregate.n_spawns == 12
    finally:
        conn.close()


def test_build_report_prior_window_delta_against_preceding_equal_span(tmp_path: Path) -> None:
    conn = _store(tmp_path)
    try:
        for i in range(6):
            upsert_session(conn, _session(f"cur{i}", session_date="2026-07-05"))
        for i in range(3):
            upsert_session(conn, _session(f"prior{i}", session_date="2026-06-28"))

        window = WindowRange(start=date(2026, 7, 4), end=date(2026, 7, 11))
        report = build_report(conn, window=window, min_sessions_for_trend=5)

        result = report.agents[0]
        assert result.aggregate.n_spawns == 6
        assert result.prior is not None
        assert result.prior.n_spawns == 3
        assert result.insufficient_data is False
        assert result.delta is not None
        assert result.delta["n_spawns"] == 3
    finally:
        conn.close()


def test_build_report_low_volume_guard_suppresses_trend(tmp_path: Path) -> None:
    conn = _store(tmp_path)
    try:
        for i in range(3):
            upsert_session(conn, _session(f"s{i}", session_date="2026-07-05"))

        window = WindowRange(start=date(2026, 7, 4), end=date(2026, 7, 11))
        report = build_report(conn, window=window, min_sessions_for_trend=5)

        result = report.agents[0]
        assert result.aggregate.n_spawns == 3
        assert result.insufficient_data is True
        assert result.delta is None
    finally:
        conn.close()


def test_build_report_default_threshold_matches_design_default() -> None:
    assert DEFAULT_MIN_SESSIONS_FOR_TREND == 5


def test_build_report_agent_filter_narrows_results(tmp_path: Path) -> None:
    conn = _store(tmp_path)
    try:
        for i in range(2):
            upsert_session(conn, _session(f"impl{i}", agent_type="implementer"))
        for i in range(2):
            upsert_session(conn, _session(f"res{i}", agent_type="researcher"))

        window = WindowRange(start=date(2026, 7, 4), end=date(2026, 7, 11))
        report = build_report(conn, window=window, agent_type="implementer")

        assert [a.aggregate.agent_type for a in report.agents] == ["implementer"]
        assert report.agents[0].aggregate.n_spawns == 2
    finally:
        conn.close()


def test_build_report_excludes_main_sessions_from_agent_aggregates(tmp_path: Path) -> None:
    conn = _store(tmp_path)
    try:
        upsert_session(
            conn,
            _session("main-1", agent_type=None, session_kind="main", parent_session_id=None),
        )
        upsert_session(conn, _session("s1"))

        window = WindowRange(start=date(2026, 7, 4), end=date(2026, 7, 11))
        report = build_report(conn, window=window)

        assert len(report.agents) == 1
        assert report.agents[0].aggregate.n_spawns == 1
    finally:
        conn.close()


def test_build_report_parent_lens_summarizes_fanout_and_health(tmp_path: Path) -> None:
    conn = _store(tmp_path)
    try:
        upsert_session(conn, _session("s0", parent_session_id="parent-1", n_errors=1))
        upsert_session(
            conn, _session("s1", parent_session_id="parent-1", n_permission_denials=1)
        )
        upsert_session(conn, _session("s2", parent_session_id="parent-1"))
        upsert_session(conn, _session("s3", parent_session_id="parent-1"))

        window = WindowRange(start=date(2026, 7, 4), end=date(2026, 7, 11))
        report = build_report(conn, window=window)

        assert len(report.parent_lens) == 1
        row = report.parent_lens[0]
        assert row.parent_session_id == "parent-1"
        assert row.n_spawns == 4
        assert row.n_spawns_with_errors == 1
        assert row.n_denial_spawns == 1
    finally:
        conn.close()


def test_to_verdict_slice_has_no_score_fields(tmp_path: Path) -> None:
    conn = _store(tmp_path)
    try:
        upsert_session(conn, _session("s1"))
        window = WindowRange(start=date(2026, 7, 4), end=date(2026, 7, 11))
        report = build_report(conn, window=window)

        slice_dict = report.to_verdict_slice()
        serialized = json.dumps(slice_dict)  # must be JSON-serializable

        assert "score" not in serialized.lower()
        assert slice_dict["agents"][0]["n_spawns"] == 1
    finally:
        conn.close()


def test_to_verdict_slice_has_no_n_failures_key(tmp_path: Path) -> None:
    conn = _store(tmp_path)
    try:
        for i in range(6):
            upsert_session(conn, _session(f"cur{i}", session_date="2026-07-05", n_errors=1))
        for i in range(6):
            upsert_session(conn, _session(f"prior{i}", session_date="2026-06-28"))
        upsert_session(conn, _session("s0", parent_session_id="parent-1", n_errors=1))

        window = WindowRange(start=date(2026, 7, 4), end=date(2026, 7, 11))
        report = build_report(conn, window=window, min_sessions_for_trend=5)

        result = report.agents[0]
        assert result.delta is not None
        assert "n_spawns_with_errors" in result.delta

        payload = json.dumps(report.to_verdict_slice())
        assert "n_failures" not in payload
        assert "n_spawns_with_errors" in payload
    finally:
        conn.close()


def test_render_terminal_summary_mentions_spawns(tmp_path: Path) -> None:
    conn = _store(tmp_path)
    try:
        upsert_session(conn, _session("s1"))
        window = WindowRange(start=date(2026, 7, 4), end=date(2026, 7, 11))
        report = build_report(conn, window=window)

        summary = render_terminal_summary(report)
        assert "spawns" in summary
    finally:
        conn.close()


# --------------------------------------------------------------------------
# fact_verdict integration (windowed-reporting spec: opportunistic verdict
# inclusion when present, deterministic-only otherwise)
# --------------------------------------------------------------------------


def _verdict_json(overall_score: float = 4.0) -> dict[str, object]:
    return {
        "dimensions": {
            "task_completion": {"score": 4, "evidence": ["did the task"]},
            "honesty": {"score": 4, "evidence": ["accurate report"]},
            "efficiency": {"score": 4, "evidence": ["no redundant calls"]},
            "scope_adherence": {"score": 4, "evidence": ["stayed in scope"]},
        },
        "overall_score": overall_score,
        "suggested_fixes": [
            {
                "dimension": "efficiency",
                "target": "agent_instructions",
                "recommendation": "reduce redundant Read calls",
                "rationale": "the agent re-read the same file twice",
            }
        ],
    }


def _insert_verdict(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    rubric_version: str = RUBRIC_VERSION,
    judge_model: str = "claude-sonnet-5",
    overall_score: float = 4.0,
    judge_input_hash: str | None = None,
) -> None:
    if judge_input_hash is None:
        row = conn.execute(
            "SELECT judge_input_hash FROM fact_session WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        assert row is not None
        judge_input_hash = str(row[0])
    with conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO fact_verdict
                (session_id, judge_input_hash, rubric_version, judge_model, verdict_json,
                 judge_cost_usd, judge_input_tokens, judge_output_tokens)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                judge_input_hash,
                rubric_version,
                judge_model,
                json.dumps(_verdict_json(overall_score)),
                0.02,
                1000,
                200,
            ),
        )


def test_report_with_no_verdicts_unchanged(tmp_path: Path) -> None:
    conn = _store(tmp_path)
    try:
        upsert_session(conn, _session("s1"))
        window = WindowRange(start=date(2026, 7, 4), end=date(2026, 7, 11))
        report = build_report(conn, window=window)

        assert report.verdicts == {}
        assert report.agents[0].aggregate.avg_verdict_score is None
        assert report.to_verdict_slice()["verdicts"] == {}
    finally:
        conn.close()


def test_report_includes_verdicts_when_present(tmp_path: Path) -> None:
    conn = _store(tmp_path)
    try:
        upsert_session(conn, _session("s1"))
        _insert_verdict(conn, "s1", overall_score=4.0)

        window = WindowRange(start=date(2026, 7, 4), end=date(2026, 7, 11))
        report = build_report(conn, window=window)

        assert "s1" in report.verdicts
        assert report.verdicts["s1"]["overall_score"] == 4.0
        assert report.verdicts["s1"]["suggested_fixes"] == [
            {
                "dimension": "efficiency",
                "target": "agent_instructions",
                "recommendation": "reduce redundant Read calls",
                "rationale": "the agent re-read the same file twice",
            }
        ]
        assert report.agents[0].aggregate.avg_verdict_score == 4.0
    finally:
        conn.close()


def test_report_mixed_scored_and_unscored(tmp_path: Path) -> None:
    conn = _store(tmp_path)
    try:
        upsert_session(conn, _session("s1"))
        upsert_session(conn, _session("s2"))
        upsert_session(conn, _session("s3"))
        _insert_verdict(conn, "s2", overall_score=3.0)

        window = WindowRange(start=date(2026, 7, 4), end=date(2026, 7, 11))
        report = build_report(conn, window=window)

        assert len(report.agents) == 1
        assert report.agents[0].aggregate.n_spawns == 3
        assert len(report.sessions) == 3
        assert [row.session_id for row in report.sessions] == ["s1", "s2", "s3"]
        assert [row.verdict is not None for row in report.sessions] == [False, True, False]
        assert set(report.verdicts) == {"s2"}
        assert report.agents[0].aggregate.avg_verdict_score == 3.0
    finally:
        conn.close()


def test_terminal_summary_shows_avg_score(tmp_path: Path) -> None:
    conn = _store(tmp_path)
    try:
        upsert_session(conn, _session("s1"))
        _insert_verdict(conn, "s1", overall_score=4.2)

        window = WindowRange(start=date(2026, 7, 4), end=date(2026, 7, 11))
        report = build_report(conn, window=window)

        summary = render_terminal_summary(report)
        assert "avg score:" in summary
    finally:
        conn.close()


def test_verdict_slice_json_includes_verdicts(tmp_path: Path) -> None:
    conn = _store(tmp_path)
    try:
        upsert_session(conn, _session("s1"))
        _insert_verdict(conn, "s1", overall_score=4.0)

        window = WindowRange(start=date(2026, 7, 4), end=date(2026, 7, 11))
        report = build_report(conn, window=window)

        slice_dict = report.to_verdict_slice()
        json.dumps(slice_dict)  # must remain JSON-serializable

        assert "verdicts" in slice_dict
        assert slice_dict["verdicts"]["s1"]["overall_score"] == 4.0
        assert slice_dict["verdict_cohort"] == {
            "rubric_version": RUBRIC_VERSION,
            "judge_model": "claude-sonnet-5",
            "judge_input_policy": "current",
        }
        assert slice_dict["sessions"][0]["session_id"] == "s1"
        assert slice_dict["sessions"][0]["raw_session_id"] == "raw-s1"
        assert slice_dict["sessions"][0]["source_project"] == "project-a"
    finally:
        conn.close()


def test_report_explicit_cohort_excludes_other_rubrics_models_and_stale_input(
    tmp_path: Path,
) -> None:
    conn = _store(tmp_path)
    try:
        upsert_session(conn, _session("s1"))
        _insert_verdict(conn, "s1", overall_score=4.0)
        _insert_verdict(
            conn,
            "s1",
            rubric_version="legacy",
            judge_model="claude-sonnet-5",
            overall_score=1.0,
        )
        _insert_verdict(
            conn,
            "s1",
            judge_model="claude-opus-5",
            overall_score=2.0,
        )
        _insert_verdict(
            conn,
            "s1",
            judge_model="claude-sonnet-5",
            overall_score=0.0,
            judge_input_hash="stale-input",
        )

        report = build_report(
            conn,
            window=WindowRange(start=date(2026, 7, 4), end=date(2026, 7, 11)),
            rubric_version=RUBRIC_VERSION,
            judge_model="claude-sonnet-5",
        )

        assert report.verdict_cohort.judge_model == "claude-sonnet-5"
        assert report.sessions[0].verdict is not None
        assert report.sessions[0].verdict["overall_score"] == 4.0
        assert report.agents[0].aggregate.avg_verdict_score == 4.0
    finally:
        conn.close()


def test_report_requires_model_when_current_cohort_is_ambiguous(tmp_path: Path) -> None:
    conn = _store(tmp_path)
    try:
        upsert_session(conn, _session("s1"))
        _insert_verdict(conn, "s1", judge_model="claude-sonnet-5")
        _insert_verdict(conn, "s1", judge_model="claude-opus-5")

        with pytest.raises(ValueError, match="pass --judge-model"):
            build_report(
                conn,
                window=WindowRange(start=date(2026, 7, 4), end=date(2026, 7, 11)),
            )
    finally:
        conn.close()


def test_report_rejects_floating_model_alias(tmp_path: Path) -> None:
    conn = _store(tmp_path)
    try:
        with pytest.raises(ValueError, match="must be concrete"):
            build_report(
                conn,
                window=WindowRange(start=date(2026, 7, 4), end=date(2026, 7, 11)),
                judge_model="sonnet",
            )
    finally:
        conn.close()


def test_report_payload_is_independent_of_verdict_insertion_order(tmp_path: Path) -> None:
    payloads: list[dict[str, object]] = []
    for store_name, insertion_order in (
        ("forward.db", ("s1", "s2")),
        ("reverse.db", ("s2", "s1")),
    ):
        conn = create_store(tmp_path / store_name)
        try:
            upsert_session(conn, _session("s1"))
            upsert_session(conn, _session("s2"))
            scores = {"s1": 5.0, "s2": 3.0}
            for session_id in insertion_order:
                _insert_verdict(conn, session_id, overall_score=scores[session_id])
            report = build_report(
                conn,
                window=WindowRange(start=date(2026, 7, 4), end=date(2026, 7, 11)),
                judge_model="claude-sonnet-5",
            )
            payloads.append(report.to_verdict_slice())
        finally:
            conn.close()

    assert payloads[0] == payloads[1]


def test_agent_aggregate_reconciles_to_same_type_session_rows(tmp_path: Path) -> None:
    conn = _store(tmp_path)
    try:
        for session_id in ("s1", "s2", "s3"):
            upsert_session(conn, _session(session_id, parent_session_id="parent"))
        _insert_verdict(conn, "s1", overall_score=5.0)
        _insert_verdict(conn, "s2", overall_score=3.0)

        report = build_report(
            conn,
            window=WindowRange(start=date(2026, 7, 4), end=date(2026, 7, 11)),
            judge_model="claude-sonnet-5",
        )

        scored_rows = [row for row in report.sessions if row.verdict is not None]
        assert report.agents[0].aggregate.n_spawns == len(report.sessions) == 3
        assert report.agents[0].aggregate.avg_verdict_score == mean(
            float(row.verdict["overall_score"]) for row in scored_rows if row.verdict
        )
        assert report.parent_lens[0].n_spawns == len(report.sessions)
    finally:
        conn.close()
