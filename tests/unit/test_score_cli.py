"""Tests for the `score` CLI command: dry-run listing, the confirmation
gate and its upper-bound framing for a floating model alias, `--max-sessions`
capping, the missing-`claude` error, the all-scored short-circuit message,
and the final summary's resolved-model note.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from agentlens.cli import main
from agentlens.discovery.models import SourceIdentity
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
    "modelUsage": {
        "claude-sonnet-5": {
            "inputTokens": 1000,
            "outputTokens": 200,
            "cacheReadInputTokens": 0,
            "cacheCreationInputTokens": 0,
            "webSearchRequests": 0,
            "costUSD": 0.02,
            "contextWindow": 200000,
            "maxOutputTokens": 64000,
            "canonicalModel": "claude-sonnet-5",
            "provider": "firstParty",
        }
    },
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
            session_id = SourceIdentity("-proj", "subagent", agent_id).session_id
            subagents_dir = (
                claude_home / "projects" / "-proj" / "parent-sid" / "subagents"
            )
            subagents_dir.mkdir(parents=True, exist_ok=True)
            (subagents_dir / f"agent-{agent_id}.jsonl").write_text("")
            upsert_session(
                conn,
                _session_record(
                    session_id,
                    f"task {i}",
                    raw_session_id=agent_id,
                    source_project="-proj",
                    agent_id=agent_id,
                ),
            )
    finally:
        conn.close()
    return store_path, claude_home


def _insert_verdict(
    store_path: Path,
    session_id: str,
    *,
    judge_model: str = "claude-sonnet-5",
) -> None:
    raw_session_id = session_id
    conn = sqlite3.connect(store_path)
    try:
        stored_session_id = conn.execute(
            "SELECT session_id FROM fact_session WHERE raw_session_id = ?",
            (raw_session_id,),
        ).fetchone()[0]
        judge_input_hash = f"input-{stored_session_id}"
        with conn:
            conn.execute(
                "UPDATE fact_session SET judge_input_hash = ? WHERE session_id = ?",
                (judge_input_hash, stored_session_id),
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO fact_verdict
                    (session_id, judge_input_hash, rubric_version, judge_model, verdict_json,
                     judge_cost_usd, judge_input_tokens, judge_output_tokens)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    stored_session_id,
                    judge_input_hash,
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
    # --judge-model defaults to the "sonnet" alias, which has no verdict row
    # keyed under the alias itself, so the count is an upper bound.
    assert "up to 3 sessions" in result.output


def test_dry_run_shows_exact_count_for_a_concrete_model(tmp_path: Path) -> None:
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
            "--judge-model",
            "claude-sonnet-5",
            "--claude-home",
            str(claude_home),
        ],
    )

    assert result.exit_code == 0
    assert "for 3 sessions" in result.output
    assert "up to" not in result.output


def test_confirmation_prompt_shows_upper_bound_for_alias(tmp_path: Path) -> None:
    store_path, claude_home = _setup_unscored_sessions(tmp_path, 3)

    result = CliRunner().invoke(
        main,
        [
            "--store",
            str(store_path),
            "score",
            "--since",
            "30d",
            "--claude-home",
            str(claude_home),
        ],
        input="n\n",
    )

    assert result.exit_code == 0
    assert "Will score up to 3 sessions" in result.output

    conn = sqlite3.connect(store_path)
    try:
        n_verdicts = conn.execute("SELECT COUNT(*) FROM fact_verdict").fetchone()[0]
        assert n_verdicts == 0
    finally:
        conn.close()


@patch("agentlens.judge.claude_cli.subprocess.run")
@patch("agentlens.judge.claude_cli.shutil.which", return_value="/usr/bin/claude")
def test_no_confirm_skips_prompt(
    mock_judge_which: MagicMock, mock_run: MagicMock, tmp_path: Path
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
    assert "Attempts: 3" in result.output
    assert "Scored: 3" in result.output
    assert "Remaining: 0" in result.output
    # The configured value was the "sonnet" alias; the summary names the
    # concrete model the judge resolved it to.
    assert "Resolved 'sonnet' to claude-sonnet-5" in result.output

    conn = sqlite3.connect(store_path)
    try:
        n_verdicts = conn.execute("SELECT COUNT(*) FROM fact_verdict").fetchone()[0]
        assert n_verdicts == 3
    finally:
        conn.close()


@patch("agentlens.judge.claude_cli.subprocess.run")
@patch("agentlens.judge.claude_cli.shutil.which", return_value="/usr/bin/claude")
def test_max_sessions_cap(
    mock_judge_which: MagicMock, mock_run: MagicMock, tmp_path: Path
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
    assert "Attempts: 2" in result.output
    assert "Scored: 2" in result.output
    assert "Remaining: 3" in result.output
    assert "--max-sessions reached" in result.output
    # A capped run still names the resolved model: each scored session's
    # own judge call resolves it even though `score_window`'s own
    # resolution flow is bypassed while the cap is in effect.
    assert "Resolved 'sonnet' to claude-sonnet-5" in result.output

    conn = sqlite3.connect(store_path)
    try:
        n_verdicts = conn.execute("SELECT COUNT(*) FROM fact_verdict").fetchone()[0]
        assert n_verdicts == 2
    finally:
        conn.close()


@patch("agentlens.judge.claude_cli.shutil.which", return_value=None)
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
    assert "judge unavailable" in result.output.lower()
    assert "authenticate" in result.output.lower()


@patch(
    "agentlens.judge.claude_cli.subprocess.run",
    side_effect=AssertionError("all-scored cache hit invoked the real judge path"),
)
@patch(
    "agentlens.judge.claude_cli.shutil.which",
    side_effect=AssertionError("all-scored cache hit checked the real judge path"),
)
def test_all_scored_message(
    mock_which: MagicMock,
    mock_run: MagicMock,
    tmp_path: Path,
) -> None:
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
            "--judge-model",
            "claude-sonnet-5",
            "--claude-home",
            str(claude_home),
        ],
    )

    assert result.exit_code == 0
    assert "all sessions already scored" in result.output
    assert "Attempts: 0" in result.output
    assert "Judge model: claude-sonnet-5" in result.output
    mock_which.assert_not_called()
    mock_run.assert_not_called()


@pytest.mark.parametrize("value", ["0", "-3"])
def test_non_positive_max_sessions_is_rejected_before_store_or_judge_work(
    value: str,
    tmp_path: Path,
) -> None:
    store_path = tmp_path / "store.db"
    with (
        patch("agentlens.cli.create_store") as create_store_mock,
        patch("agentlens.judge.claude_cli.subprocess.run") as subprocess_mock,
    ):
        result = CliRunner().invoke(
            main,
            ["--store", str(store_path), "score", "--max-sessions", value],
        )

    assert result.exit_code == 2
    assert "Invalid value for '--max-sessions'" in result.output
    create_store_mock.assert_not_called()
    subprocess_mock.assert_not_called()
    assert not store_path.exists()


@patch(
    "agentlens.judge.claude_cli.shutil.which",
    return_value="/usr/bin/claude",
)
@patch("agentlens.judge.claude_cli.subprocess.run")
def test_capped_failure_summary_is_complete_and_exits_nonzero(
    mock_run: MagicMock,
    mock_which: MagicMock,
    tmp_path: Path,
) -> None:
    failed = MagicMock(returncode=1, stdout="", stderr="synthetic judge failure")
    succeeded = MagicMock(returncode=0, stdout=json.dumps(MOCK_ENVELOPE), stderr="")
    mock_run.side_effect = [failed, succeeded]
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
            "--max-sessions",
            "2",
            "--claude-home",
            str(claude_home),
        ],
    )

    assert result.exit_code == 1
    assert "Attempts: 2" in result.output
    assert "Scored: 1" in result.output
    assert "Skipped: 1" in result.output
    assert "Remaining: 2" in result.output
    assert "Aborted: no" in result.output
    assert "--max-sessions reached" in result.output
    assert "Resolved 'sonnet' to claude-sonnet-5" in result.output
    assert mock_run.call_count == 2


@patch(
    "agentlens.judge.claude_cli.shutil.which",
    return_value="/usr/bin/claude",
)
@patch("agentlens.judge.claude_cli.subprocess.run")
def test_abort_summary_preserves_success_count_and_exits_nonzero(
    mock_run: MagicMock,
    mock_which: MagicMock,
    tmp_path: Path,
) -> None:
    mock_run.return_value = MagicMock(
        returncode=1,
        stdout="",
        stderr="synthetic judge failure",
    )
    store_path, claude_home = _setup_unscored_sessions(tmp_path, 4)

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

    assert result.exit_code == 1
    assert "Attempts: 3" in result.output
    assert "Scored: 0" in result.output
    assert "Skipped: 3" in result.output
    assert "Remaining: 4" in result.output
    assert "Aborted: yes" in result.output
    assert "Resolved model: unresolved" in result.output
    assert mock_run.call_count == 3
