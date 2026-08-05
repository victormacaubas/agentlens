"""Tests for the `score` CLI command: dry-run listing, the confirmation
gate, `--max-sessions` capping, the missing-`claude` error, and the
all-scored short-circuit message.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from agentlens.cli import main
from agentlens.judge.rubric import RUBRIC_VERSION
from agentlens.store.models import SessionRecord
from agentlens.store.operations import upsert_session
from agentlens.store.schema import create_store

_TODAY = date.today().isoformat()

MOCK_ENVELOPE: dict[str, object] = {
    "result": "",
    "structured_output": {
        "dimensions": {
            "task_completion": {"score": 4, "evidence": ["did the thing"]},
            "honesty": {"score": 5, "evidence": ["accurate report"]},
            "efficiency": {"score": 4, "evidence": ["no redundant calls"]},
            "scope_adherence": {"score": 4, "evidence": ["stayed in scope"]},
        },
        "overall_score": 4.25,
        "suggested_fixes": [],
    },
    "is_error": False,
    "session_id": "judge-session",
    "total_cost_usd": 0.02,
    "usage": {"input_tokens": 1000, "output_tokens": 200},
}


def _session_record(session_id: str, task_description: str, **overrides: object) -> SessionRecord:
    defaults: dict[str, object] = {
        "session_id": session_id,
        "agent_id": session_id,
        "agent_type": "implementer",
        "name_source": "meta_agent_type",
        "session_kind": "subagent",
        "spawn_depth": 1,
        "parent_session_id": "parent-sid",
        "spawn_tool_use_id": "toolu_1",
        "task_description": task_description,
        "session_date": _TODAY,
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


def _setup_unscored_sessions(tmp_path: Path, n: int) -> tuple[Path, Path]:
    """Seed a store with `n` unscored subagent sessions and a matching
    `.claude`-shaped tree of empty transcript files under a fresh
    `claude_home`, so `score`'s `_discover_jsonl_paths` can find them.
    """
    
    claude_home = tmp_path / "claude-home"
    store_path = tmp_path / "store.db"
    conn = create_store(store_path)
    try:
        for i in range(n):
            agent_id = f"a{i}"
            subagents_dir = (
                claude_home / "projects" / "-proj" / "parent-sid" / "subagents"
            )
            subagents_dir.mkdir(parents=True, exist_ok=True)
            (subagents_dir / f"agent-{agent_id}.jsonl").write_text("")
            upsert_session(conn, _session_record(agent_id, f"task {i}"))
    finally:
        conn.close()
    return store_path, claude_home


def _insert_verdict(store_path: Path, session_id: str, *, judge_model: str = "sonnet") -> None:
    conn = sqlite3.connect(store_path)
    try:
        with conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO fact_verdict
                    (session_id, rubric_version, judge_model, verdict_json,
                     judge_cost_usd, judge_input_tokens, judge_output_tokens)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    RUBRIC_VERSION,
                    judge_model,
                    json.dumps({"dimensions": {}, "overall_score": 4.0, "suggested_fixes": []}),
                    0.02,
                    100,
                    50,
                ),
            )
    finally:
        conn.close()


def test_dry_run_lists_sessions(tmp_path: Path) -> None:
    store_path, claude_home = _setup_unscored_sessions(tmp_path, 3)

    result = CliRunner().invoke(
        main,
        [
            "--store",
            str(store_path),
            "score",
            "--dry-run",
            "--since",
            "30d",
            "--claude-home",
            str(claude_home),
        ],
    )

    assert result.exit_code == 0
    assert "task 0" in result.output
    assert "task 1" in result.output
    assert "task 2" in result.output
    assert "estimated cost" in result.output.lower()


@patch("agentlens.judge.claude_cli.subprocess.run")
@patch("agentlens.judge.claude_cli.shutil.which", return_value="/usr/bin/claude")
@patch("agentlens.cli.shutil.which", return_value="/usr/bin/claude")
def test_no_confirm_skips_prompt(
    mock_cli_which: MagicMock, mock_judge_which: MagicMock, mock_run: MagicMock, tmp_path: Path
) -> None:
    mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(MOCK_ENVELOPE), stderr="")
    store_path, claude_home = _setup_unscored_sessions(tmp_path, 3)

    result = CliRunner().invoke(
        main,
        [
            "--store",
            str(store_path),
            "score",
            "--since",
            "30d",
            "--no-confirm",
            "--claude-home",
            str(claude_home),
        ],
    )

    assert result.exit_code == 0
    assert "Proceed?" not in result.output
    assert "Scored 3/3" in result.output

    conn = sqlite3.connect(store_path)
    try:
        n_verdicts = conn.execute("SELECT COUNT(*) FROM fact_verdict").fetchone()[0]
        assert n_verdicts == 3
    finally:
        conn.close()


@patch("agentlens.judge.claude_cli.subprocess.run")
@patch("agentlens.judge.claude_cli.shutil.which", return_value="/usr/bin/claude")
@patch("agentlens.cli.shutil.which", return_value="/usr/bin/claude")
def test_max_sessions_cap(
    mock_cli_which: MagicMock, mock_judge_which: MagicMock, mock_run: MagicMock, tmp_path: Path
) -> None:
    mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(MOCK_ENVELOPE), stderr="")
    store_path, claude_home = _setup_unscored_sessions(tmp_path, 5)

    result = CliRunner().invoke(
        main,
        [
            "--store",
            str(store_path),
            "score",
            "--since",
            "30d",
            "--no-confirm",
            "--max-sessions",
            "2",
            "--claude-home",
            str(claude_home),
        ],
    )

    assert result.exit_code == 0
    assert "2/5 scored" in result.output

    conn = sqlite3.connect(store_path)
    try:
        n_verdicts = conn.execute("SELECT COUNT(*) FROM fact_verdict").fetchone()[0]
        assert n_verdicts == 2
    finally:
        conn.close()


@patch("agentlens.cli.shutil.which", return_value=None)
def test_error_when_claude_missing(mock_which: MagicMock, tmp_path: Path) -> None:
    store_path, claude_home = _setup_unscored_sessions(tmp_path, 1)

    result = CliRunner().invoke(
        main,
        [
            "--store",
            str(store_path),
            "score",
            "--since",
            "30d",
            "--no-confirm",
            "--claude-home",
            str(claude_home),
        ],
    )

    assert result.exit_code != 0
    assert "claude" in result.output.lower()


def test_all_scored_message(tmp_path: Path) -> None:
    store_path, claude_home = _setup_unscored_sessions(tmp_path, 2)
    _insert_verdict(store_path, "a0")
    _insert_verdict(store_path, "a1")

    result = CliRunner().invoke(
        main,
        [
            "--store",
            str(store_path),
            "score",
            "--since",
            "30d",
            "--no-confirm",
            "--claude-home",
            str(claude_home),
        ],
    )

    assert result.exit_code == 0
    assert "all sessions already scored" in result.output
