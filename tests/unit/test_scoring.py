"""Tests for `agentlens.judge.scoring.ScoringLoop`: per-session pass/fail
handling, the 3-consecutive-failure abort, idempotent re-runs, and the
`find_unscored_sessions` window/model filter."""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

from agentlens.errors import JudgeError
from agentlens.judge.protocol import DimensionScore, Verdict
from agentlens.judge.scoring import ScoringLoop
from agentlens.reporting.date_window import WindowRange
from agentlens.store.models import SessionRecord
from agentlens.store.operations import upsert_session_grain
from agentlens.store.schema import create_store

_DIMENSION_NAMES = ("task_completion", "honesty", "efficiency", "scope_adherence")


class MockJudge:
    """A `Judge` stand-in that fails for sessions whose task description
    (embedded in the transcript view's `## Task` section) is in
    `fail_session_ids`, and otherwise returns a valid `Verdict`.
    """

    def __init__(self, fail_session_ids: set[str] | None = None) -> None:
        self.fail_session_ids = fail_session_ids or set()
        self.calls: list[str] = []

    def score(self, transcript_view: str, rubric_version: str) -> Verdict:
        self.calls.append(transcript_view)
        session_marker = _extract_task_marker(transcript_view)
        if session_marker in self.fail_session_ids:
            raise JudgeError(f"mock judge failure for {session_marker}")
        return _make_verdict(session_marker, rubric_version)


def _extract_task_marker(transcript_view: str) -> str:
    """Pull back out the `task_description` a test embedded in the `## Task`
    section, so the mock judge can decide pass/fail per logical session
    without `score()` ever being told a session_id directly (matching the
    real `Judge` Protocol's signature)."""
    task_section = transcript_view.split("\n\n## Agent Identity")[0]
    return task_section.removeprefix("## Task\n")


def _make_verdict(session_marker: str, rubric_version: str) -> Verdict:
    return Verdict(
        session_id="",  # overwritten by ScoringLoop._score_session via replace()
        rubric_version=rubric_version,
        judge_model="mock-model",
        dimensions={
            name: DimensionScore(score=4, evidence=[f"evidence for {session_marker}"])
            for name in _DIMENSION_NAMES
        },
        overall_score=4.0,
        suggested_fixes=[],
        judge_cost_usd=0.01,
        judge_input_tokens=100,
        judge_output_tokens=50,
    )


def _session_record(session_id: str, **overrides: object) -> SessionRecord:
    defaults: dict[str, object] = {
        "session_id": session_id,
        "agent_id": session_id,
        "agent_type": "implementer",
        "name_source": "meta_agent_type",
        "session_kind": "subagent",
        "spawn_depth": 1,
        "parent_session_id": "parent-sid",
        "spawn_tool_use_id": "toolu_1",
        # The task description doubles as this session's identity marker for
        # MockJudge, since `score()` only ever sees the transcript view.
        "task_description": session_id,
        "session_date": "2026-07-05",
        "n_turns": 1,
        "n_tool_calls": 0,
        "n_reads": 0,
        "n_edits": 0,
        "n_writes": 0,
        "n_bash": 0,
        "n_files_touched": 0,
        "n_errors": 0,
        "n_permission_denials": 0,
        "n_duplicate_tool_calls": 0,
        "final_report_flagged_partial": False,
        "duration_sec": 1.0,
        "input_tokens": 1,
        "output_tokens": 1,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
        "task_prompt_len": 1,
        "n_skills_fired": 0,
    }
    defaults.update(overrides)
    return SessionRecord(**defaults)  # type: ignore[arg-type]


def _seed_sessions(
    conn: sqlite3.Connection, tmp_path: Path, session_ids: list[str], **overrides: object
) -> dict[str, Path]:
    """Persist `fact_session`/`fact_tool_event` rows for each session and
    return the `session_id -> jsonl_path` map `ScoringLoop.run` needs.

    The JSONL files are empty: `build_transcript_view` falls back to
    `parsed.task_description` (set to `session_id` here) when the raw
    transcript has no user record, so an empty file is a sufficient fixture.
    """
    jsonl_paths: dict[str, Path] = {}
    for session_id in session_ids:
        upsert_session_grain(
            conn, record=_session_record(session_id, **overrides), events=[], skills=[]
        )
        jsonl_path = tmp_path / f"{session_id}.jsonl"
        jsonl_path.write_text("")
        jsonl_paths[session_id] = jsonl_path
    return jsonl_paths


