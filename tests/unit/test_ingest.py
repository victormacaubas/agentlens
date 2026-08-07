"""Tests for `agentlens.ingest`: bulk ingest, idempotency, --limit, and
full-grain replace on re-ingest (session-parser spec's "Idempotent ingest").

Synthetic-only per ADR 0001: every `.claude`-shaped tree here is built
under `tmp_path`.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from agentlens.errors import SessionLookupAmbiguityError
from agentlens.ingest.orchestrator import (
    ingest_all,
    ingest_target,
    persist_parsed_session,
    resolve_target,
    sync_agent_definitions,
)
from agentlens.store.models import AgentDefRecord
from agentlens.store.operations import upsert_agent_definition
from agentlens.store.schema import create_store

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


def test_ingest_limit_does_not_enumerate_later_discovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claude_home = tmp_path / "claude-home"
    _write_main_session(claude_home, "-proj", "main-sid")

    def fail_if_iterated(*_args: object, **_kwargs: object):  # type: ignore[no-untyped-def]
        raise AssertionError("later discovery must remain lazy")
        yield

    monkeypatch.setattr(
        "agentlens.ingest.orchestrator.discover_subagent_runs",
        fail_if_iterated,
    )
    conn = create_store(tmp_path / "store.db")
    try:
        summary = ingest_all(conn, claude_home=claude_home, limit=1)
        assert summary.n_ingested == 1
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
            "SELECT n_tool_calls FROM fact_session WHERE raw_session_id = 'a1'"
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
            "SELECT n_tool_calls, session_date FROM fact_session WHERE raw_session_id = 'a1'"
        ).fetchone() == (1, "2026-07-06")
        assert conn.execute(
            """
            SELECT tool_name FROM fact_tool_event
            WHERE session_id = (
                SELECT session_id FROM fact_session WHERE raw_session_id = 'a1'
            )
            """
        ).fetchall() == [("Read",)]
        assert conn.execute(
            """
            SELECT skill_name FROM bridge_session_skill
            WHERE session_id = (
                SELECT session_id FROM fact_session WHERE raw_session_id = 'a1'
            )
            """
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
        assert conn.execute("SELECT raw_session_id FROM fact_session").fetchall() == [
            ("z-successful",)
        ]
        assert conn.execute(
            """
            SELECT DISTINCT fs.raw_session_id
            FROM fact_tool_event fte
            JOIN fact_session fs ON fs.session_id = fte.session_id
            """
        ).fetchall() == [
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

    (claude_home / "projects" / "-proj" / "bad-sid.jsonl").write_text("not-json\n")

    conn = create_store(tmp_path / "store.db")
    try:
        with caplog.at_level("WARNING"):
            summary = ingest_all(conn, claude_home=claude_home)

        assert summary.n_ingested == 2
        assert _table_count(conn, "fact_session") == 2
        ids = {
            row[0]
            for row in conn.execute("SELECT raw_session_id FROM fact_session").fetchall()
        }
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
            for row in conn.execute("SELECT raw_session_id FROM fact_session").fetchall()
        }
        assert ids == {"main-sid-1", "main-sid-2"}
    finally:
        conn.close()


def test_raw_id_collisions_across_projects_and_kinds_remain_distinct(
    tmp_path: Path,
) -> None:
    claude_home = tmp_path / "claude-home"
    _write_main_session(claude_home, "project-a", "shared")
    _write_main_session(claude_home, "project-b", "shared")
    _write_subagent_run(claude_home, "project-a", "parent", "shared")

    conn = create_store(tmp_path / "store.db")
    try:
        summary = ingest_all(conn, claude_home=claude_home)
        rows = conn.execute(
            """
            SELECT session_id, raw_session_id, source_project, session_kind
            FROM fact_session
            WHERE raw_session_id = 'shared'
            """
        ).fetchall()

        assert summary.n_ingested == 3
        assert len(rows) == 3
        assert len({row[0] for row in rows}) == 3
        assert {(row[2], row[3]) for row in rows} == {
            ("project-a", "main"),
            ("project-b", "main"),
            ("project-a", "subagent"),
        }
    finally:
        conn.close()


def test_parent_lineage_is_qualified_within_source_project(tmp_path: Path) -> None:
    claude_home = tmp_path / "claude-home"
    for project in ("project-a", "project-b"):
        _write_main_session(claude_home, project, "parent")
        _write_subagent_run(claude_home, project, "parent", f"agent-{project}")

    conn = create_store(tmp_path / "store.db")
    try:
        ingest_all(conn, claude_home=claude_home)
        rows = conn.execute(
            """
            SELECT child.source_project, parent.source_project
            FROM fact_session child
            JOIN fact_session parent ON parent.session_id = child.parent_session_id
            WHERE child.session_kind = 'subagent'
                ORDER BY child.source_project
            """
        ).fetchall()
        assert rows == [("project-a", "project-a"), ("project-b", "project-b")]
    finally:
        conn.close()


def test_raw_id_lookup_reports_project_and_kind_ambiguity(tmp_path: Path) -> None:
    claude_home = tmp_path / "claude-home"
    _write_main_session(claude_home, "project-a", "shared")
    _write_main_session(claude_home, "project-b", "shared")

    with pytest.raises(
        SessionLookupAmbiguityError,
        match=r"project-a/main.*project-b/main",
    ):
        resolve_target(file_path=None, session_id="shared", claude_home=claude_home)


def test_malformed_reingest_preserves_good_grain(tmp_path: Path) -> None:
    claude_home = tmp_path / "claude-home"
    path = _write_main_session(claude_home, "project", "session")
    conn = create_store(tmp_path / "store.db")
    try:
        assert ingest_all(conn, claude_home=claude_home).n_ingested == 1
        path.write_text("not-json\n")

        summary = ingest_all(conn, claude_home=claude_home)

        assert summary.n_ingested == 0
        assert summary.n_degraded == 1
        assert conn.execute(
            "SELECT n_tool_calls FROM fact_session WHERE raw_session_id = 'session'"
        ).fetchone() == (1,)
    finally:
        conn.close()


def test_project_correct_definition_binding_and_skill_bridge(tmp_path: Path) -> None:
    claude_home = tmp_path / "claude-home"
    _write_subagent_run(claude_home, "project-a", "parent", "a1")
    _write_subagent_run(claude_home, "project-b", "parent", "b1")

    conn = create_store(tmp_path / "store.db")
    try:
        for project, skill in (("project-a", "skill-a"), ("project-b", "skill-b")):
            upsert_agent_definition(
                conn,
                AgentDefRecord(
                    agent_type="implementer",
                    name="implementer",
                    model=None,
                    effort=None,
                    declared_tools=[],
                    declared_skills=[skill],
                    definition_hash=f"hash-{project}",
                    scope="project",
                    source_project=project,
                ),
            )

        ingest_all(conn, claude_home=claude_home)

        rows = conn.execute(
            """
            SELECT fs.source_project, bss.skill_name, da.source_project
            FROM fact_session fs
            JOIN bridge_session_skill bss ON bss.session_id = fs.session_id
            JOIN dim_agent da ON da.agent_definition_id = fs.agent_definition_id
            ORDER BY fs.source_project
            """
        ).fetchall()
        assert rows == [
            ("project-a", "skill-a", "project-a"),
            ("project-b", "skill-b", "project-b"),
        ]
    finally:
        conn.close()


def test_definition_update_preserves_old_session_binding(tmp_path: Path) -> None:
    claude_home = tmp_path / "claude-home"
    _write_subagent_run(claude_home, "project", "parent", "a1")
    conn = create_store(tmp_path / "store.db")
    try:
        first = AgentDefRecord(
            agent_type="implementer",
            name="implementer",
            model=None,
            effort=None,
            declared_tools=[],
            declared_skills=["skill-v1"],
            definition_hash="v1",
            scope="project",
            source_project="project",
        )
        upsert_agent_definition(conn, first)
        ingest_all(conn, claude_home=claude_home)

        second = AgentDefRecord(
            agent_type="implementer",
            name="implementer",
            model=None,
            effort=None,
            declared_tools=[],
            declared_skills=["skill-v2"],
            definition_hash="v2",
            scope="project",
            source_project="project",
        )
        upsert_agent_definition(conn, second)
        _write_subagent_run(claude_home, "project", "parent", "a2")
        ingest_all(conn, claude_home=claude_home)

        rows = conn.execute(
            """
            SELECT fs.raw_session_id, bss.skill_name
            FROM fact_session fs
            JOIN bridge_session_skill bss ON bss.session_id = fs.session_id
            ORDER BY fs.raw_session_id
            """
        ).fetchall()
        assert rows == [("a1", "skill-v1"), ("a2", "skill-v2")]
    finally:
        conn.close()


def test_out_of_tree_file_targets_use_canonical_parent_identity(tmp_path: Path) -> None:
    claude_home = tmp_path / "claude-home"
    first = tmp_path / "one" / "project" / "same.jsonl"
    second = tmp_path / "two" / "project" / "same.jsonl"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    first.write_text("{}\n")
    second.write_text("{}\n")

    first_target = resolve_target(
        file_path=first,
        session_id=None,
        claude_home=claude_home,
    )
    second_target = resolve_target(
        file_path=second,
        session_id=None,
        claude_home=claude_home,
    )

    assert first_target is not None
    assert second_target is not None
    assert first_target.session_id != second_target.session_id
    assert first_target.source_project.startswith("external:")
    assert second_target.source_project.startswith("external:")

    conn = create_store(tmp_path / "store.db")
    try:
        assert persist_parsed_session(conn, ingest_target(first_target))
        assert persist_parsed_session(conn, ingest_target(second_target))
        assert conn.execute("SELECT COUNT(*) FROM fact_session").fetchone()[0] == 2
    finally:
        conn.close()


def test_ingest_syncs_project_definition_from_transcript_cwd(tmp_path: Path) -> None:
    claude_home = tmp_path / "claude-home"
    project_root = tmp_path / "real-project"
    user_agents = claude_home / "agents"
    project_agents = project_root / ".claude" / "agents"
    user_agents.mkdir(parents=True)
    project_agents.mkdir(parents=True)
    (user_agents / "implementer.md").write_text(
        "---\nname: implementer\nskills: user-skill\n---\n"
    )
    (project_agents / "implementer.md").write_text(
        "---\nname: implementer\nskills: project-skill\n---\n"
    )

    transcript = _write_subagent_run(
        claude_home,
        "project-bucket",
        "parent",
        "agent",
    )
    records = [json.loads(line) for line in transcript.read_text().splitlines()]
    for record in records:
        record["cwd"] = str(project_root)
    transcript.write_text("\n".join(json.dumps(record) for record in records) + "\n")

    conn = create_store(tmp_path / "store.db")
    try:
        sync_agent_definitions(conn, claude_home=claude_home)
        summary = ingest_all(conn, claude_home=claude_home)

        assert summary.n_ingested == 1
        assert conn.execute(
            """
            SELECT bss.skill_name, da.scope, da.source_project
            FROM fact_session fs
            JOIN bridge_session_skill bss ON bss.session_id = fs.session_id
            JOIN dim_agent da ON da.agent_definition_id = fs.agent_definition_id
            """
        ).fetchall() == [("project-skill", "project", "project-bucket")]
    finally:
        conn.close()


def test_definition_sync_reports_unreadable_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claude_home = tmp_path / "claude-home"
    agents_dir = claude_home / "agents"
    agents_dir.mkdir(parents=True)
    original_iterdir = Path.iterdir

    def selective_iterdir(path: Path):  # type: ignore[no-untyped-def]
        if path == agents_dir:
            raise PermissionError("synthetic unreadable definitions")
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", selective_iterdir)
    conn = create_store(tmp_path / "store.db")
    try:
        summary = sync_agent_definitions(conn, claude_home=claude_home)

        assert summary.n_synced == 0
        assert summary.failed_paths == (str(agents_dir),)
    finally:
        conn.close()


def test_definition_sync_reports_unreadable_definition_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claude_home = tmp_path / "claude-home"
    definition = claude_home / "agents" / "implementer.md"
    definition.parent.mkdir(parents=True)
    definition.write_text("---\nname: implementer\n---\n")
    original_read_text = Path.read_text

    def selective_read_text(
        path: Path,
        encoding: str | None = None,
        errors: str | None = None,
    ) -> str:
        if path == definition:
            raise PermissionError("synthetic unreadable definition")
        return original_read_text(path, encoding=encoding, errors=errors)

    monkeypatch.setattr(Path, "read_text", selective_read_text)
    conn = create_store(tmp_path / "store.db")
    try:
        summary = sync_agent_definitions(conn, claude_home=claude_home)

        assert summary.n_synced == 0
        assert summary.failed_paths == (str(definition),)
    finally:
        conn.close()
