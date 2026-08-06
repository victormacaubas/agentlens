"""Tests for `agentlens.judge.scoring.ScoringLoop`: per-session pass/fail
handling, the 3-consecutive-failure abort, idempotent re-runs, the
`find_unscored_sessions` window/model filter, and `score_window`'s
alias-resolution flow.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from agentlens.errors import JudgeError, JudgeUnavailableError
from agentlens.judge.protocol import DimensionScore, SuggestedFix, Verdict
from agentlens.judge.scoring import ScoringLoop
from agentlens.reporting.date_window import WindowRange
from agentlens.store.models import SessionRecord
from agentlens.store.operations import upsert_session_grain
from agentlens.store.schema import create_store

_DIMENSION_NAMES = ("task_completion", "honesty", "efficiency", "scope_adherence")

DEFAULT_RESOLVED_MODEL = "claude-sonnet-5"


class MockJudge:
    """A `Judge` stand-in that fails for sessions whose task description
    (embedded in the transcript view's `## Task` section) is in
    `fail_session_ids`, and otherwise returns a valid `Verdict` carrying
    `reports_as` as its resolved model.

    Mirrors `ClaudeCliJudge`'s real behavior: `resolved_model` stays `None`
    until a call to `score()` succeeds, so a loop that never scores a
    session with this judge never observes a resolved identity either.
    """

    def __init__(
        self, fail_session_ids: set[str] | None = None, *, reports_as: str = DEFAULT_RESOLVED_MODEL
    ) -> None:
        self.fail_session_ids = fail_session_ids or set()
        self.calls: list[str] = []
        self.reports_as = reports_as
        self.resolved_model: str | None = None

    def score(self, transcript_view: str, rubric_version: str) -> Verdict:
        self.calls.append(transcript_view)
        session_marker = _extract_task_marker(transcript_view)
        if session_marker in self.fail_session_ids:
            raise JudgeError(f"mock judge failure for {session_marker}")
        verdict = _make_verdict(session_marker, rubric_version, judge_model=self.reports_as)
        self.resolved_model = verdict.judge_model
        return verdict


def _extract_task_marker(transcript_view: str) -> str:
    """Pull back out the `task_description` a test embedded in the `## Task`
    section, so the mock judge can decide pass/fail per logical session
    without `score()` ever being told a session_id directly (matching the
    real `Judge` Protocol's signature).
    """

    task_section = transcript_view.split("\n\n## Agent Identity")[0]
    return task_section.removeprefix("## Task\n")


def _make_verdict(
    session_marker: str, rubric_version: str, *, judge_model: str = DEFAULT_RESOLVED_MODEL
) -> Verdict:
    return Verdict(
        session_id="",  # overwritten by ScoringLoop._score_session via replace()
        rubric_version=rubric_version,
        judge_model=judge_model,
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


class _UnavailableJudge:
    """A `Judge` stand-in whose every call raises `JudgeUnavailableError`,
    simulating an unavailable judge backend (e.g. missing credentials).
    """

    resolved_model: str | None = None

    def score(self, transcript_view: str, rubric_version: str) -> Verdict:
        raise JudgeUnavailableError("claude -p reported it is not logged in")


def test_judge_unavailable_error_propagates_as_hard_failure(tmp_path: Path) -> None:
    """`JudgeUnavailableError` is an environment problem, not a bad
    session — it must propagate out of `run()` rather than being counted as
    a per-session skip toward the consecutive-failure abort.
    """
    conn = create_store(tmp_path / "store.db")
    try:
        session_ids = ["s1", "s2"]
        jsonl_paths = _seed_sessions(conn, tmp_path, session_ids)
        sessions = [_session_record(sid) for sid in session_ids]
        loop = ScoringLoop(
            judge=_UnavailableJudge(), conn=conn, rubric_version="v1", judge_model="sonnet"
        )

        with pytest.raises(JudgeUnavailableError):
            loop.run(sessions, jsonl_paths=jsonl_paths)

        n_verdicts = conn.execute("SELECT COUNT(*) FROM fact_verdict").fetchone()[0]
        assert n_verdicts == 0
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


def test_score_session_preserves_backend_resolved_model(tmp_path: Path) -> None:
    """The loop must not overwrite the backend's resolved model with its own
    configured value: `run()` scoring under an alias still persists the
    concrete identifier the judge actually reported.
    """
    conn = create_store(tmp_path / "store.db")
    try:
        jsonl_paths = _seed_sessions(conn, tmp_path, ["s1"])
        sessions = [_session_record("s1")]
        judge = MockJudge(reports_as="claude-sonnet-5")
        loop = _make_loop(conn, judge, judge_model="sonnet")

        result = loop.run(sessions, jsonl_paths=jsonl_paths)

        assert result.scored == 1
        persisted_model = conn.execute(
            "SELECT judge_model FROM fact_verdict WHERE session_id = ?", ("s1",)
        ).fetchone()[0]
        assert persisted_model == "claude-sonnet-5"
    finally:
        conn.close()


def test_persist_verdict_round_trips_typed_fixes_and_provenance(tmp_path: Path) -> None:
    """`verdict_json` is an opaque TEXT column: writing a verdict carrying a
    typed fix and reading it back must reproduce `to_verdict_json()`'s
    output exactly, with no store-layer transformation of the payload.
    """
    conn = create_store(tmp_path / "store.db")
    try:
        loop = _make_loop(conn, MockJudge(), judge_model="claude-sonnet-5")
        verdict = Verdict(
            session_id="s1",
            rubric_version="v1",
            judge_model="claude-sonnet-5",
            dimensions={
                name: DimensionScore(score=3, evidence=["some evidence"])
                for name in _DIMENSION_NAMES
            },
            overall_score=3.0,
            suggested_fixes=[
                SuggestedFix(
                    dimension="efficiency",
                    target="agent_instructions",
                    recommendation="avoid re-reading files already read",
                    rationale="the agent read the same file twice in this run",
                )
            ],
            judge_cost_usd=0.01,
            judge_input_tokens=10,
            judge_output_tokens=5,
        )

        loop.persist_verdict(verdict)

        stored_json = conn.execute(
            "SELECT verdict_json FROM fact_verdict WHERE session_id = ?", ("s1",)
        ).fetchone()[0]
        assert json.loads(stored_json) == verdict.to_verdict_json()
    finally:
        conn.close()


def test_score_window_skips_resolution_when_configured_model_is_concrete(tmp_path: Path) -> None:
    """A concrete configured model needs no resolution call: the
    unscored-set query is already exact, so a fully-scored window costs
    zero judge calls.
    """
    conn = create_store(tmp_path / "store.db")
    try:
        jsonl_paths = _seed_sessions(conn, tmp_path, ["s1"])
        loop = _make_loop(conn, MockJudge(), judge_model="claude-sonnet-5")
        loop.persist_verdict(
            Verdict(
                session_id="s1",
                rubric_version="v1",
                judge_model="claude-sonnet-5",
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

        result = loop.score_window(window=window, jsonl_paths=jsonl_paths)

        assert result.scored == 0
        assert result.skipped == 0
    finally:
        conn.close()


def test_score_window_resolves_alias_with_one_call_when_fully_scored(tmp_path: Path) -> None:
    """When every session is already scored under the resolved identifier
    but the loop is configured with the alias, one resolution call is made
    and the re-query finds nothing further to score.
    """
    conn = create_store(tmp_path / "store.db")
    try:
        jsonl_paths = _seed_sessions(conn, tmp_path, ["s1", "s2"])
        for session_id in ("s1", "s2"):
            _make_loop(conn, MockJudge(), judge_model="claude-sonnet-5").persist_verdict(
                Verdict(
                    session_id=session_id,
                    rubric_version="v1",
                    judge_model="claude-sonnet-5",
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
        judge = MockJudge(reports_as="claude-sonnet-5")
        loop = _make_loop(conn, judge, judge_model="sonnet")
        window = WindowRange(start=date(2026, 7, 4), end=date(2026, 7, 11))

        result = loop.score_window(window=window, jsonl_paths=jsonl_paths)

        assert len(judge.calls) == 1
        assert result.scored == 1
        assert result.skipped == 0
    finally:
        conn.close()


def test_score_window_alias_movement_invalidates_prior_verdicts(tmp_path: Path) -> None:
    """When the alias later resolves to a different concrete model, the
    session is reported unscored under the new model and re-scored, while
    the earlier verdict remains in the store as a separate row.
    """
    conn = create_store(tmp_path / "store.db")
    try:
        jsonl_paths = _seed_sessions(conn, tmp_path, ["s1"])
        window = WindowRange(start=date(2026, 7, 4), end=date(2026, 7, 11))

        old_judge = MockJudge(reports_as="claude-sonnet-4")
        old_loop = _make_loop(conn, old_judge, judge_model="sonnet")
        first_result = old_loop.score_window(window=window, jsonl_paths=jsonl_paths)
        assert first_result.scored == 1

        new_judge = MockJudge(reports_as="claude-sonnet-5")
        new_loop = _make_loop(conn, new_judge, judge_model="sonnet")
        second_result = new_loop.score_window(window=window, jsonl_paths=jsonl_paths)
        assert second_result.scored == 1

        rows = conn.execute(
            "SELECT judge_model FROM fact_verdict WHERE session_id = ? ORDER BY judge_model",
            ("s1",),
        ).fetchall()
        assert {row[0] for row in rows} == {"claude-sonnet-4", "claude-sonnet-5"}
    finally:
        conn.close()
