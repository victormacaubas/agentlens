"""``ClaudeCliJudge``'s envelope parsing and error translation.

Every scenario here runs against a small stub script standing in for the
``claude`` binary, never the real installed CLI: this package's own
integration canary is the only test that touches that binary. The stub lets
every envelope shape and every subprocess failure mode be reproduced
deterministically and for free.
"""

import inspect
import json
from pathlib import Path

import pytest

from agentlens.errors import JudgeResponseError, JudgeUnavailableError
from agentlens.judge.cli_backend import ClaudeCliJudge
from agentlens.models.judging import JudgeResponse

_SHORT_TIMEOUT_S = 0.2


def _write_stub_script(
    tmp_path: Path, *, stdout: str, stderr: str = "", sleep_s: float = 0.0
) -> Path:
    script = tmp_path / "fake-claude"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import sys, time\n"
        f"time.sleep({sleep_s!r})\n"
        f"sys.stderr.write({stderr!r})\n"
        f"sys.stdout.write({stdout!r})\n"
    )
    script.chmod(0o755)
    return script


def _judge(stub: Path, *, timeout_s: float = 5.0) -> ClaudeCliJudge:
    return ClaudeCliJudge(
        binary=str(stub),
        timeout_s=timeout_s,
        settings_path=Path("/no-such-settings.json"),
    )


def _envelope(**overrides: object) -> dict[str, object]:
    envelope: dict[str, object] = {
        "is_error": False,
        "subtype": "success",
        "result": None,
        "total_cost_usd": 0.011002,
        "usage": {
            "input_tokens": 675,
            "output_tokens": 52,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        },
        "modelUsage": {"claude-sonnet-5": {"canonicalModel": "claude-sonnet-5"}},
        "duration_ms": 4820,
        "structured_output": {"overall_score": 5},
    }
    envelope.update(overrides)
    return envelope


def test_successful_envelope_is_parsed_into_a_judge_response(tmp_path: Path) -> None:
    stub = _write_stub_script(tmp_path, stdout=json.dumps(_envelope()))

    response = _judge(stub).score("Score this run.", model="sonnet")

    assert response == JudgeResponse(
        resolved_model="claude-sonnet-5",
        is_error=False,
        raw_result=None,
        structured_output={"overall_score": 5},
        cost_usd=0.011002,
        input_tokens=675,
        output_tokens=52,
        duration_ms=4820,
    )


def test_is_error_true_is_trusted_even_when_subtype_reports_success(tmp_path: Path) -> None:
    stub = _write_stub_script(
        tmp_path,
        stdout=json.dumps(
            _envelope(is_error=True, subtype="success", result="something went wrong")
        ),
    )

    with pytest.raises(JudgeResponseError, match="something went wrong"):
        _judge(stub).score("Score this run.", model="sonnet")


def test_total_cost_usd_arriving_as_an_int_is_accepted(tmp_path: Path) -> None:
    stub = _write_stub_script(tmp_path, stdout=json.dumps(_envelope(total_cost_usd=0)))

    response = _judge(stub).score("Score this run.", model="sonnet")

    assert response.cost_usd == 0.0


def test_total_cost_usd_arriving_as_a_float_is_accepted(tmp_path: Path) -> None:
    stub = _write_stub_script(tmp_path, stdout=json.dumps(_envelope(total_cost_usd=0.00187)))

    response = _judge(stub).score("Score this run.", model="sonnet")

    assert response.cost_usd == 0.00187


def test_model_usage_with_zero_keys_is_rejected_as_ambiguous(tmp_path: Path) -> None:
    stub = _write_stub_script(tmp_path, stdout=json.dumps(_envelope(modelUsage={})))

    with pytest.raises(JudgeResponseError, match="0 key"):
        _judge(stub).score("Score this run.", model="sonnet")


def test_model_usage_with_several_keys_is_rejected_as_ambiguous(tmp_path: Path) -> None:
    stub = _write_stub_script(
        tmp_path,
        stdout=json.dumps(_envelope(modelUsage={"claude-sonnet-5": {}, "claude-opus-5": {}})),
    )

    with pytest.raises(JudgeResponseError, match="2 key"):
        _judge(stub).score("Score this run.", model="sonnet")


def test_model_usage_with_one_key_resolves_the_model(tmp_path: Path) -> None:
    stub = _write_stub_script(
        tmp_path, stdout=json.dumps(_envelope(modelUsage={"claude-opus-5": {}}))
    )

    response = _judge(stub).score("Score this run.", model="opus")

    assert response.resolved_model == "claude-opus-5"


def test_not_logged_in_message_is_reported_as_unavailable(tmp_path: Path) -> None:
    stub = _write_stub_script(
        tmp_path,
        stdout=json.dumps(_envelope(is_error=True, result="Not logged in · Please run /login")),
    )

    with pytest.raises(JudgeUnavailableError, match="not authenticated"):
        _judge(stub).score("Score this run.", model="sonnet")


def test_credential_helper_failure_is_reported_distinctly_from_not_authenticated(
    tmp_path: Path,
) -> None:
    stub = _write_stub_script(
        tmp_path,
        stdout=json.dumps(_envelope(is_error=True, result="Not logged in · Please run /login")),
        stderr="apiKeyHelper failed: exited 1: could not read secret",
    )

    with pytest.raises(JudgeUnavailableError, match="credential helper failed") as exc_info:
        _judge(stub).score("Score this run.", model="sonnet")

    assert "not authenticated" not in str(exc_info.value)


def test_binary_absent_is_reported_as_unavailable(tmp_path: Path) -> None:
    missing = tmp_path / "no-such-claude-binary"

    with pytest.raises(JudgeUnavailableError, match="could not be found"):
        _judge(missing).score("Score this run.", model="sonnet")


def test_timeout_expiring_is_reported_as_unavailable(tmp_path: Path) -> None:
    stub = _write_stub_script(tmp_path, stdout=json.dumps(_envelope()), sleep_s=5.0)

    with pytest.raises(JudgeUnavailableError, match="did not respond"):
        _judge(stub, timeout_s=_SHORT_TIMEOUT_S).score("Score this run.", model="sonnet")


def test_undecodable_json_is_reported_as_a_response_error(tmp_path: Path) -> None:
    stub = _write_stub_script(tmp_path, stdout="not json at all")

    with pytest.raises(JudgeResponseError, match="undecodable JSON"):
        _judge(stub).score("Score this run.", model="sonnet")


def test_score_signature_carries_no_subprocess_shaped_type() -> None:
    signature = inspect.signature(ClaudeCliJudge.score)
    for parameter in signature.parameters.values():
        annotation = parameter.annotation
        assert "subprocess" not in str(annotation)
    assert "subprocess" not in str(signature.return_annotation)
