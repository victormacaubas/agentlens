"""Tests for `agentlens.reporting`: window resolution, prior-window delta,
low-volume guard, spawns-not-sessions counting, and the intra-session
parent lens (windowed-reporting spec).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from agentlens.errors import WindowResolutionError
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
        assert row.n_failures == 1
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
