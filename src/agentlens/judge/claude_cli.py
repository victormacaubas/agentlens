"""Claude CLI judge backend: invokes `claude -p` in headless, non-interactive
mode as a subprocess and parses its JSON envelope into a `Verdict`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Final

from agentlens.errors import JudgeError, JudgeTimeoutError, JudgeUnavailableError
from agentlens.judge.protocol import DimensionScore, Verdict
from agentlens.judge.rubric import DIMENSION_NAMES, RUBRIC_PROMPT_TEMPLATE, VERDICT_JSON_SCHEMA

DEFAULT_MODEL: Final[str] = "sonnet"
DEFAULT_TIMEOUT_SECONDS: Final[int] = 180
MAX_TURNS: Final[str] = "3"
CLAUDE_EXECUTABLE: Final[str] = "claude"
OUTPUT_EXCERPT_MAX_CHARS: Final[int] = 500
NO_TOOLS: Final[str] = ""
SETTING_SOURCES: Final[str] = "user"
_ENV_PASSTHROUGH_NAMES: Final[frozenset[str]] = frozenset({"PATH", "HOME"})
_ENV_PASSTHROUGH_PREFIX: Final[str] = "ANTHROPIC_"
_NOT_LOGGED_IN_MARKER: Final[str] = "not logged in"
_AUTH_REMEDY: Final[str] = (
    "set ANTHROPIC_API_KEY, or add apiKeyHelper to ~/.claude/settings.json "
    "(read via --settings under --bare; OAuth/keychain login is not read)"
)


class ClaudeCliJudge:
    """Scores a transcript view by invoking the `claude` CLI's headless mode.

    Availability is checked lazily on the first `score()` call rather than at
    construction time, so instantiating the judge never touches the
    filesystem or environment. `resolved_model` stays `None` until a
    successful `score()` call resolves `model` (which may be a floating
    alias) to the concrete identifier the CLI actually used.
    """

    def __init__(
        self, *, model: str = DEFAULT_MODEL, timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    ) -> None:
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.resolved_model: str | None = None
        self._checked_available = False

    def score(self, transcript_view: str, rubric_version: str) -> Verdict:
        self._check_claude_available()
        args = self._build_args()

        with tempfile.TemporaryDirectory() as tmp_cwd:
            try:
                result = subprocess.run(
                    args,
                    input=transcript_view,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds,
                    cwd=tmp_cwd,
                    env=_build_subprocess_env(),
                )
            except subprocess.TimeoutExpired as exc:
                raise JudgeTimeoutError(
                    f"claude -p exceeded {self.timeout_seconds}s timeout"
                ) from exc
            except OSError as exc:
                raise JudgeError(f"failed to launch claude subprocess: {exc}") from exc

        if result.returncode != 0:
            unavailable = _detect_unavailable(result.stdout)
            if unavailable is not None:
                raise unavailable
            raise JudgeError(
                f"claude -p exited with code {result.returncode}; "
                f"stderr: {_excerpt(result.stderr)}; stdout: {_excerpt(result.stdout)}"
            )

        envelope = _parse_envelope(result.stdout)
        if envelope.get("is_error"):
            result_text = envelope.get("result", "(no result)")
            raise JudgeError(f"claude -p reported an error: {result_text}")

        verdict = _build_verdict(envelope, rubric_version=rubric_version)
        self.resolved_model = verdict.judge_model
        return verdict

    def _check_claude_available(self) -> None:
        if self._checked_available:
            return
        if shutil.which(CLAUDE_EXECUTABLE) is None:
            raise JudgeUnavailableError(f"{CLAUDE_EXECUTABLE!r} was not found on PATH")
        self._checked_available = True

    def _build_args(self) -> list[str]:
        return [
            CLAUDE_EXECUTABLE,
            "-p",
            "--output-format",
            "json",
            "--model",
            self.model,
            "--json-schema",
            json.dumps(VERDICT_JSON_SCHEMA),
            "--max-turns",
            MAX_TURNS,
            "--bare",
            "--tools",
            NO_TOOLS,
            "--settings",
            str(_user_settings_path()),
            "--setting-sources",
            SETTING_SOURCES,
            "--append-system-prompt",
            RUBRIC_PROMPT_TEMPLATE,
        ]


def _user_settings_path() -> Path:
    return Path.home() / ".claude" / "settings.json"


def _build_subprocess_env() -> dict[str, str]:
    """Build the subprocess environment explicitly instead of inheriting
    agentlens's: `PATH`, `HOME`, and any `ANTHROPIC_*` variable are forwarded
    so the machine's auth channel keeps working; everything else is dropped.
    """
    return {
        name: value
        for name, value in os.environ.items()
        if name in _ENV_PASSTHROUGH_NAMES or name.startswith(_ENV_PASSTHROUGH_PREFIX)
    }


def _detect_unavailable(stdout: str) -> JudgeUnavailableError | None:
    """Check a non-zero-exit envelope for the CLI's not-logged-in response.

    Returns `None` for anything not recognizably that response, including
    stdout that isn't valid JSON, so an unrelated failure keeps its own error
    rather than being reported as missing credentials.
    """
    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(envelope, dict):
        return None
    result_text = envelope.get("result")
    if not isinstance(result_text, str) or _NOT_LOGGED_IN_MARKER not in result_text.lower():
        return None
    return JudgeUnavailableError(f"claude -p reported it is not logged in; {_AUTH_REMEDY}")


def _excerpt(text: str) -> str:
    text = text.strip()
    if len(text) <= OUTPUT_EXCERPT_MAX_CHARS:
        return text
    return text[:OUTPUT_EXCERPT_MAX_CHARS] + "... [truncated]"


def _parse_envelope(stdout: str) -> dict[str, Any]:
    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise JudgeError(f"claude -p stdout was not valid JSON: {_excerpt(stdout)}") from exc
    if not isinstance(envelope, dict):
        raise JudgeError(f"claude -p envelope was not a JSON object: {_excerpt(stdout)}")
    return envelope


def _build_verdict(envelope: dict[str, Any], *, rubric_version: str) -> Verdict:
    structured_output = envelope.get("structured_output")
    if not isinstance(structured_output, dict):
        raise JudgeError(
            f"claude -p envelope is missing a usable 'structured_output': {envelope!r}"
        )

    dimensions = _parse_dimensions(structured_output)
    suggested_fixes = structured_output.get("suggested_fixes")
    if not isinstance(suggested_fixes, list) or not all(
        isinstance(fix, str) for fix in suggested_fixes
    ):
        raise JudgeError("structured_output.suggested_fixes must be a list of strings")

    overall_score = sum(d.score for d in dimensions.values()) / len(dimensions)

    resolved_model, model_usage = _extract_model_usage(envelope)
    input_tokens = (
        _numeric_field(model_usage, "inputTokens")
        + _numeric_field(model_usage, "cacheCreationInputTokens")
        + _numeric_field(model_usage, "cacheReadInputTokens")
    )
    output_tokens = _numeric_field(model_usage, "outputTokens")
    total_cost_usd = envelope.get("total_cost_usd", 0.0)

    session_id = envelope.get("session_id")

    return Verdict(
        session_id=session_id if isinstance(session_id, str) else "",
        rubric_version=rubric_version,
        judge_model=resolved_model,
        dimensions=dimensions,
        overall_score=overall_score,
        suggested_fixes=suggested_fixes,
        judge_cost_usd=float(total_cost_usd) if isinstance(total_cost_usd, (int, float)) else 0.0,
        judge_input_tokens=input_tokens,
        judge_output_tokens=output_tokens,
    )


def _extract_model_usage(envelope: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Extract the resolved model identifier and its usage entry from the
    envelope's `modelUsage` map.

    The identifier is the map's key, which carries the dated snapshot where a
    model's key and its `canonicalModel` field diverge (e.g. a `haiku` call
    resolves to key `claude-haiku-4-5-20251001` while `canonicalModel` holds
    the undated `claude-haiku-4-5`). `canonicalModel` is used only when the
    key itself is unusable. `modelUsage` missing, empty, or carrying more
    than one entry raises rather than falling back to the configured alias:
    a silent fallback would reintroduce the alias ambiguity this extraction
    exists to remove, and multiple entries would mean the call used more
    than one model, which no single `judge_model` value can represent.
    """
    raw_model_usage = envelope.get("modelUsage")
    if not isinstance(raw_model_usage, dict) or len(raw_model_usage) != 1:
        raise JudgeError(
            "claude -p envelope's modelUsage must carry exactly one entry, "
            f"got: {raw_model_usage!r}"
        )
    ((model_key, entry),) = raw_model_usage.items()
    if not isinstance(entry, dict):
        raise JudgeError(f"claude -p envelope's modelUsage entry is not an object: {entry!r}")

    resolved_model = model_key if isinstance(model_key, str) and model_key else None
    if resolved_model is None:
        canonical_model = entry.get("canonicalModel")
        resolved_model = (
            canonical_model if isinstance(canonical_model, str) and canonical_model else None
        )
    if resolved_model is None:
        raise JudgeError(
            "claude -p envelope's modelUsage entry has no usable model "
            f"identifier: {raw_model_usage!r}"
        )
    return resolved_model, entry