def _make_loop(
    conn: sqlite3.Connection, judge: MockJudge, *, judge_model: str = "sonnet"
) -> ScoringLoop:
    return ScoringLoop(
        judge=judge, conn=conn, rubric_version="v1", judge_model=judge_model
    )


def test_loop_scores_all_sessions(tmp_path: Path) -> None:
    conn = create_store(tmp_path / "store.db")
    try:
        session_ids = ["s1", "s2", "s3"]
        jsonl_paths = _seed_sessions(conn, tmp_path, session_ids)
        sessions = [_session_record(sid) for sid in session_ids]
        judge = MockJudge()
        loop = _make_loop(conn, judge)

        result = loop.run(sessions, jsonl_paths=jsonl_paths)

        assert result.scored == 3
        assert result.skipped == 0
        assert result.aborted is False
        n_verdicts = conn.execute("SELECT COUNT(*) FROM fact_verdict").fetchone()[0]
        assert n_verdicts == 3
    finally:
        conn.close()


def test_single_failure_skipped(tmp_path: Path) -> None:
    conn = create_store(tmp_path / "store.db")
    try:
        session_ids = ["s1", "s2", "s3"]
        jsonl_paths = _seed_sessions(conn, tmp_path, session_ids)
        sessions = [_session_record(sid) for sid in session_ids]
        judge = MockJudge(fail_session_ids={"s2"})
        loop = _make_loop(conn, judge)

        result = loop.run(sessions, jsonl_paths=jsonl_paths)

        assert result.scored == 2
        assert result.skipped == 1
        assert result.aborted is False
        scored_ids = {
            row[0] for row in conn.execute("SELECT session_id FROM fact_verdict").fetchall()
        }
        assert scored_ids == {"s1", "s3"}
    finally:
        conn.close()


def test_three_consecutive_failures_abort(tmp_path: Path) -> None:
    conn = create_store(tmp_path / "store.db")
    try:
        session_ids = ["s1", "s2", "s3", "s4"]
        jsonl_paths = _seed_sessions(conn, tmp_path, session_ids)
        sessions = [_session_record(sid) for sid in session_ids]
        judge = MockJudge(fail_session_ids={"s2", "s3", "s4"})
        loop = _make_loop(conn, judge)

        result = loop.run(sessions, jsonl_paths=jsonl_paths)

        assert result.aborted is True
        assert result.scored == 1
        assert result.skipped == 3
        scored_ids = {
            row[0] for row in conn.execute("SELECT session_id FROM fact_verdict").fetchall()
        }
        assert scored_ids == {"s1"}
    finally:
        conn.close()


def test_idempotent_rerun(tmp_path: Path) -> None:
    conn = create_store(tmp_path / "store.db")
    try:
        session_ids = ["s1", "s2"]
        jsonl_paths = _seed_sessions(conn, tmp_path, session_ids)
        sessions = [_session_record(sid) for sid in session_ids]
        judge = MockJudge()
        loop = _make_loop(conn, judge)

        first_result = loop.run(sessions, jsonl_paths=jsonl_paths)
        assert first_result.scored == 2

        window = WindowRange(start=date(2026, 7, 4), end=date(2026, 7, 11))
        unscored = loop.find_unscored_sessions(window=window)
        assert unscored == []

        second_result = loop.run(unscored, jsonl_paths=jsonl_paths)
        assert second_result.scored == 0
        assert second_result.skipped == 0
        assert second_result.aborted is False
    finally:
        conn.close()


def test_missing_transcript_skipped_and_next_scored(tmp_path: Path) -> None:
    conn = create_store(tmp_path / "store.db")
    try:
        session_ids = ["s1", "s2"]
        jsonl_paths = _seed_sessions(conn, tmp_path, session_ids)
        # Drop s1's transcript path entirely: `_score_session` raises
        # JudgeError("no transcript path provided...") for a missing key.
        del jsonl_paths["s1"]
        sessions = [_session_record(sid) for sid in session_ids]
        judge = MockJudge()
        loop = _make_loop(conn, judge)

        result = loop.run(sessions, jsonl_paths=jsonl_paths)

        assert result.skipped == 1
        assert result.scored == 1
        assert result.aborted is False
        scored_ids = {
            row[0] for row in conn.execute("SELECT session_id FROM fact_verdict").fetchall()
        }
        assert scored_ids == {"s2"}
    finally:
        conn.close()


