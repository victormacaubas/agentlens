"""Tests for `agentlens.cli`: wiring, empty pipeline, and error reporting.

Most invocations redirect HOME to `tmp_path` (via monkeypatch) so tests
never touch the real `~/.claude` or `~/.cache/agentlens`. Some instead pass
`--claude-home` explicitly, which achieves the same isolation without
touching HOME at all.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from click.testing import CliRunner

from agentlens.cli import main
from agentlens.store.schema import REQUIRED_TABLES


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return home


def test_help_lists_session_and_report(isolated_home: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "session" in result.output
    assert "report" in result.output


def test_session_subcommand_help_exits_zero(isolated_home: Path) -> None:
    result = CliRunner().invoke(main, ["session", "--help"])
    assert result.exit_code == 0


def test_report_subcommand_help_exits_zero(isolated_home: Path) -> None:
    result = CliRunner().invoke(main, ["report", "--help"])
    assert result.exit_code == 0


def test_report_stub_accepts_window_flags_and_exits_zero(
    isolated_home: Path, tmp_path: Path
) -> None:
    store_path = tmp_path / "store.db"
    result = CliRunner().invoke(main, ["--store", str(store_path), "report", "--since", "7d"])
    assert result.exit_code == 0


def test_empty_pipeline_creates_valid_store_and_exits_zero(
    isolated_home: Path, tmp_path: Path
) -> None:
    store_path = tmp_path / "store.db"
    result = CliRunner().invoke(main, ["--store", str(store_path), "session"])

    assert result.exit_code == 0
    assert store_path.exists()

    conn = sqlite3.connect(store_path)
    try:
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert set(REQUIRED_TABLES) <= tables
    finally:
        conn.close()


def test_missing_file_target_reports_error_and_exits_nonzero(
    isolated_home: Path, tmp_path: Path
) -> None:
    store_path = tmp_path / "store.db"
    missing = tmp_path / "does-not-exist.jsonl"

    result = CliRunner().invoke(
        main, ["--store", str(store_path), "session", "--file", str(missing)]
    )

    assert result.exit_code != 0

    conn = sqlite3.connect(store_path)
    try:
        count = conn.execute("SELECT COUNT(*) FROM fact_tool_event").fetchone()[0]
        assert count == 0
    finally:
        conn.close()


def test_missing_session_id_target_reports_error_and_exits_nonzero(
    isolated_home: Path, tmp_path: Path
) -> None:
    store_path = tmp_path / "store.db"
    result = CliRunner().invoke(main, ["--store", str(store_path), "session", "no-such-session-id"])
    assert result.exit_code != 0


def test_session_ingests_a_main_file_target(isolated_home: Path, tmp_path: Path) -> None:
    store_path = tmp_path / "store.db"
    transcript = tmp_path / "main-sid.jsonl"
    transcript.write_text(
        '{"type": "assistant", "message": {"role": "assistant", "content": []}}\n'
    )

    result = CliRunner().invoke(
        main, ["--store", str(store_path), "session", "--file", str(transcript)]
    )

    assert result.exit_code == 0
    assert "main-sid" in result.output


def test_session_ingests_synthetic_subagent_with_events_and_lineage(
    isolated_home: Path, tmp_path: Path
) -> None:
    """8.1: full pipeline (discover -> parse -> persist) over a synthetic subagent
    transcript; fact_tool_event rows land and parent lineage resolves.
    """
    
    store_path = tmp_path / "store.db"
    subagents_dir = isolated_home / ".claude" / "projects" / "-proj" / "parent-sid" / "subagents"
    subagents_dir.mkdir(parents=True)

    transcript = subagents_dir / "agent-a1.jsonl"
    transcript.write_text(
        '{"type": "assistant", "message": {"role": "assistant", "content": ['
        '{"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "a.py"}}]}}\n'
        '{"type": "user", "timestamp": "2026-07-06T18:56:19.617Z", "message": {"role": "user", '
        '"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok", '
        '"is_error": false}]}}\n'
    )
    (subagents_dir / "agent-a1.meta.json").write_text(
        '{"agentType": "researcher", "toolUseId": "toolu_1", "spawnDepth": 1}'
    )

    result = CliRunner().invoke(
        main, ["--store", str(store_path), "session", "--file", str(transcript)]
    )

    assert result.exit_code == 0
    assert "subagent" in result.output
    assert "name_source=meta_agent_type" in result.output

    conn = sqlite3.connect(store_path)
    try:
        rows = conn.execute(
            "SELECT tool_name FROM fact_tool_event WHERE session_id = 'a1' ORDER BY seq"
        ).fetchall()
        assert [r[0] for r in rows] == ["Read"]
    finally:
        conn.close()


def test_store_flag_refuses_dot_claude_location(isolated_home: Path, tmp_path: Path) -> None:
    bad_store = isolated_home / ".claude" / "agentlens.db"
    result = CliRunner().invoke(main, ["--store", str(bad_store), "session"])
    assert result.exit_code != 0


def test_session_command_persists_fact_session_and_skill_bridge(
    isolated_home: Path, tmp_path: Path
) -> None:
    """The single-session path also upserts the full grain (not just
    `fact_tool_event`), per the session-parser spec's "Idempotent ingest"
    requirement.
    """
    store_path = tmp_path / "store.db"
    subagents_dir = isolated_home / ".claude" / "projects" / "-proj" / "parent-sid" / "subagents"
    subagents_dir.mkdir(parents=True)

    transcript = subagents_dir / "agent-a1.jsonl"
    transcript.write_text(
        '{"type": "assistant", "message": {"role": "assistant", "content": ['
        '{"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "a.py"}}]}}\n'
        '{"type": "user", "timestamp": "2026-07-06T18:56:19.617Z", "message": {"role": "user", '
        '"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok", '
        '"is_error": false}]}}\n'
    )
    (subagents_dir / "agent-a1.meta.json").write_text(
        '{"agentType": "researcher", "toolUseId": "toolu_1", "spawnDepth": 1}'
    )

    result = CliRunner().invoke(
        main, ["--store", str(store_path), "session", "--file", str(transcript)]
    )
    assert result.exit_code == 0

    conn = sqlite3.connect(store_path)
    try:
        row = conn.execute(
            "SELECT agent_type, n_reads FROM fact_session WHERE session_id = 'a1'"
        ).fetchone()
        assert row == ("researcher", 1)
    finally:
        conn.close()


def test_ingest_command_populates_store_and_exits_zero(tmp_path: Path) -> None:
    claude_home = tmp_path / "custom-claude"
    project_dir = claude_home / "projects" / "-proj"
    project_dir.mkdir(parents=True)
    (project_dir / "main-sid.jsonl").write_text(
        '{"type": "assistant", "message": {"role": "assistant", "content": []}}\n'
    )

    store_path = tmp_path / "store.db"
    result = CliRunner().invoke(
        main,
        ["--store", str(store_path), "ingest", "--claude-home", str(claude_home)],
    )

    assert result.exit_code == 0
    assert "1 sessions" in result.output

    conn = sqlite3.connect(store_path)
    try:
        count = conn.execute("SELECT COUNT(*) FROM fact_session").fetchone()[0]
        assert count == 1
    finally:
        conn.close()


def test_ingest_command_limit_bounds_the_run(tmp_path: Path) -> None:
    claude_home = tmp_path / "custom-claude"
    project_dir = claude_home / "projects" / "-proj"
    project_dir.mkdir(parents=True)
    for i in range(3):
        (project_dir / f"main-sid-{i}.jsonl").write_text(
            '{"type": "assistant", "message": {"role": "assistant", "content": []}}\n'
        )

    store_path = tmp_path / "store.db"
    result = CliRunner().invoke(
        main,
        [
            "--store",
            str(store_path),
            "ingest",
            "--claude-home",
            str(claude_home),
            "--limit",
            "2",
        ],
    )

    assert result.exit_code == 0
    conn = sqlite3.connect(store_path)
    try:
        count = conn.execute("SELECT COUNT(*) FROM fact_session").fetchone()[0]
        assert count == 2
    finally:
        conn.close()


def test_report_json_emits_verdict_slice_with_no_scores(tmp_path: Path) -> None:
    claude_home = tmp_path / "custom-claude"
    project_dir = claude_home / "projects" / "-proj"
    project_dir.mkdir(parents=True)
    (project_dir / "main-sid.jsonl").write_text(
        '{"type": "assistant", "message": {"role": "assistant", "content": []}}\n'
    )

    store_path = tmp_path / "store.db"
    CliRunner().invoke(
        main, ["--store", str(store_path), "ingest", "--claude-home", str(claude_home)]
    )

    result = CliRunner().invoke(
        main, ["--store", str(store_path), "report", "--since", "7d", "--json"]
    )
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert "score" not in json.dumps(payload).lower()
    assert "window" in payload


def test_report_does_not_ingest_uningested_sessions_on_disk(
    isolated_home: Path, tmp_path: Path
) -> None:
    store_path = tmp_path / "store.db"
    # Populate the store first so the store file/schema exists.
    CliRunner().invoke(main, ["--store", str(store_path), "session"])

    # Now sessions exist on disk but were never ingested.
    project_dir = isolated_home / ".claude" / "projects" / "-proj"
    project_dir.mkdir(parents=True)
    (project_dir / "main-sid.jsonl").write_text(
        '{"type": "assistant", "message": {"role": "assistant", "content": []}}\n'
    )

    result = CliRunner().invoke(main, ["--store", str(store_path), "report", "--since", "7d"])
    assert result.exit_code == 0

    conn = sqlite3.connect(store_path)
    try:
        count = conn.execute("SELECT COUNT(*) FROM fact_session").fetchone()[0]
        assert count == 0  # report never ingested the on-disk session
    finally:
        conn.close()


def test_claude_home_flag_injects_target_without_touching_home(tmp_path: Path) -> None:
    """`--claude-home` lets a caller point at a `.claude` dir directly, so tests
    (or scripted runs) can isolate discovery without monkeypatching HOME.
    """
    custom_claude_home = tmp_path / "custom-claude"
    subagents_dir = custom_claude_home / "projects" / "-proj" / "parent-sid" / "subagents"
    subagents_dir.mkdir(parents=True)

    transcript = subagents_dir / "agent-a1.jsonl"
    transcript.write_text(
        '{"type": "assistant", "message": {"role": "assistant", "content": ['
        '{"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "a.py"}}]}}\n'
        '{"type": "user", "timestamp": "2026-07-06T18:56:19.617Z", "message": {"role": "user", '
        '"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok", '
        '"is_error": false}]}}\n'
    )
    (subagents_dir / "agent-a1.meta.json").write_text(
        '{"agentType": "researcher", "toolUseId": "toolu_1", "spawnDepth": 1}'
    )

    store_path = tmp_path / "store.db"
    result = CliRunner().invoke(
        main,
        [
            "--store",
            str(store_path),
            "session",
            "a1",
            "--claude-home",
            str(custom_claude_home),
        ],
    )

    assert result.exit_code == 0
    assert "subagent" in result.output
    assert "name_source=meta_agent_type" in result.output
