"""Tests for `agentlens.judge.claude_cli`: subprocess mocking only, per the
project's synthetic-only test policy — no real `claude` invocation."""

from __future__ import annotations

import json
import subprocess
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from agentlens.errors import JudgeError, JudgeTimeoutError, JudgeUnavailableError
from agentlens.judge.claude_cli import ClaudeCliJudge

MOCK_ENVELOPE: dict[str, Any] = {
    "result": "",
    "structured_output": {
        "dimensions": {
            "task_completion": {"score": 4, "evidence": ["completed all tasks"]},
            "honesty": {"score": 5, "evidence": ["report matches actions"]},
            "efficiency": {"score": 3, "evidence": ["some unnecessary reads"]},
            "scope_adherence": {"score": 4, "evidence": ["stayed within brief"]},
        },
        "overall_score": 4.0,
        "suggested_fixes": ["reduce redundant Read calls"],
    },
    "is_error": False,
    "session_id": "judge-session-123",
    "total_cost_usd": 0.019,
    "usage": {"input_tokens": 3200, "output_tokens": 850},
    "duration_ms": 5200,
}


@patch("agentlens.judge.claude_cli.subprocess.run")
@patch("agentlens.judge.claude_cli.shutil.which", return_value="/usr/bin/claude")
def test_successful_scoring(mock_which: MagicMock, mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(
        returncode=0, stdout=json.dumps(MOCK_ENVELOPE), stderr=""
    )
    judge = ClaudeCliJudge(model="sonnet")

    verdict = judge.score("transcript text", "v1")

    assert verdict.overall_score == 4.0
    assert verdict.session_id == "judge-session-123"
    assert verdict.rubric_version == "v1"
    assert verdict.judge_model == "sonnet"
    assert verdict.judge_cost_usd == 0.019
    assert verdict.judge_input_tokens == 3200
    assert verdict.judge_output_tokens == 850
    assert verdict.suggested_fixes == ["reduce redundant Read calls"]
    assert verdict.dimensions["task_completion"].score == 4
    assert verdict.dimensions["task_completion"].evidence == ["completed all tasks"]

    mock_run.assert_called_once()
    call_args, call_kwargs = mock_run.call_args
    assert call_kwargs["input"] == "transcript text"
    assert call_kwargs["timeout"] == 60
    args_list = call_args[0]
    assert "--model" in args_list
    assert args_list[args_list.index("--model") + 1] == "sonnet"


@patch("agentlens.judge.claude_cli.subprocess.run")
@patch("agentlens.judge.claude_cli.shutil.which", return_value="/usr/bin/claude")
def test_timeout_raises_judge_timeout_error(mock_which: MagicMock, mock_run: MagicMock) -> None:
    mock_run.side_effect = subprocess.TimeoutExpired(cmd="claude", timeout=60)
    judge = ClaudeCliJudge(model="sonnet", timeout_seconds=60)

    with pytest.raises(JudgeTimeoutError):
        judge.score("transcript text", "v1")


@patch("agentlens.judge.claude_cli.subprocess.run")
@patch("agentlens.judge.claude_cli.shutil.which", return_value="/usr/bin/claude")
def test_nonzero_exit_raises_judge_error(mock_which: MagicMock, mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="something went wrong")
    judge = ClaudeCliJudge(model="sonnet")

    with pytest.raises(JudgeError):
        judge.score("transcript text", "v1")


@patch("agentlens.judge.claude_cli.subprocess.run")
@patch("agentlens.judge.claude_cli.shutil.which", return_value="/usr/bin/claude")
def test_is_error_in_envelope_raises_judge_error(
    mock_which: MagicMock, mock_run: MagicMock
) -> None:
    envelope = {"is_error": True, "result": "auth expired"}
    mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(envelope), stderr="")
    judge = ClaudeCliJudge(model="sonnet")

    with pytest.raises(JudgeError, match="auth expired"):
        judge.score("transcript text", "v1")


@patch("agentlens.judge.claude_cli.subprocess.run")
@patch("agentlens.judge.claude_cli.shutil.which", return_value=None)
def test_claude_not_on_path_raises_unavailable(mock_which: MagicMock, mock_run: MagicMock) -> None:
    judge = ClaudeCliJudge(model="sonnet")

    with pytest.raises(JudgeUnavailableError):
        judge.score("transcript text", "v1")

    mock_run.assert_not_called()


@patch("agentlens.judge.claude_cli.subprocess.run")
@patch("agentlens.judge.claude_cli.shutil.which", return_value="/usr/bin/claude")
def test_malformed_json_raises_judge_error(mock_which: MagicMock, mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(returncode=0, stdout="not valid json {{{", stderr="")
    judge = ClaudeCliJudge(model="sonnet")

    with pytest.raises(JudgeError):
        judge.score("transcript text", "v1")
