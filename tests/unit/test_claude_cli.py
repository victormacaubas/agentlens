"""Tests for `agentlens.judge.claude_cli`: subprocess mocking only, per the
project's synthetic-only test policy — no real `claude` invocation.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from agentlens.errors import JudgeError, JudgeTimeoutError, JudgeUnavailableError
from agentlens.judge.claude_cli import DEFAULT_TIMEOUT_SECONDS, ClaudeCliJudge

NOT_LOGGED_IN_ENVELOPE: dict[str, Any] = {
    "is_error": True,
    "duration_api_ms": 0,
    "num_turns": 1,
    "stop_reason": "stop_sequence",
    "session_id": "83f7dfd1-4ec6-471e-aa51-08208fde1743",
    "total_cost_usd": 0,
    "usage": {"input_tokens": 0, "output_tokens": 0},
    "modelUsage": {},
    "permission_denials": [],
    "terminal_reason": "api_error",
    "subtype": "success",
    "api_error_status": None,
    "result": "Not logged in · Please run /login",
    "type": "result",
    "duration_ms": 16,
}

def _model_usage_entry(
    *, input_tokens: int = 3200, output_tokens: int = 850, canonical_model: str | None = None
) -> dict[str, Any]:
    """Build a single `modelUsage` entry with the shape a real envelope
    reports, so tests only need to vary the fields they care about.
    """
    return {
        "inputTokens": input_tokens,
        "outputTokens": output_tokens,
        "cacheReadInputTokens": 0,
        "cacheCreationInputTokens": 0,
        "webSearchRequests": 0,
        "costUSD": 0.019,
        "contextWindow": 200000,
        "maxOutputTokens": 64000,
        "canonicalModel": canonical_model if canonical_model is not None else "claude-sonnet-5",
        "provider": "firstParty",
    }


MOCK_ENVELOPE: dict[str, Any] = {
    "result": "",
    "structured_output": {
        "dimensions": {
            "task_completion": {"score": 4, "evidence": ["completed all tasks"]},
            "honesty": {"score": 5, "evidence": ["report matches actions"]},
            "efficiency": {"score": 3, "evidence": ["some unnecessary reads"]},
            "scope_adherence": {"score": 4, "evidence": ["stayed within brief"]},
        },
        "suggested_fixes": ["reduce redundant Read calls"],
    },
    "is_error": False,
    "session_id": "judge-session-123",
    "total_cost_usd": 0.019,
    "usage": {"input_tokens": 3200, "output_tokens": 850},
    "modelUsage": {"claude-sonnet-5": _model_usage_entry()},
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
    assert verdict.judge_model == "claude-sonnet-5"
    assert verdict.judge_cost_usd == 0.019
    assert verdict.judge_input_tokens == 3200
    assert verdict.judge_output_tokens == 850
    assert verdict.suggested_fixes == ["reduce redundant Read calls"]
    assert verdict.dimensions["task_completion"].score == 4
    assert verdict.dimensions["task_completion"].evidence == ["completed all tasks"]
    assert judge.resolved_model == "claude-sonnet-5"

    mock_run.assert_called_once()
    call_args, call_kwargs = mock_run.call_args
    assert call_kwargs["input"] == "transcript text"
    assert call_kwargs["timeout"] == DEFAULT_TIMEOUT_SECONDS
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


def test_build_args_grants_no_filesystem_tools() -> None:
    """Fast complement to the canary integration test: asserts the
    argument list positively disables the built-in tool set and pins setting
    sources, not merely that a tool-granting flag is absent — omitting
    `--allowedTools` is not equivalent to denying tools.
    """
    judge = ClaudeCliJudge(model="sonnet")

    args = judge._build_args()

    assert "--tools" in args
    assert args[args.index("--tools") + 1] == ""
    assert "--setting-sources" in args
    assert args[args.index("--setting-sources") + 1] == "user"

    assert "--allowedTools" not in args
    assert "Read" not in args
    assert "Grep" not in args
    assert "Bash" not in args
    assert "--permission-mode" not in args
    assert "dontAsk" not in args


@patch("agentlens.judge.claude_cli.subprocess.run")
@patch("agentlens.judge.claude_cli.shutil.which", return_value="/usr/bin/claude")
def test_subprocess_launch_oserror_raises_judge_error(
    mock_which: MagicMock, mock_run: MagicMock
) -> None:
    mock_run.side_effect = OSError("No such file or directory")
    judge = ClaudeCliJudge(model="sonnet")

    with pytest.raises(JudgeError):
        judge.score("transcript text", "v1")


@patch("agentlens.judge.claude_cli.subprocess.run")
@patch("agentlens.judge.claude_cli.shutil.which", return_value="/usr/bin/claude")
def test_overall_score_derived_not_trusted(mock_which: MagicMock, mock_run: MagicMock) -> None:
    envelope: dict[str, Any] = {
        "result": "",
        "structured_output": {
            "dimensions": {
                "task_completion": {"score": 4, "evidence": ["completed all tasks"]},
                "honesty": {"score": 5, "evidence": ["report matches actions"]},
                "efficiency": {"score": 3, "evidence": ["some unnecessary reads"]},
                "scope_adherence": {"score": 4, "evidence": ["stayed within brief"]},
            },
            "overall_score": 99,
            "suggested_fixes": [],
        },
        "is_error": False,
        "session_id": "judge-session-123",
        "total_cost_usd": 0.019,
        "usage": {"input_tokens": 3200, "output_tokens": 850},
        "modelUsage": {"claude-sonnet-5": _model_usage_entry()},
    }
    mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(envelope), stderr="")
    judge = ClaudeCliJudge(model="sonnet")

    verdict = judge.score("transcript text", "v1")

    assert verdict.overall_score == 4.0


@patch("agentlens.judge.claude_cli.subprocess.run")
@patch("agentlens.judge.claude_cli.shutil.which", return_value="/usr/bin/claude")
def test_dimension_score_out_of_range_rejected(
    mock_which: MagicMock, mock_run: MagicMock
) -> None:
    envelope: dict[str, Any] = {
        "result": "",
        "structured_output": {
            "dimensions": {
                "task_completion": {"score": 6, "evidence": ["completed all tasks"]},
                "honesty": {"score": 5, "evidence": ["report matches actions"]},
                "efficiency": {"score": 3, "evidence": ["some unnecessary reads"]},
                "scope_adherence": {"score": 4, "evidence": ["stayed within brief"]},
            },
            "suggested_fixes": [],
        },
        "is_error": False,
        "session_id": "judge-session-123",
        "total_cost_usd": 0.019,
        "usage": {"input_tokens": 3200, "output_tokens": 850},
    }
    mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(envelope), stderr="")
    judge = ClaudeCliJudge(model="sonnet")

    with pytest.raises(JudgeError):
        judge.score("transcript text", "v1")


@patch("agentlens.judge.claude_cli.subprocess.run")
@patch("agentlens.judge.claude_cli.shutil.which", return_value="/usr/bin/claude")
def test_subprocess_uses_isolated_cwd(mock_which: MagicMock, mock_run: MagicMock) -> None:
    """The subprocess must not inherit agentlens's own working
    directory, where a repo's `.claude/settings.local.json` could live.
    """
    mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(MOCK_ENVELOPE), stderr="")
    judge = ClaudeCliJudge(model="sonnet")

    judge.score("transcript text", "v1")

    _, call_kwargs = mock_run.call_args
    cwd = call_kwargs["cwd"]
    assert cwd is not None
    assert Path(cwd).resolve() != Path.cwd().resolve()


@patch("agentlens.judge.claude_cli.subprocess.run")
@patch("agentlens.judge.claude_cli.shutil.which", return_value="/usr/bin/claude")
def test_subprocess_env_forwards_anthropic_and_drops_unrelated(
    mock_which: MagicMock, mock_run: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`ANTHROPIC_*` is forwarded by prefix so whichever auth channel a
    machine uses keeps working, while an unrelated env var is dropped.
    """
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "test-token")
    monkeypatch.setenv("AGENTLENS_UNRELATED_SENTINEL", "should-not-be-forwarded")
    mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(MOCK_ENVELOPE), stderr="")
    judge = ClaudeCliJudge(model="sonnet")

    judge.score("transcript text", "v1")

    _, call_kwargs = mock_run.call_args
    env = call_kwargs["env"]
    assert env["ANTHROPIC_AUTH_TOKEN"] == "test-token"
    assert "AGENTLENS_UNRELATED_SENTINEL" not in env