def test_unreadable_transcript_skipped(tmp_path: Path) -> None:
    conn = create_store(tmp_path / "store.db")
    try:
        session_ids = ["s1", "s2"]
        jsonl_paths = _seed_sessions(conn, tmp_path, session_ids)
        # Point s1's "transcript" at a directory: reading it raises
        # IsADirectoryError, a subclass of OSError.
        jsonl_paths["s1"] = tmp_path
        sessions = [_session_record(sid) for sid in session_ids]
        judge = MockJudge()
        loop = _make_loop(conn, judge)

        result = loop.run(sessions, jsonl_paths=jsonl_paths)

        assert result.skipped == 1
        assert result.scored == 1
        assert result.aborted is False
    finally:
        conn.close()


def test_invalid_utf8_transcript_skipped(tmp_path: Path) -> None:
    conn = create_store(tmp_path / "store.db")
    try:
        session_ids = ["s1", "s2"]
        jsonl_paths = _seed_sessions(conn, tmp_path, session_ids)
        jsonl_paths["s1"].write_bytes(b"\x80\x81\x82")
        sessions = [_session_record(sid) for sid in session_ids]
        judge = MockJudge()
        loop = _make_loop(conn, judge)

        result = loop.run(sessions, jsonl_paths=jsonl_paths)

        assert result.skipped == 1
        assert result.scored == 1
        assert result.aborted is False
    finally:
        conn.close()


def test_io_failure_counts_toward_consecutive_abort(tmp_path: Path) -> None:
    conn = create_store(tmp_path / "store.db")
    try:
        session_ids = ["s1", "s2", "s3", "s4"]
        jsonl_paths = _seed_sessions(conn, tmp_path, session_ids)
        for sid in ("s2", "s3", "s4"):
            del jsonl_paths[sid]
        sessions = [_session_record(sid) for sid in session_ids]
        judge = MockJudge()
        loop = _make_loop(conn, judge)

        result = loop.run(sessions, jsonl_paths=jsonl_paths)

        assert result.aborted is True
        assert result.scored == 1
    finally:
        conn.close()


def test_find_unscored_filters_by_window_and_model(tmp_path: Path) -> None:
    conn = create_store(tmp_path / "store.db")
    try:
        jsonl_paths = _seed_sessions(conn, tmp_path, ["in-window-unscored"])
        jsonl_paths.update(
            _seed_sessions(
                conn, tmp_path, ["out-of-window"], session_date="2026-05-01"
            )
        )
        jsonl_paths.update(_seed_sessions(conn, tmp_path, ["scored-same-model"]))
        jsonl_paths.update(_seed_sessions(conn, tmp_path, ["scored-other-model"]))

        loop = _make_loop(conn, MockJudge(), judge_model="sonnet")

        # Already scored under this loop's exact (rubric_version, judge_model).
        loop.persist_verdict(
            Verdict(
                session_id="scored-same-model",
                rubric_version="v1",
                judge_model="sonnet",
                dimensions={
                    name: DimensionScore(score=3, evidence=[]) for name in _DIMENSION_NAMES
                },
                overall_score=3.0,
                suggested_fixes=[],
                judge_cost_usd=0.01,
                judge_input_tokens=10,
                judge_output_tokens=5,
            )
        )
        # Scored, but under a different judge_model -> still unscored for
        # this loop's (rubric_version, judge_model).
        other_model_loop = _make_loop(conn, MockJudge(), judge_model="opus")
        other_model_loop.persist_verdict(
            Verdict(
                session_id="scored-other-model",
                rubric_version="v1",
                judge_model="opus",
                dimensions={
                    name: DimensionScore(score=3, evidence=[]) for name in _DIMENSION_NAMES
                },
                overall_score=3.0,
                suggested_fixes=[],
                judge_cost_usd=0.01,
                judge_input_tokens=10,
                judge_output_tokens=5,
            )
        )

        window = WindowRange(start=date(2026, 7, 4), end=date(2026, 7, 11))
        unscored = loop.find_unscored_sessions(window=window)
        unscored_ids = {record.session_id for record in unscored}

        assert unscored_ids == {"in-window-unscored", "scored-other-model"}
    finally:
        conn.close()
