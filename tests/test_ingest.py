"""Tests for `agentlens.ingest`: bulk ingest, idempotency, --limit, and
full-grain replace on re-ingest (session-parser spec's "Idempotent ingest").

Synthetic-only per ADR 0001: every `.claude`-shaped tree here is built
under `tmp_path`.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from agentlens.ingest import ingest_all
from agentlens.store import create_store

_TOOL_USE_RECORD = (
    '{{"type": "assistant", "message": {{"role": "assistant", "content": ['
    '{{"type": "tool_use", "id": "{tool_use_id}", "name": "Read", '
    '"input": {{"file_path": "{path}"}}}}]}}}}\n'
    '{{"type": "user", "timestamp": "2026-07-06T18:56:19.617Z", "message": '
    '{{"role": "user", "content": [{{"type": "tool_result", '
    '"tool_use_id": "{tool_use_id}", "content": "ok", "is_error": false}}]}}}}\n'
)


def _write_main_session(claude_home: Path, project: str, session_id: str) -> Path:
    project_dir = claude_home / "projects" / project
    project_dir.mkdir(parents=True, exist_ok=True)
    path = project_dir / f"{session_id}.jsonl"
    path.write_text(_TOOL_USE_RECORD.format(tool_use_id="t1", path="a.py"))
    return path


def _write_subagent_run(
    claude_home: Path,
    project: str,
    parent_session_id: str,
    agent_id: str,
    *,
    n_tool_calls: int = 1,
) -> Path:
    subagents_dir = claude_home / "projects" / project / parent_session_id / "subagents"
    subagents_dir.mkdir(parents=True, exist_ok=True)
    path = subagents_dir / f"agent-{agent_id}.jsonl"
    content = "".join(
        _TOOL_USE_RECORD.format(tool_use_id=f"t{i}", path=f"file{i}.py")
        for i in range(n_tool_calls)
    )
    path.write_text(content)
    (subagents_dir / f"agent-{agent_id}.meta.json").write_text(
        '{"agentType": "implementer", "toolUseId": "toolu_1", "description": "fix it"}'
    )
    return path


def _table_count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])  # noqa: S608


def test_ingest_all_bulk_populates_store(tmp_path: Path) -> None:
    claude_home = tmp_path / "claude-home"
    _write_main_session(claude_home, "-proj", "main-sid")
    _write_subagent_run(claude_home, "-proj", "parent-sid", "a1")

    conn = create_store(tmp_path / "store.db")
    try:
        summary = ingest_all(conn, claude_home=claude_home)

        assert summary.n_ingested == 2
        assert _table_count(conn, "fact_session") == 2
        assert _table_count(conn, "fact_tool_event") == 2  # one Read each
    finally:
        conn.close()


def test_ingest_all_is_idempotent(tmp_path: Path) -> None:
    claude_home = tmp_path / "claude-home"
    _write_main_session(claude_home, "-proj", "main-sid")
    _write_subagent_run(claude_home, "-proj", "parent-sid", "a1")

    conn = create_store(tmp_path / "store.db")
    try:
        first = ingest_all(conn, claude_home=claude_home)
        second = ingest_all(conn, claude_home=claude_home)

        assert first.n_ingested == second.n_ingested == 2
        assert _table_count(conn, "fact_session") == 2
        assert _table_count(conn, "fact_tool_event") == 2
    finally:
        conn.close()


def test_ingest_all_limit_bounds_the_run(tmp_path: Path) -> None:
    claude_home = tmp_path / "claude-home"
    _write_main_session(claude_home, "-proj", "main-sid-1")
    _write_main_session(claude_home, "-proj", "main-sid-2")
    _write_main_session(claude_home, "-proj", "main-sid-3")

    conn = create_store(tmp_path / "store.db")
    try:
        summary = ingest_all(conn, claude_home=claude_home, limit=2)
        assert summary.n_ingested == 2
        assert _table_count(conn, "fact_session") == 2
    finally:
        conn.close()


def test_ingest_all_full_grain_replaced_on_reingest(tmp_path: Path) -> None:
    claude_home = tmp_path / "claude-home"
    path = _write_subagent_run(claude_home, "-proj", "parent-sid", "a1", n_tool_calls=1)

    conn = create_store(tmp_path / "store.db")
    try:
        ingest_all(conn, claude_home=claude_home)
        assert _table_count(conn, "fact_tool_event") == 1

        # Same session, but the transcript on disk now has more tool calls —
        # re-ingest must replace (not append to) every table for that session.
        content = "".join(
            _TOOL_USE_RECORD.format(tool_use_id=f"t{i}", path=f"file{i}.py") for i in range(3)
        )
        path.write_text(content)

        ingest_all(conn, claude_home=claude_home)

        assert _table_count(conn, "fact_tool_event") == 3
        assert _table_count(conn, "fact_session") == 1
        row = conn.execute(
            "SELECT n_tool_calls FROM fact_session WHERE session_id = 'a1'"
        ).fetchone()
        assert row == (3,)
    finally:
        conn.close()


def test_ingest_all_skips_one_bad_target_and_ingests_the_rest(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # ERR-01: a target that raises while being parsed (e.g. an unreadable
    # transcript) must not abort the rest of the batch.
    claude_home = tmp_path / "claude-home"
    _write_main_session(claude_home, "-proj", "good-sid-1")
    _write_main_session(claude_home, "-proj", "good-sid-2")

    # A directory named like a session transcript is discovered by the
    # `*.jsonl` glob but raises `IsADirectoryError` (a subclass of
    # `Exception`) when the parser tries to open it as a file.
    (claude_home / "projects" / "-proj" / "bad-sid.jsonl").mkdir(parents=True)

    conn = create_store(tmp_path / "store.db")
    try:
        with caplog.at_level("WARNING"):
            summary = ingest_all(conn, claude_home=claude_home)

        assert summary.n_ingested == 2
        assert _table_count(conn, "fact_session") == 2
        ids = {row[0] for row in conn.execute("SELECT session_id FROM fact_session").fetchall()}
        assert ids == {"good-sid-1", "good-sid-2"}
        assert any("bad-sid" in record.getMessage() for record in caplog.records)
    finally:
        conn.close()


def test_ingest_all_new_sessions_added_on_rerun_without_touching_prior_rows(tmp_path: Path) -> None:
    claude_home = tmp_path / "claude-home"
    _write_main_session(claude_home, "-proj", "main-sid-1")

    conn = create_store(tmp_path / "store.db")
    try:
        ingest_all(conn, claude_home=claude_home)
        assert _table_count(conn, "fact_session") == 1

        _write_main_session(claude_home, "-proj", "main-sid-2")
        ingest_all(conn, claude_home=claude_home)

        assert _table_count(conn, "fact_session") == 2
        ids = {
            row[0]
            for row in conn.execute("SELECT session_id FROM fact_session").fetchall()
        }
        assert ids == {"main-sid-1", "main-sid-2"}
    finally:
        conn.close()