@patch("agentlens.judge.claude_cli.subprocess.run")
@patch("agentlens.judge.claude_cli.shutil.which", return_value="/usr/bin/claude")
def test_not_logged_in_raises_judge_unavailable_error(
    mock_which: MagicMock, mock_run: MagicMock
) -> None:
    """The CLI's not-logged-in response (non-zero exit, valid JSON
    envelope) raises `JudgeUnavailableError`, not `JudgeError`, naming both
    remedies — so the scoring loop doesn't count it as a per-session
    failure.
    """
    mock_run.return_value = MagicMock(
        returncode=1, stdout=json.dumps(NOT_LOGGED_IN_ENVELOPE), stderr=""
    )
    judge = ClaudeCliJudge(model="sonnet")

    with pytest.raises(JudgeUnavailableError) as exc_info:
        judge.score("transcript text", "v1")

    assert "ANTHROPIC_API_KEY" in str(exc_info.value)
    assert "apiKeyHelper" in str(exc_info.value)


@patch("agentlens.judge.claude_cli.subprocess.run")
@patch("agentlens.judge.claude_cli.shutil.which", return_value="/usr/bin/claude")
def test_nonzero_exit_non_json_stdout_falls_through_to_judge_error(
    mock_which: MagicMock, mock_run: MagicMock
) -> None:
    """The not-logged-in detection's fall-through: a non-zero exit with
    unparseable stdout is a different failure and must keep today's
    `JudgeError` behavior, with its message intact — the not-logged-in
    detection cannot swallow it.
    """
    mock_run.return_value = MagicMock(returncode=1, stdout="not valid json {{{", stderr="boom")
    judge = ClaudeCliJudge(model="sonnet")

    with pytest.raises(JudgeError) as exc_info:
        judge.score("transcript text", "v1")

    assert not isinstance(exc_info.value, JudgeUnavailableError)
    assert "boom" in str(exc_info.value)


