"""Tests for `agentlens.store`: DDL, location resolution, and idempotent writes."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from agentlens.store import (
    REQUIRED_TABLES,
    STORE_PATH_ENV_VAR,
    AgentDefRecord,
    StoreLocationError,
    ToolEventRecord,
    create_store,
    resolve_store_path,
    upsert_agent_definition,
    upsert_session_events,
)


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {row[0] for row in rows}


def test_create_store_produces_all_required_tables(tmp_path: Path) -> None:
    conn = create_store(tmp_path / "agentlens.db")
    try:
        assert set(REQUIRED_TABLES) <= _table_names(conn)
    finally:
        conn.close()


def test_create_store_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "agentlens.db"
    conn1 = create_store(db_path)
    conn1.close()

    conn2 = create_store(db_path)
    try:
        assert set(REQUIRED_TABLES) <= _table_names(conn2)
    finally:
        conn2.close()


def test_unpopulated_tables_exist_and_are_empty(tmp_path: Path) -> None:
    conn = create_store(tmp_path / "agentlens.db")
    try:
        unpopulated_tables = (
            "fact_session",
            "dim_date",
            "dim_tool",
            "bridge_session_skill",
            "fact_verdict",
        )
        for table in unpopulated_tables:
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608
            assert count == 0
    finally:
        conn.close()


def test_fact_tool_event_exposes_required_columns(tmp_path: Path) -> None:
    conn = create_store(tmp_path / "agentlens.db")
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(fact_tool_event)")}
        assert columns == {
            "session_id",
            "seq",
            "tool_name",
            "is_error",
            "denial_kind",
            "ts",
            "input_hash",
            "output_bytes",
        }
    finally:
        conn.close()


def test_dim_agent_exposes_required_columns(tmp_path: Path) -> None:
    conn = create_store(tmp_path / "agentlens.db")
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(dim_agent)")}
        assert columns == {
            "agent_type",
            "name",
            "model",
            "effort",
            "declared_tools",
            "declared_skills",
            "definition_hash",
        }
    finally:
        conn.close()


def test_resolve_store_path_defaults_to_cache_dir() -> None:
    path = resolve_store_path(env={})
    assert path == Path.home() / ".cache" / "agentlens" / "agentlens.db"


def test_resolve_store_path_env_var_override() -> None:
    path = resolve_store_path(env={STORE_PATH_ENV_VAR: "/tmp/custom/agentlens.db"})
    assert path == Path("/tmp/custom/agentlens.db")


def test_resolve_store_path_explicit_override_wins_over_env() -> None:
    path = resolve_store_path(
        store_override="/tmp/explicit/agentlens.db",
        env={STORE_PATH_ENV_VAR: "/tmp/custom/agentlens.db"},
    )
    assert path == Path("/tmp/explicit/agentlens.db")


def test_resolve_store_path_refuses_dot_claude_via_override() -> None:
    with pytest.raises(StoreLocationError):
        resolve_store_path(store_override="/Users/someone/.claude/agentlens.db")


def test_resolve_store_path_refuses_dot_claude_via_env() -> None:
    with pytest.raises(StoreLocationError):
        resolve_store_path(env={STORE_PATH_ENV_VAR: "/Users/someone/.claude/agentlens.db"})


def _event(session_id: str, seq: int, tool_name: str = "Read") -> ToolEventRecord:
    return ToolEventRecord(
        session_id=session_id,
        seq=seq,
        tool_name=tool_name,
        is_error=False,
        denial_kind=None,
        ts="2026-07-06T18:56:19.617Z",
        input_hash="deadbeef",
        output_bytes=42,
    )


def test_upsert_session_events_is_idempotent(tmp_path: Path) -> None:
    conn = create_store(tmp_path / "agentlens.db")
    try:
        events = [_event("s1", 1), _event("s1", 2, "Bash")]

        upsert_session_events(conn, "s1", events)
        upsert_session_events(conn, "s1", events)

        rows = conn.execute(
            "SELECT seq, tool_name FROM fact_tool_event WHERE session_id = ? ORDER BY seq", ("s1",)
        ).fetchall()
        assert rows == [(1, "Read"), (2, "Bash")]
    finally:
        conn.close()


def test_upsert_session_events_new_session_leaves_prior_rows_unchanged(tmp_path: Path) -> None:
    conn = create_store(tmp_path / "agentlens.db")
    try:
        upsert_session_events(conn, "s1", [_event("s1", 1)])
        upsert_session_events(conn, "s2", [_event("s2", 1), _event("s2", 2)])

        s1_rows = conn.execute(
            "SELECT COUNT(*) FROM fact_tool_event WHERE session_id = ?", ("s1",)
        ).fetchone()[0]
        s2_rows = conn.execute(
            "SELECT COUNT(*) FROM fact_tool_event WHERE session_id = ?", ("s2",)
        ).fetchone()[0]
        assert s1_rows == 1
        assert s2_rows == 2
    finally:
        conn.close()


def test_upsert_agent_definition_upserts_by_agent_type(tmp_path: Path) -> None:
    conn = create_store(tmp_path / "agentlens.db")
    try:
        agent = AgentDefRecord(
            agent_type="implementer",
            name="implementer",
            model="claude-sonnet-5",
            effort="high",
            declared_tools=["Read", "Write"],
            declared_skills=["python-engineering-standards"],
            definition_hash="hash1",
        )
        upsert_agent_definition(conn, agent)
        upsert_agent_definition(conn, agent)  # re-run: still one row

        rows = conn.execute("SELECT agent_type, definition_hash FROM dim_agent").fetchall()
        assert rows == [("implementer", "hash1")]

        updated = AgentDefRecord(
            agent_type="implementer",
            name="implementer",
            model="claude-sonnet-5",
            effort="high",
            declared_tools=["Read"],
            declared_skills=[],
            definition_hash="hash2",
        )
        upsert_agent_definition(conn, updated)
        rows = conn.execute("SELECT agent_type, definition_hash FROM dim_agent").fetchall()
        assert rows == [("implementer", "hash2")]
    finally:
        conn.close()