def _numeric_field(mapping: dict[str, Any], key: str) -> int:
    value = mapping.get(key, 0)
    return int(value) if isinstance(value, (int, float)) else 0


def _parse_dimensions(structured_output: dict[str, Any]) -> dict[str, DimensionScore]:
    raw_dimensions = structured_output.get("dimensions")
    if not isinstance(raw_dimensions, dict):
        raise JudgeError("structured_output.dimensions must be an object")

    dimensions: dict[str, DimensionScore] = {}
    for name in DIMENSION_NAMES:
        raw_dimension = raw_dimensions.get(name)
        if not isinstance(raw_dimension, dict):
            raise JudgeError(f"structured_output.dimensions is missing required key {name!r}")
        score = raw_dimension.get("score")
        evidence = raw_dimension.get("evidence")
        if (
            not isinstance(score, int)
            or isinstance(score, bool)
            or score < 0
            or score > 5
        ):
            raise JudgeError(
                f"structured_output.dimensions[{name!r}].score must be an integer in 0-5"
            )
        if not isinstance(evidence, list) or not all(
            isinstance(item, str) for item in evidence
        ):
            raise JudgeError(f"structured_output.dimensions[{name!r}] has an invalid shape")
        dimensions[name] = DimensionScore(score=score, evidence=evidence)
    return dimensions