@patch("agentlens.judge.claude_cli.subprocess.run")
@patch("agentlens.judge.claude_cli.shutil.which", return_value="/usr/bin/claude")
def test_dimension_score_negative_rejected(mock_which: MagicMock, mock_run: MagicMock) -> None:
    envelope: dict[str, Any] = {
        "result": "",
        "structured_output": {
            "dimensions": {
                "task_completion": {"score": -1, "evidence": ["completed all tasks"]},
                "honesty": {"score": 5, "evidence": ["report matches actions"]},
                "efficiency": {"score": 3, "evidence": ["some unnecessary reads"]},
                "scope_adherence": {"score": 4, "evidence": ["stayed within brief"]},
            },
            "suggested_fixes": [],
        },
        "is_error": False,
        "session_id": "judge-session-123",
        "total_cost_usd": 0.019,
        "usage": {"input_tokens": 3200, "output_tokens": 850},
    }
    mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(envelope), stderr="")
    judge = ClaudeCliJudge(model="sonnet")

    with pytest.raises(JudgeError):
        judge.score("transcript text", "v1")


def test_build_args_includes_settings_path_for_bare_auth() -> None:
    """`--bare` reads `apiKeyHelper` only via `--settings`, never through
    `--setting-sources`; both flags must be present for a machine that
    authenticates by `apiKeyHelper` to have a working credential channel.
    """
    judge = ClaudeCliJudge(model="sonnet")

    args = judge._build_args()

    assert "--settings" in args
    settings_path = args[args.index("--settings") + 1]
    assert settings_path == str(Path.home() / ".claude" / "settings.json")
    assert "--setting-sources" in args
    assert args[args.index("--setting-sources") + 1] == "user"


@patch("agentlens.judge.claude_cli.subprocess.run")
@patch("agentlens.judge.claude_cli.shutil.which", return_value="/usr/bin/claude")
def test_alias_resolves_to_concrete_model_identifier(
    mock_which: MagicMock, mock_run: MagicMock
) -> None:
    envelope = {**MOCK_ENVELOPE, "modelUsage": {"claude-sonnet-5": _model_usage_entry()}}
    mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(envelope), stderr="")
    judge = ClaudeCliJudge(model="sonnet")

    verdict = judge.score("transcript text", "v1")

    assert verdict.judge_model == "claude-sonnet-5"


@patch("agentlens.judge.claude_cli.subprocess.run")
@patch("agentlens.judge.claude_cli.shutil.which", return_value="/usr/bin/claude")
def test_pinned_model_identifier_passes_through_unchanged(
    mock_which: MagicMock, mock_run: MagicMock
) -> None:
    envelope = {**MOCK_ENVELOPE, "modelUsage": {"claude-sonnet-5": _model_usage_entry()}}
    mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(envelope), stderr="")
    judge = ClaudeCliJudge(model="claude-sonnet-5")

    verdict = judge.score("transcript text", "v1")

    assert verdict.judge_model == "claude-sonnet-5"


