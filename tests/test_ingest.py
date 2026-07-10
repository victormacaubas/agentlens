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
from agentlens.store import AgentDefRecord, create_store, upsert_agent_definition

_DEFAULT_TIMESTAMP = "2026-07-06T18:56:19.617Z"

_TOOL_USE_RECORD = (
    '{{"type": "assistant", "message": {{"role": "assistant", "content": ['
    '{{"type": "tool_use", "id": "{tool_use_id}", "name": "{tool_name}", '
    '"input": {{"file_path": "{path}"}}}}]}}}}\n'
    '{{"type": "user", "timestamp": "{timestamp}", "message": '
    '{{"role": "user", "content": [{{"type": "tool_result", '
    '"tool_use_id": "{tool_use_id}", "content": "ok", "is_error": false}}]}}}}\n'
)


def _tool_use_record(
    *,
    tool_use_id: str,
    path: str,
    tool_name: str = "Read",
    timestamp: str = _DEFAULT_TIMESTAMP,
) -> str:
    return _TOOL_USE_RECORD.format(
        tool_use_id=tool_use_id,
        path=path,
        tool_name=tool_name,
        timestamp=timestamp,
    )


def _write_main_session(
    claude_home: Path,
    project: str,
    session_id: str,
    *,
    tool_name: str = "Read",
    timestamp: str = _DEFAULT_TIMESTAMP,
) -> Path:
    project_dir = claude_home / "projects" / project
    project_dir.mkdir(parents=True, exist_ok=True)
    path = project_dir / f"{session_id}.jsonl"
    path.write_text(
        _tool_use_record(
            tool_use_id="t1",
            path="a.py",
            tool_name=tool_name,
            timestamp=timestamp,
        )
    )
    return path


def _write_subagent_run(
    claude_home: Path,
    project: str,
    parent_session_id: str,
    agent_id: str,
    *,
    n_tool_calls: int = 1,
    tool_name: str = "Read",
    timestamp: str = _DEFAULT_TIMESTAMP,
) -> Path:
    subagents_dir = claude_home / "projects" / project / parent_session_id / "subagents"
    subagents_dir.mkdir(parents=True, exist_ok=True)
    path = subagents_dir / f"agent-{agent_id}.jsonl"
    content = "".join(
        _tool_use_record(
            tool_use_id=f"t{i}",
            path=f"file{i}.py",
            tool_name=tool_name,
            timestamp=timestamp,
        )
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
            _tool_use_record(tool_use_id=f"t{i}", path=f"file{i}.py") for i in range(3)
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


def test_failed_reingest_preserves_previously_committed_session_grain(tmp_path: Path) -> None:
    claude_home = tmp_path / "claude-home"
    path = _write_subagent_run(claude_home, "-proj", "parent-sid", "a1")

    conn = create_store(tmp_path / "store.db")
    try:
        upsert_agent_definition(
            conn,
            AgentDefRecord(
                agent_type="implementer",
                name="implementer",
                model=None,
                effort=None,
                declared_tools=[],
                declared_skills=["old-skill"],
                definition_hash="old",
            ),
        )
        assert ingest_all(conn, claude_home=claude_home).n_ingested == 1

        upsert_agent_definition(
            conn,
            AgentDefRecord(
                agent_type="implementer",
                name="implementer",
                model=None,
                effort=None,
                declared_tools=[],
                declared_skills=["new-skill"],
                definition_hash="new",
            ),
        )
        path.write_text(
            "".join(
                _tool_use_record(
                    tool_use_id=f"new-t{i}",
                    path=f"new-file{i}.py",
                    tool_name="Bash",
                    timestamp="2026-07-07T18:56:19.617Z",
                )
                for i in range(3)
            )
        )
        conn.executescript(
            """
            CREATE TRIGGER fail_new_session_date
            BEFORE INSERT ON dim_date
            WHEN NEW.date = '2026-07-07'
            BEGIN
                SELECT RAISE(ABORT, 'synthetic late write failure');
            END;
            """
        )

        summary = ingest_all(conn, claude_home=claude_home)

        assert summary.n_ingested == 0
        assert conn.execute(
            "SELECT n_tool_calls, session_date FROM fact_session WHERE session_id = 'a1'"
        ).fetchone() == (1, "2026-07-06")
        assert conn.execute(
            "SELECT tool_name FROM fact_tool_event WHERE session_id = 'a1'"
        ).fetchall() == [("Read",)]
        assert conn.execute(
            "SELECT skill_name FROM bridge_session_skill WHERE session_id = 'a1'"
        ).fetchall() == [("old-skill",)]
        assert conn.execute("SELECT date FROM dim_date ORDER BY date").fetchall() == [
            ("2026-07-06",)
        ]
        assert conn.execute("SELECT tool_name FROM dim_tool ORDER BY tool_name").fetchall() == [
            ("Read",)
        ]
    finally:
        conn.close()


def test_failed_first_ingest_leaves_no_partial_rows_and_keeps_other_target(
    tmp_path: Path,
) -> None:
    claude_home = tmp_path / "claude-home"
    _write_main_session(
        claude_home,
        "-proj",
        "a-failed",
        timestamp="2026-07-08T18:56:19.617Z",
    )
    _write_main_session(
        claude_home,
        "-proj",
        "z-successful",
        timestamp="2026-07-09T18:56:19.617Z",
    )

    conn = create_store(tmp_path / "store.db")
    try:
        conn.executescript(
            """
            CREATE TRIGGER fail_first_session_date
            BEFORE INSERT ON dim_date
            WHEN NEW.date = '2026-07-08'
            BEGIN
                SELECT RAISE(ABORT, 'synthetic late write failure');
            END;
            """
        )

        summary = ingest_all(conn, claude_home=claude_home)

        assert summary.n_ingested == 1
        assert conn.execute("SELECT session_id FROM fact_session").fetchall() == [
            ("z-successful",)
        ]
        assert conn.execute("SELECT DISTINCT session_id FROM fact_tool_event").fetchall() == [
            ("z-successful",)
        ]
        assert conn.execute("SELECT date FROM dim_date").fetchall() == [("2026-07-09",)]
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
