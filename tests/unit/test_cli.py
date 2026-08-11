"""Tests for `agentlens.cli`: wiring, empty pipeline, and error reporting.

Most invocations redirect HOME to `tmp_path` (via monkeypatch) so tests
never touch the real `~/.claude` or `~/.cache/agentlens`. Some instead pass
`--claude-home` explicitly, which achieves the same isolation without
touching HOME at all.
"""

from __future__ import annotations

import json
import sqlite3
from importlib import metadata
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from agentlens.cli import main
from agentlens.ingest.orchestrator import DefinitionSyncSummary
from agentlens.judge.rubric import RUBRIC_VERSION
from agentlens.store.schema import REQUIRED_TABLES, create_store


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
    create_store(store_path).close()
    before = store_path.read_bytes()
    result = CliRunner().invoke(main, ["--store", str(store_path), "report", "--since", "7d"])
    assert result.exit_code == 0
    assert store_path.read_bytes() == before


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
            """
            SELECT fte.tool_name
            FROM fact_tool_event fte
            JOIN fact_session fs ON fs.session_id = fte.session_id
            WHERE fs.raw_session_id = 'a1'
            ORDER BY fte.seq
            """
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
            "SELECT agent_type, n_reads FROM fact_session WHERE raw_session_id = 'a1'"
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
    assert "Ingested: 1" in result.output
    assert "Skipped: 0" in result.output

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