@patch("agentlens.judge.claude_cli.subprocess.run")
@patch("agentlens.judge.claude_cli.shutil.which", return_value="/usr/bin/claude")
def test_dated_snapshot_key_preferred_over_canonical_model(
    mock_which: MagicMock, mock_run: MagicMock
) -> None:
    """`haiku` is the case where the map key and `canonicalModel` diverge:
    the key carries the dated snapshot, `canonicalModel` the undated family
    name. The key must win.
    """
    envelope = {
        **MOCK_ENVELOPE,
        "modelUsage": {
            "claude-haiku-4-5-20251001": _model_usage_entry(canonical_model="claude-haiku-4-5")
        },
    }
    mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(envelope), stderr="")
    judge = ClaudeCliJudge(model="haiku")

    verdict = judge.score("transcript text", "v1")

    assert verdict.judge_model == "claude-haiku-4-5-20251001"


@patch("agentlens.judge.claude_cli.subprocess.run")
@patch("agentlens.judge.claude_cli.shutil.which", return_value="/usr/bin/claude")
def test_missing_model_usage_raises_judge_error(mock_which: MagicMock, mock_run: MagicMock) -> None:
    envelope = {k: v for k, v in MOCK_ENVELOPE.items() if k != "modelUsage"}
    mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(envelope), stderr="")
    judge = ClaudeCliJudge(model="sonnet")

    with pytest.raises(JudgeError):
        judge.score("transcript text", "v1")


@patch("agentlens.judge.claude_cli.subprocess.run")
@patch("agentlens.judge.claude_cli.shutil.which", return_value="/usr/bin/claude")
def test_empty_model_usage_raises_judge_error(mock_which: MagicMock, mock_run: MagicMock) -> None:
    envelope = {**MOCK_ENVELOPE, "modelUsage": {}}
    mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(envelope), stderr="")
    judge = ClaudeCliJudge(model="sonnet")

    with pytest.raises(JudgeError):
        judge.score("transcript text", "v1")


@patch("agentlens.judge.claude_cli.subprocess.run")
@patch("agentlens.judge.claude_cli.shutil.which", return_value="/usr/bin/claude")
def test_multi_entry_model_usage_raises_judge_error(
    mock_which: MagicMock, mock_run: MagicMock
) -> None:
    envelope = {
        **MOCK_ENVELOPE,
        "modelUsage": {
            "claude-sonnet-5": _model_usage_entry(),
            "claude-haiku-4-5-20251001": _model_usage_entry(canonical_model="claude-haiku-4-5"),
        },
    }
    mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(envelope), stderr="")
    judge = ClaudeCliJudge(model="sonnet")

    with pytest.raises(JudgeError):
        judge.score("transcript text", "v1")


@patch("agentlens.judge.claude_cli.subprocess.run")
@patch("agentlens.judge.claude_cli.shutil.which", return_value="/usr/bin/claude")
def test_resolved_model_exposed_after_successful_call(
    mock_which: MagicMock, mock_run: MagicMock
) -> None:
    mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(MOCK_ENVELOPE), stderr="")
    judge = ClaudeCliJudge(model="sonnet")
    assert judge.resolved_model is None

    judge.score("transcript text", "v1")

    assert judge.resolved_model == "claude-sonnet-5"


@patch("agentlens.judge.claude_cli.subprocess.run")
@patch("agentlens.judge.claude_cli.shutil.which", return_value="/usr/bin/claude")
def test_judge_input_tokens_counts_cache_creation_not_nominal_usage(
    mock_which: MagicMock, mock_run: MagicMock
) -> None:
    """A large, uncached prompt is booked almost entirely as cache creation:
    the envelope's top-level `usage.input_tokens` reports a nominal 1 while
    the real consumption lives in `modelUsage`'s cache-creation count.
    """
    envelope: dict[str, Any] = {
        **MOCK_ENVELOPE,
        "total_cost_usd": 0.0710,
        "usage": {"input_tokens": 1, "output_tokens": 1523, "cache_read_input_tokens": 0},
        "modelUsage": {
            "claude-sonnet-5": {
                "inputTokens": 1,
                "outputTokens": 1523,
                "cacheReadInputTokens": 0,
                "cacheCreationInputTokens": 12843,
                "webSearchRequests": 0,
                "costUSD": 0.0710,
                "contextWindow": 200000,
                "maxOutputTokens": 64000,
                "canonicalModel": "claude-sonnet-5",
                "provider": "firstParty",
            }
        },
    }
    mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(envelope), stderr="")
    judge = ClaudeCliJudge(model="sonnet")

    verdict = judge.score("transcript text", "v1")

    assert verdict.judge_input_tokens == 12844
    assert verdict.judge_output_tokens == 1523
    assert verdict.judge_cost_usd == 0.0710
