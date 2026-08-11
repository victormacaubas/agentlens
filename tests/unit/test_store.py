"""Tests for `agentlens.store`: DDL, location resolution, and idempotent writes."""

from __future__ import annotations

import os
import sqlite3
import stat
from dataclasses import fields
from pathlib import Path

import pytest

from agentlens.errors import (
    ScoringClaimError,
    StaleVerdictError,
    StoreLocationError,
    StoreSchemaError,
)
from agentlens.store.models import (
    AgentDefRecord,
    ScoringClaimRecord,
    SessionRecord,
    SkillBridgeRecord,
    ToolEventRecord,
    VerdictRecord,
)
from agentlens.store.operations import (
    acquire_scoring_claim,
    fetch_declared_skills,
    fetch_effective_agent_definition,
    finalize_scoring_claim,
    release_scoring_claim,
    upsert_agent_definition,
    upsert_dim_date,
    upsert_dim_tool,
    upsert_session,
    upsert_session_events,
    upsert_session_grain,
    upsert_session_skills,
)
from agentlens.store.schema import (
    FACT_SESSION_COLUMNS,
    REQUIRED_TABLES,
    SCHEMA_VERSION,
    STORE_PATH_ENV_VAR,
    assert_readable_schema_version,
    create_store,
    resolve_store_path,
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


def _stale_store(path: Path) -> None:
    """Write a store in the pre-qualified-identity shape, unstamped."""
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE fact_session (
                session_id TEXT PRIMARY KEY,
                agent_type TEXT,
                session_date TEXT
            );
            CREATE TABLE dim_agent (
                agent_type TEXT PRIMARY KEY,
                definition_hash TEXT
            );
            """
        )
        conn.commit()
    finally:
        conn.close()
    path.chmod(0o600)


def test_create_store_stamps_schema_version(tmp_path: Path) -> None:
    conn = create_store(tmp_path / "agentlens.db")
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    finally:
        conn.close()


def test_create_store_rejects_unstamped_store_with_existing_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "agentlens.db"
    _stale_store(db_path)

    with pytest.raises(StoreSchemaError) as excinfo:
        create_store(db_path)

    message = str(excinfo.value)
    assert "schema version 0" in message
    assert str(SCHEMA_VERSION) in message
    assert "delete it and re-run ingest" in message
    # The stale tables are left untouched rather than partially upgraded.
    conn = sqlite3.connect(db_path)
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(fact_session)")}
        assert "judge_input_hash" not in columns
    finally:
        conn.close()


def test_create_store_rejects_store_stamped_by_a_newer_schema(tmp_path: Path) -> None:
    db_path = tmp_path / "agentlens.db"
    conn = create_store(db_path)
    try:
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(StoreSchemaError, match=f"schema version {SCHEMA_VERSION + 1}"):
        create_store(db_path)


def test_read_only_schema_check_rejects_a_stale_store(tmp_path: Path) -> None:
    db_path = tmp_path / "agentlens.db"
    _stale_store(db_path)

    conn = sqlite3.connect(db_path)
    try:
        with pytest.raises(StoreSchemaError):
            assert_readable_schema_version(conn, db_path)
    finally:
        conn.close()


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
            "file_path_hash",
            "output_bytes",
        }
    finally:
        conn.close()


def test_dim_agent_exposes_required_columns(tmp_path: Path) -> None:
    conn = create_store(tmp_path / "agentlens.db")
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(dim_agent)")}
        assert columns == {
            "agent_definition_id",
            "agent_type",
            "scope",
            "source_project",
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
    assert path == Path("/tmp/custom/agentlens.db").resolve(strict=False)


def test_resolve_store_path_explicit_override_wins_over_env() -> None:
    path = resolve_store_path(
        store_override="/tmp/explicit/agentlens.db",
        env={STORE_PATH_ENV_VAR: "/tmp/custom/agentlens.db"},
    )
    assert path == Path("/tmp/explicit/agentlens.db").resolve(strict=False)


def test_resolve_store_path_refuses_dot_claude_via_override() -> None:
    with pytest.raises(StoreLocationError):
        resolve_store_path(store_override="/Users/someone/.claude/agentlens.db")


def test_resolve_store_path_refuses_dot_claude_via_env() -> None:
    with pytest.raises(StoreLocationError):
        resolve_store_path(env={STORE_PATH_ENV_VAR: "/Users/someone/.claude/agentlens.db"})


def test_resolve_store_path_refuses_symlink_into_dot_claude(tmp_path: Path) -> None:
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    alias = tmp_path / "cache"
    alias.symlink_to(claude_dir, target_is_directory=True)

    with pytest.raises(StoreLocationError, match="inside a .claude directory"):
        resolve_store_path(store_override=alias / "agentlens.db")

    assert not (claude_dir / "agentlens.db").exists()


def test_create_store_is_private_under_permissive_umask(tmp_path: Path) -> None:
    store_dir = tmp_path / "private-store"
    store_path = store_dir / "agentlens.db"
    previous_umask = os.umask(0)
    try:
        conn = create_store(store_path)
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            with conn:
                conn.execute("INSERT INTO dim_tool (tool_name) VALUES ('Read')")

            assert stat.S_IMODE(store_dir.stat().st_mode) == 0o700
            assert stat.S_IMODE(store_path.stat().st_mode) == 0o600
            for suffix in ("-wal", "-shm"):
                sidecar = Path(f"{store_path}{suffix}")
                assert sidecar.exists()
                assert stat.S_IMODE(sidecar.stat().st_mode) == 0o600
        finally:
            conn.close()
    finally:
        os.umask(previous_umask)


def test_create_store_rejects_existing_insecure_permissions(tmp_path: Path) -> None:
    store_path = tmp_path / "agentlens.db"
    conn = create_store(store_path)
    conn.close()
    store_path.chmod(0o644)

    with pytest.raises(StoreLocationError, match="owner-only permissions"):
        create_store(store_path)


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


def test_upsert_agent_definition_preserves_versions_by_definition_hash(tmp_path: Path) -> None:
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
        rows = conn.execute(
            "SELECT agent_type, definition_hash FROM dim_agent ORDER BY definition_hash"
        ).fetchall()
        assert rows == [("implementer", "hash1"), ("implementer", "hash2")]
    finally:
        conn.close()


def test_fetch_declared_skills_returns_list_for_known_agent(tmp_path: Path) -> None:
    conn = create_store(tmp_path / "agentlens.db")
    try:
        agent = AgentDefRecord(
            agent_type="implementer",
            name="implementer",
            model=None,
            effort=None,
            declared_tools=[],
            declared_skills=["python-engineering-standards"],
            definition_hash="hash1",
        )
        upsert_agent_definition(conn, agent)
        assert fetch_declared_skills(conn, "implementer") == ["python-engineering-standards"]
    finally:
        conn.close()


def test_fetch_declared_skills_unknown_agent_returns_empty(tmp_path: Path) -> None:
    conn = create_store(tmp_path / "agentlens.db")
    try:
        assert fetch_declared_skills(conn, "no-such-agent") == []
    finally:
        conn.close()


def test_effective_definition_prefers_matching_project_then_user(tmp_path: Path) -> None:
    conn = create_store(tmp_path / "agentlens.db")
    try:
        user = AgentDefRecord(
            agent_type="implementer",
            name="implementer",
            model=None,
            effort=None,
            declared_tools=[],
            declared_skills=["user-skill"],
            definition_hash="user-hash",
        )
        project = AgentDefRecord(
            agent_type="implementer",
            name="implementer",
            model=None,
            effort=None,
            declared_tools=[],
            declared_skills=["project-skill"],
            definition_hash="project-hash",
            scope="project",
            source_project="project-a",
        )
        upsert_agent_definition(conn, user)
        upsert_agent_definition(conn, project)

        project_match = fetch_effective_agent_definition(
            conn, agent_type="implementer", source_project="project-a"
        )
        fallback = fetch_effective_agent_definition(
            conn, agent_type="implementer", source_project="project-b"
        )

        assert project_match is not None
        assert project_match.declared_skills == ["project-skill"]
        assert fallback is not None
        assert fallback.declared_skills == ["user-skill"]
    finally:
        conn.close()


# --------------------------------------------------------------------------
# fact_session: renamed/demoted columns (store-schema spec)
# --------------------------------------------------------------------------


def test_fact_session_has_renamed_and_demoted_columns(tmp_path: Path) -> None:
    conn = create_store(tmp_path / "agentlens.db")
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(fact_session)")}
        assert "n_duplicate_tool_calls" in columns
        assert "final_report_flagged_partial" in columns
        assert "n_retry_loops" not in columns
        assert "claimed_status" not in columns
        assert {
            "raw_session_id",
            "source_project",
            "source_revision",
            "source_mtime_ns",
            "source_size",
            "source_content_hash",
            "judge_input_hash",
            "agent_definition_id",
        } <= columns
    finally:
        conn.close()


def test_store_has_scoring_claim_and_window_indexes(tmp_path: Path) -> None:
    conn = create_store(tmp_path / "agentlens.db")
    try:
        indexes = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            ).fetchall()
        }
        assert "scoring_claim" in _table_names(conn)
        assert {
            "idx_fact_session_window",
            "idx_fact_session_agent_window",
            "idx_fact_session_parent",
            "idx_fact_session_parent_window",
        } <= indexes
    finally:
        conn.close()


def test_report_window_and_parent_lens_plans_avoid_full_session_scan(tmp_path: Path) -> None:
    conn = create_store(tmp_path / "agentlens.db")
    try:
        with conn:
            conn.executemany(
                """
                INSERT INTO fact_session (
                    session_id, raw_session_id, source_project, session_kind,
                    session_date, agent_type, parent_session_id
                ) VALUES (?, ?, ?, 'subagent', ?, ?, ?)
                """,
                (
                    (
                        f"qualified-{index}",
                        f"raw-{index}",
                        f"project-{index % 20}",
                        f"2026-07-{(index % 28) + 1:02d}",
                        "implementer" if index % 2 else "researcher",
                        f"parent-{index % 500}",
                    )
                    for index in range(20_000)
                ),
            )

        window_plan = [
            str(row[3])
            for row in conn.execute(
                """
                EXPLAIN QUERY PLAN
                SELECT agent_type, COUNT(*)
                FROM fact_session
                WHERE session_kind = 'subagent'
                  AND session_date >= ?
                  AND session_date < ?
                GROUP BY agent_type
                """,
                ("2026-07-10", "2026-07-17"),
            ).fetchall()
        ]
        parent_lens_plan = [
            str(row[3])
            for row in conn.execute(
                """
                EXPLAIN QUERY PLAN
                SELECT parent_session_id, COUNT(*)
                FROM fact_session
                WHERE session_kind = 'subagent'
                  AND parent_session_id IS NOT NULL
                  AND session_date >= ?
                  AND session_date < ?
                GROUP BY parent_session_id
                """,
                ("2026-07-10", "2026-07-17"),
            ).fetchall()
        ]

        assert not any(detail == "SCAN fact_session" for detail in window_plan)
        assert any("INDEX idx_fact_session_window" in detail for detail in window_plan)
        assert not any(detail == "SCAN fact_session" for detail in parent_lens_plan)
        assert any("INDEX idx_fact_session_parent_window" in detail for detail in parent_lens_plan)
    finally:
        conn.close()


def test_scoring_claim_blocks_active_owner_and_recovers_after_expiry(tmp_path: Path) -> None:
    db_path = tmp_path / "agentlens.db"
    first_conn = create_store(db_path)
    second_conn = create_store(db_path)
    try:
        upsert_session(first_conn, _session_record(judge_input_hash="input-hash"))
        first_claim = ScoringClaimRecord(
            session_id="s1",
            judge_input_hash="input-hash",
            rubric_version="v1",
            judge_model="claude-sonnet-5",
            owner_id="owner-1",
            expires_at="2026-07-06T00:10:00+00:00",
        )
        second_claim = ScoringClaimRecord(
            session_id="s1",
            judge_input_hash="input-hash",
            rubric_version="v1",
            judge_model="claude-sonnet-5",
            owner_id="owner-2",
            expires_at="2026-07-06T00:20:00+00:00",
        )

        assert acquire_scoring_claim(
            first_conn,
            first_claim,
            now="2026-07-06T00:00:00+00:00",
        )
        assert not acquire_scoring_claim(
            second_conn,
            second_claim,
            now="2026-07-06T00:05:00+00:00",
        )
        assert acquire_scoring_claim(
            second_conn,
            second_claim,
            now="2026-07-06T00:11:00+00:00",
        )
        assert not release_scoring_claim(first_conn, first_claim)
        assert release_scoring_claim(second_conn, second_claim)
    finally:
        first_conn.close()
        second_conn.close()


def test_scoring_claim_finalization_requires_current_owner(tmp_path: Path) -> None:
    conn = create_store(tmp_path / "agentlens.db")
    try:
        upsert_session(conn, _session_record(judge_input_hash="input-hash"))
        claim = ScoringClaimRecord(
            session_id="s1",
            judge_input_hash="input-hash",
            rubric_version="v1",
            judge_model="sonnet",
            owner_id="owner-1",
            expires_at="2026-07-06T00:10:00+00:00",
        )
        assert acquire_scoring_claim(
            conn,
            claim,
            now="2026-07-06T00:00:00+00:00",
        )
        verdict = VerdictRecord(
            session_id="s1",
            judge_input_hash="input-hash",
            rubric_version="v1",
            judge_model="claude-sonnet-5",
            verdict_json="{}",
            judge_cost_usd=0.01,
            judge_input_tokens=10,
            judge_output_tokens=5,
        )
        wrong_owner = ScoringClaimRecord(
            session_id=claim.session_id,
            judge_input_hash=claim.judge_input_hash,
            rubric_version=claim.rubric_version,
            judge_model=claim.judge_model,
            owner_id="owner-2",
            expires_at=claim.expires_at,
        )

        with pytest.raises(ScoringClaimError, match="active for this owner"):
            finalize_scoring_claim(
                conn,
                claim=wrong_owner,
                verdict=verdict,
                now="2026-07-06T00:05:00+00:00",
            )

        finalize_scoring_claim(
            conn,
            claim=claim,
            verdict=verdict,
            now="2026-07-06T00:05:00+00:00",
        )
        assert conn.execute("SELECT COUNT(*) FROM fact_verdict").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM scoring_claim").fetchone()[0] == 0
    finally:
        conn.close()


def test_scoring_claim_rejects_stale_in_flight_verdict(tmp_path: Path) -> None:
    conn = create_store(tmp_path / "agentlens.db")
    try:
        upsert_session(conn, _session_record(judge_input_hash="input-old"))
        claim = ScoringClaimRecord(
            session_id="s1",
            judge_input_hash="input-old",
            rubric_version="v1",
            judge_model="claude-sonnet-5",
            owner_id="owner-1",
            expires_at="2026-07-06T00:10:00+00:00",
        )
        assert acquire_scoring_claim(
            conn,
            claim,
            now="2026-07-06T00:00:00+00:00",
        )
        with conn:
            conn.execute(
                "UPDATE fact_session SET judge_input_hash = ? WHERE session_id = ?",
                ("input-new", "s1"),
            )
        verdict = VerdictRecord(
            session_id="s1",
            judge_input_hash="input-old",
            rubric_version="v1",
            judge_model="claude-sonnet-5",
            verdict_json="{}",
            judge_cost_usd=0.01,
            judge_input_tokens=10,
            judge_output_tokens=5,
        )

        with pytest.raises(StaleVerdictError, match="changed while scoring"):
            finalize_scoring_claim(
                conn,
                claim=claim,
                verdict=verdict,
                now="2026-07-06T00:05:00+00:00",
            )

        assert conn.execute("SELECT COUNT(*) FROM fact_verdict").fetchone()[0] == 0
        assert conn.execute("SELECT owner_id FROM scoring_claim").fetchone() == ("owner-1",)
    finally:
        conn.close()


def _session_record(session_id: str = "s1", **overrides: object) -> SessionRecord:
    defaults: dict[str, object] = {
        "session_id": session_id,
        "agent_id": "a1",
        "agent_type": "implementer",
        "name_source": "meta_agent_type",
        "session_kind": "subagent",
        "spawn_depth": 1,
        "parent_session_id": "parent-sid",
        "spawn_tool_use_id": "toolu_1",
        "task_description": "do the thing",
        "session_date": "2026-07-06",
        "n_turns": 3,
        "n_tool_calls": 2,
        "n_reads": 1,
        "n_edits": 1,
        "n_writes": 0,
        "n_bash": 0,
        "n_files_touched": 1,
        "n_errors": 0,
        "n_permission_denials": 0,
        "n_duplicate_tool_calls": 0,
        "final_report_flagged_partial": False,
        "duration_sec": 12.5,
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_read_tokens": 10,
        "cache_creation_tokens": 5,
        "task_prompt_len": 12,
        "n_skills_fired": 0,
    }
    defaults.update(overrides)
    return SessionRecord(**defaults)  # type: ignore[arg-type]


def test_upsert_session_replaces_row_by_session_id(tmp_path: Path) -> None:
    conn = create_store(tmp_path / "agentlens.db")
    try:
        upsert_session(conn, _session_record(n_errors=0))
        upsert_session(conn, _session_record(n_errors=3))  # re-ingest: full replace

        rows = conn.execute(
            "SELECT n_errors FROM fact_session WHERE session_id = ?", ("s1",)
        ).fetchall()
        assert rows == [(3,)]
    finally:
        conn.close()


def test_upsert_session_stores_final_report_flagged_partial_as_boolean(tmp_path: Path) -> None:
    conn = create_store(tmp_path / "agentlens.db")
    try:
        upsert_session(conn, _session_record(final_report_flagged_partial=True))
        row = conn.execute(
            "SELECT final_report_flagged_partial FROM fact_session WHERE session_id = ?", ("s1",)
        ).fetchone()
        assert row == (1,)
    finally:
        conn.close()


def test_upsert_session_skills_is_idempotent_and_full_replace(tmp_path: Path) -> None:
    conn = create_store(tmp_path / "agentlens.db")
    try:
        first = [
            SkillBridgeRecord(
                session_id="s1", skill_name="a", declared=True, available=False, fired=False
            )
        ]
        upsert_session_skills(conn, "s1", first)
        upsert_session_skills(conn, "s1", first)

        rows = conn.execute(
            "SELECT skill_name, declared, fired FROM bridge_session_skill WHERE session_id = ?",
            ("s1",),
        ).fetchall()
        assert rows == [("a", 1, 0)]

        second = [
            SkillBridgeRecord(
                session_id="s1", skill_name="b", declared=False, available=False, fired=True
            )
        ]
        upsert_session_skills(conn, "s1", second)
        rows = conn.execute(
            "SELECT skill_name FROM bridge_session_skill WHERE session_id = ?", ("s1",)
        ).fetchall()
        assert rows == [("b",)]  # "a" row is gone: full replace, not merge
    finally:
        conn.close()


def test_upsert_dim_date_is_idempotent(tmp_path: Path) -> None:
    conn = create_store(tmp_path / "agentlens.db")
    try:
        upsert_dim_date(conn, "2026-07-06", year=2026, month=7, day=6, iso_week=28)
        upsert_dim_date(conn, "2026-07-06", year=2026, month=7, day=6, iso_week=28)

        rows = conn.execute("SELECT date, iso_week FROM dim_date").fetchall()
        assert rows == [("2026-07-06", 28)]
    finally:
        conn.close()


def test_upsert_dim_tool_is_idempotent(tmp_path: Path) -> None:
    conn = create_store(tmp_path / "agentlens.db")
    try:
        upsert_dim_tool(conn, "Read")
        upsert_dim_tool(conn, "Read")
        upsert_dim_tool(conn, "Bash")

        rows = {row[0] for row in conn.execute("SELECT tool_name FROM dim_tool").fetchall()}
        assert rows == {"Read", "Bash"}
    finally:
        conn.close()


@pytest.mark.parametrize("mismatched_child", ["event", "skill"])
def test_session_grain_rejects_mismatched_child_before_mutation(
    tmp_path: Path,
    mismatched_child: str,
) -> None:
    conn = create_store(tmp_path / "agentlens.db")
    try:
        original = _session_record(
            n_errors=1,
            source_mtime_ns=10,
            source_size=10,
            source_content_hash="old",
            source_revision="old-revision",
        )
        assert upsert_session_grain(
            conn,
            record=original,
            events=[_event("s1", 1)],
            skills=[
                SkillBridgeRecord(
                    session_id="s1",
                    skill_name="old-skill",
                    declared=True,
                    available=True,
                    fired=False,
                )
            ],
        )

        events = [_event("other" if mismatched_child == "event" else "s1", 1, "Bash")]
        skills = [
            SkillBridgeRecord(
                session_id="other" if mismatched_child == "skill" else "s1",
                skill_name="new-skill",
                declared=False,
                available=False,
                fired=True,
            )
        ]
        with pytest.raises(ValueError, match="child identity mismatch"):
            upsert_session_grain(
                conn,
                record=_session_record(
                    n_errors=9,
                    source_mtime_ns=20,
                    source_size=20,
                    source_content_hash="new",
                    source_revision="new-revision",
                ),
                events=events,
                skills=skills,
            )

        assert conn.execute(
            "SELECT n_errors FROM fact_session WHERE session_id = 's1'"
        ).fetchone() == (1,)
        assert conn.execute(
            "SELECT tool_name FROM fact_tool_event WHERE session_id = 's1'"
        ).fetchall() == [("Read",)]
        assert conn.execute(
            "SELECT skill_name FROM bridge_session_skill WHERE session_id = 's1'"
        ).fetchall() == [("old-skill",)]
    finally:
        conn.close()


def test_stale_source_revision_cannot_replace_newer_grain(tmp_path: Path) -> None:
    conn = create_store(tmp_path / "agentlens.db")
    try:
        newer = _session_record(
            n_errors=2,
            source_mtime_ns=20,
            source_size=20,
            source_content_hash="new",
            source_revision="new",
        )
        older = _session_record(
            n_errors=9,
            source_mtime_ns=10,
            source_size=10,
            source_content_hash="old",
            source_revision="old",
        )
        assert upsert_session_grain(conn, record=newer, events=[], skills=[])
        assert not upsert_session_grain(conn, record=older, events=[], skills=[])
        assert conn.execute(
            "SELECT n_errors, source_revision FROM fact_session WHERE session_id = 's1'"
        ).fetchone() == (2, "new")
    finally:
        conn.close()


def test_equal_stat_metadata_with_different_hash_is_conflict(tmp_path: Path) -> None:
    conn = create_store(tmp_path / "agentlens.db")
    try:
        first = _session_record(
            n_errors=1,
            source_mtime_ns=20,
            source_size=20,
            source_content_hash="hash-a",
            source_revision="a",
        )
        conflict = _session_record(
            n_errors=9,
            source_mtime_ns=20,
            source_size=20,
            source_content_hash="hash-b",
            source_revision="b",
        )
        assert upsert_session_grain(conn, record=first, events=[], skills=[])
        assert not upsert_session_grain(
            conn, record=conflict, events=[], skills=[]
        )
        assert conn.execute(
            "SELECT n_errors FROM fact_session WHERE session_id = 's1'"
        ).fetchone() == (1,)
    finally:
        conn.close()


def test_every_fact_session_column_resolves_to_a_session_record_field() -> None:
    """`_fact_session_values` reads `SessionRecord` via `getattr` over
    `FACT_SESSION_COLUMNS`, which mypy cannot check, so a field rename that
    missed the constant would surface only as a runtime `AttributeError`.

    Declaration order deliberately differs between the two: the INSERT's column
    list and its values tuple are both generated from `FACT_SESSION_COLUMNS`, so
    they stay aligned with each other whatever the dataclass field order is. Only
    the name sets have to agree.
    """
    record_fields = {field.name for field in fields(SessionRecord)}
    assert set(FACT_SESSION_COLUMNS) == record_fields
    assert len(FACT_SESSION_COLUMNS) == len(set(FACT_SESSION_COLUMNS))