def test_report_cli_requires_and_accepts_concrete_model_for_ambiguous_cohort(
    tmp_path: Path,
) -> None:
    store_path = tmp_path / "store.db"
    conn = create_store(store_path)
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO fact_session (
                    session_id, raw_session_id, source_project, session_kind,
                    judge_input_hash, agent_id, agent_type, session_date
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "qualified-s1",
                    "raw-s1",
                    "project-a",
                    "subagent",
                    "input-s1",
                    "raw-s1",
                    "implementer",
                    "2026-08-01",
                ),
            )
            for model, score in (
                ("claude-sonnet-5", 4.0),
                ("claude-opus-5", 2.0),
            ):
                conn.execute(
                    """
                    INSERT INTO fact_verdict (
                        session_id, judge_input_hash, rubric_version,
                        judge_model, verdict_json
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        "qualified-s1",
                        "input-s1",
                        RUBRIC_VERSION,
                        model,
                        json.dumps({"overall_score": score}),
                    ),
                )
    finally:
        conn.close()

    runner = CliRunner()
    ambiguous = runner.invoke(
        main,
        [
            "--store",
            str(store_path),
            "report",
            "--from",
            "2026-08-01",
            "--to",
            "2026-08-01",
            "--json",
        ],
    )
    assert ambiguous.exit_code != 0
    assert "pass --judge-model" in ambiguous.output

    selected = runner.invoke(
        main,
        [
            "--store",
            str(store_path),
            "report",
            "--from",
            "2026-08-01",
            "--to",
            "2026-08-01",
            "--judge-model",
            "claude-sonnet-5",
            "--json",
        ],
    )
    assert selected.exit_code == 0
    payload = json.loads(selected.output)
    assert payload["verdict_cohort"]["judge_model"] == "claude-sonnet-5"
    assert payload["sessions"][0]["session_id"] == "qualified-s1"
    assert payload["sessions"][0]["raw_session_id"] == "raw-s1"
    assert payload["sessions"][0]["verdict"]["overall_score"] == 4.0


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


@pytest.mark.parametrize("value", ["0", "-1"])
def test_ingest_rejects_non_positive_limit_before_store_work(
    tmp_path: Path,
    value: str,
) -> None:
    store_path = tmp_path / "store.db"
    with patch("agentlens.cli.create_store") as create_store_mock:
        result = CliRunner().invoke(
            main,
            ["--store", str(store_path), "ingest", "--limit", value],
        )

    assert result.exit_code == 2
    assert "Invalid value for '--limit'" in result.output
    create_store_mock.assert_not_called()
    assert not store_path.exists()


def test_partial_ingest_reports_all_counts_and_exits_nonzero(tmp_path: Path) -> None:
    claude_home = tmp_path / "claude-home"
    project_dir = claude_home / "projects" / "project"
    project_dir.mkdir(parents=True)
    (project_dir / "good.jsonl").write_text(
        '{"type": "assistant", "message": {"role": "assistant", "content": []}}\n'
    )
    bad_path = project_dir / "bad.jsonl"
    bad_path.write_text("not-json\n")
    store_path = tmp_path / "store.db"

    result = CliRunner().invoke(
        main,
        ["--store", str(store_path), "ingest", "--claude-home", str(claude_home)],
    )

    assert result.exit_code == 1
    assert "Ingested: 1" in result.output
    assert "Skipped: 1" in result.output
    assert "Degraded: 1" in result.output
    assert str(bad_path) in result.output
    with sqlite3.connect(store_path) as conn:
        assert conn.execute("SELECT raw_session_id FROM fact_session").fetchall() == [
            ("good",)
        ]


def test_store_location_error_is_actionable_click_error(
    isolated_home: Path,
) -> None:
    bad_store = isolated_home / ".claude" / "agentlens.db"

    result = CliRunner().invoke(main, ["--store", str(bad_store), "session"])
    unwrapped = CliRunner().invoke(
        main,
        ["--store", str(bad_store), "session"],
        standalone_mode=False,
    )

    assert result.exit_code != 0
    assert "Error:" in result.output
    assert "refusing to write" in result.output
    assert unwrapped.exception is not None
    assert unwrapped.exception.__cause__ is not None


@pytest.mark.parametrize("command", ["ingest", "report"])
def test_stale_store_is_actionable_click_error(tmp_path: Path, command: str) -> None:
    db_path = tmp_path / "agentlens.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE fact_session (session_id TEXT PRIMARY KEY)")
        conn.commit()
    finally:
        conn.close()
    db_path.chmod(0o600)

    result = CliRunner().invoke(main, ["--store", str(db_path), command])

    assert result.exit_code != 0
    assert "Error:" in result.output
    assert "delete it and re-run ingest" in result.output
    assert "Traceback" not in result.output


def test_programmer_error_is_not_translated_to_click_error(tmp_path: Path) -> None:
    with patch("agentlens.cli.resolve_store_path", side_effect=RuntimeError("programmer bug")):
        result = CliRunner().invoke(
            main,
            ["--store", str(tmp_path / "store.db"), "session"],
        )

    assert isinstance(result.exception, RuntimeError)
    assert "Error:" not in result.output


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (OSError("permission denied"), "filesystem operation failed"),
        (sqlite3.OperationalError("database is locked"), "store operation failed"),
    ],
)
def test_expected_io_errors_are_actionable_click_errors(
    tmp_path: Path,
    error: OSError | sqlite3.Error,
    message: str,
) -> None:
    with patch("agentlens.cli.create_store", side_effect=error):
        result = CliRunner().invoke(
            main,
            ["--store", str(tmp_path / "store.db"), "session"],
        )

    assert result.exit_code != 0
    assert "Error:" in result.output
    assert message in result.output


def test_version_matches_distribution_metadata() -> None:
    result = CliRunner().invoke(main, ["--version"])

    assert result.exit_code == 0
    assert metadata.version("agentlens") in result.output


def test_ambiguous_raw_session_id_is_actionable_click_error(tmp_path: Path) -> None:
    claude_home = tmp_path / "claude-home"
    for project in ("project-a", "project-b"):
        project_dir = claude_home / "projects" / project
        project_dir.mkdir(parents=True)
        (project_dir / "shared.jsonl").write_text("{}\n")

    result = CliRunner().invoke(
        main,
        [
            "--store",
            str(tmp_path / "store.db"),
            "session",
            "shared",
            "--claude-home",
            str(claude_home),
        ],
    )

    assert result.exit_code != 0
    assert "Error:" in result.output
    assert "project-a/main" in result.output
    assert "project-b/main" in result.output
    assert "Traceback" not in result.output


def test_definition_discovery_failure_preserves_ingest_and_exits_nonzero(
    tmp_path: Path,
) -> None:
    claude_home = tmp_path / "claude-home"
    project_dir = claude_home / "projects" / "project"
    project_dir.mkdir(parents=True)
    (project_dir / "session.jsonl").write_text(
        '{"type": "assistant", "message": {"content": []}}\n'
    )
    failed_path = str(claude_home / "agents")
    store_path = tmp_path / "store.db"

    with patch(
        "agentlens.cli.sync_agent_definitions",
        return_value=DefinitionSyncSummary(
            n_synced=0,
            failed_paths=(failed_path,),
        ),
    ):
        result = CliRunner().invoke(
            main,
            [
                "--store",
                str(store_path),
                "ingest",
                "--claude-home",
                str(claude_home),
            ],
        )

    assert result.exit_code == 1
    assert "Ingested: 1" in result.output
    assert "Discovery failures: 1" in result.output
    assert failed_path in result.output
    with sqlite3.connect(store_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM fact_session").fetchone()[0] == 1
