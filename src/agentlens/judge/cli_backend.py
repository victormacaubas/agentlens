"""The real ``claude -p`` implementation of ``JudgeBackend``.

Owns the only ``subprocess`` call in this package's public surface, and
translates every failure mode observed against the installed CLI into
agentlens's own error taxonomy at this boundary: a caller depends on
:class:`~agentlens.errors.JudgeUnavailableError` and
:class:`~agentlens.errors.JudgeResponseError`, never on ``subprocess`` or
``json`` exceptions.

The envelope carries more than one status-like field and they can disagree
(``subtype`` has been observed to read ``"success"`` while ``is_error`` was
``true``), so only ``is_error`` is ever trusted as the error signal here.
"""

import json
import os
import subprocess
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Final

from agentlens.errors import JudgeResponseError, JudgeUnavailableError
from agentlens.judge.invocation import (
    DEFAULT_CLAUDE_BINARY,
    build_invocation,
    default_user_settings_path,
)
from agentlens.models.judging import JudgeResponse

DEFAULT_TIMEOUT_S: Final = 120.0
DEFAULT_SPEND_CEILING_USD: Final = 0.50

_CREDENTIAL_HELPER_MARKER: Final = "apiKeyHelper failed"
_NOT_LOGGED_IN_MARKER: Final = "Not logged in"


class ClaudeCliJudge:
    """Scores a prepared prompt by shelling out to the installed ``claude`` CLI.

    The timeout and spend ceiling are constructor arguments rather than
    per-call parameters: both are properties of this transport, not of a
    scoring request, and a fake backend has neither a subprocess nor a
    dollar to bound.
    """

    def __init__(
        self,
        *,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        spend_ceiling_usd: float = DEFAULT_SPEND_CEILING_USD,
        settings_path: Path | None = None,
        binary: str = DEFAULT_CLAUDE_BINARY,
    ) -> None:
        self._timeout_s = timeout_s
        self._spend_ceiling_usd = spend_ceiling_usd
        self._settings_path = (
            settings_path if settings_path is not None else default_user_settings_path()
        )
        self._binary = binary

    def score(self, prompt: str, *, model: str) -> JudgeResponse:
        """Score one prepared prompt. See ``JudgeBackend.score`` for the contract."""
        with tempfile.TemporaryDirectory() as temp_dir:
            invocation = build_invocation(
                prompt=prompt,
                model=model,
                spend_ceiling_usd=self._spend_ceiling_usd,
                timeout_s=self._timeout_s,
                settings_path=self._settings_path,
                temp_dir=Path(temp_dir),
                source_env=os.environ,
                binary=self._binary,
            )
            completed = self._run(invocation.argv, env=invocation.env, cwd=invocation.cwd)
        return _translate_envelope(completed.stdout, completed.stderr)

    def _run(
        self, argv: tuple[str, ...], *, env: Mapping[str, str], cwd: Path
    ) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(  # noqa: S603 -- argv is a tuple literal, never shell=True
                argv,
                capture_output=True,
                text=True,
                cwd=cwd,
                env=dict(env),
                timeout=self._timeout_s,
                check=False,
            )
        except FileNotFoundError as error:
            raise JudgeUnavailableError(
                f"Judge binary {argv[0]!r} could not be found on PATH."
            ) from error
        except subprocess.TimeoutExpired as error:
            raise JudgeUnavailableError(
                f"Judge did not respond within {self._timeout_s:.0f}s."
            ) from error
        except subprocess.SubprocessError as error:
            raise JudgeUnavailableError(f"Judge call failed to run: {error}") from error


def _translate_envelope(stdout: str, stderr: str) -> JudgeResponse:
    envelope = _decode_envelope(stdout)
    if envelope.get("is_error") is True:
        raise _translate_error(envelope, stderr)
    return _build_response(envelope)


def _decode_envelope(stdout: str) -> Mapping[str, object]:
    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError as error:
        raise JudgeResponseError(f"Judge returned undecodable JSON: {error}") from error
    if not isinstance(envelope, Mapping):
        raise JudgeResponseError("Judge returned a JSON value that is not an object.")
    return envelope


def _translate_error(
    envelope: Mapping[str, object], stderr: str
) -> JudgeUnavailableError | JudgeResponseError:
    if _CREDENTIAL_HELPER_MARKER in stderr:
        return JudgeUnavailableError(f"Judge's credential helper failed: {stderr.strip()}")
    result = envelope.get("result")
    if isinstance(result, str) and _NOT_LOGGED_IN_MARKER in result:
        return JudgeUnavailableError(f"Judge is not authenticated: {result}")
    return JudgeResponseError(f"Judge reported an error: {result!r}")


def _build_response(envelope: Mapping[str, object]) -> JudgeResponse:
    resolved_model = _resolve_model(envelope)
    total_cost = envelope.get("total_cost_usd")
    usage = envelope.get("usage")
    input_tokens = usage.get("input_tokens") if isinstance(usage, Mapping) else None
    output_tokens = usage.get("output_tokens") if isinstance(usage, Mapping) else None
    duration_ms = envelope.get("duration_ms")
    structured_output = envelope.get("structured_output")
    result = envelope.get("result")
    return JudgeResponse(
        resolved_model=resolved_model,
        is_error=False,
        raw_result=result if isinstance(result, str) else None,
        structured_output=structured_output if isinstance(structured_output, Mapping) else None,
        cost_usd=float(total_cost) if isinstance(total_cost, int | float) else None,
        input_tokens=input_tokens if isinstance(input_tokens, int) else None,
        output_tokens=output_tokens if isinstance(output_tokens, int) else None,
        duration_ms=duration_ms if isinstance(duration_ms, int) else None,
    )


def _resolve_model(envelope: Mapping[str, object]) -> str:
    model_usage = envelope.get("modelUsage")
    if not isinstance(model_usage, Mapping) or len(model_usage) != 1:
        count = len(model_usage) if isinstance(model_usage, Mapping) else 0
        raise JudgeResponseError(
            f"Judge envelope's modelUsage carries {count} key(s); the verdict's "
            "resolved-model identity would be ambiguous."
        )
    (resolved_model,) = model_usage.keys()
    return str(resolved_model)
