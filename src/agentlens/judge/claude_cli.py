"""Claude CLI judge backend (design D1/D3): invokes `claude -p` in headless,
non-interactive mode as a subprocess and parses its JSON envelope into a
`Verdict`.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any, Final

from agentlens.errors import JudgeError, JudgeTimeoutError, JudgeUnavailableError
from agentlens.judge.protocol import DimensionScore, Verdict
from agentlens.judge.rubric import DIMENSION_NAMES, RUBRIC_PROMPT_TEMPLATE, VERDICT_JSON_SCHEMA

DEFAULT_MODEL: Final[str] = "sonnet"
DEFAULT_TIMEOUT_SECONDS: Final[int] = 60
MAX_TURNS: Final[str] = "3"
ALLOWED_TOOLS: Final[str] = "Read,Grep"
CLAUDE_EXECUTABLE: Final[str] = "claude"
OUTPUT_EXCERPT_MAX_CHARS: Final[int] = 500


class ClaudeCliJudge:
    """Scores a transcript view by invoking the `claude` CLI's headless mode.

    Availability is checked lazily on the first `score()` call rather than at
    construction time, so instantiating the judge never touches the
    filesystem or environment.
    """

    def __init__(
        self, *, model: str = DEFAULT_MODEL, timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    ) -> None:
        self.model = model
        self.timeout_seconds = timeout_seconds
        self._checked_available = False

    def score(self, transcript_view: str, rubric_version: str) -> Verdict:
        self._check_claude_available()
        args = self._build_args()

        try:
            result = subprocess.run(
                args,
                input=transcript_view,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise JudgeTimeoutError(
                f"claude -p exceeded {self.timeout_seconds}s timeout"
            ) from exc

        if result.returncode != 0:
            raise JudgeError(
                f"claude -p exited with code {result.returncode}; "
                f"stderr: {_excerpt(result.stderr)}; stdout: {_excerpt(result.stdout)}"
            )

        envelope = _parse_envelope(result.stdout)
        if envelope.get("is_error"):
            result_text = envelope.get("result", "(no result)")
            raise JudgeError(f"claude -p reported an error: {result_text}")

        return _build_verdict(envelope, rubric_version=rubric_version, judge_model=self.model)

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
            "--permission-mode",
            "dontAsk",
            "--allowedTools",
            ALLOWED_TOOLS,
            "--max-turns",
            MAX_TURNS,
            "--bare",
            "--append-system-prompt",
            RUBRIC_PROMPT_TEMPLATE,
        ]


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


def _build_verdict(envelope: dict[str, Any], *, rubric_version: str, judge_model: str) -> Verdict:
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

    overall_score = structured_output.get("overall_score")
    if not isinstance(overall_score, (int, float)):
        raise JudgeError("structured_output.overall_score must be a number")

    raw_usage = envelope.get("usage")
    usage: dict[str, Any] = raw_usage if isinstance(raw_usage, dict) else {}
    input_tokens = usage.get("input_tokens", 0)
    output_tokens = usage.get("output_tokens", 0)
    total_cost_usd = envelope.get("total_cost_usd", 0.0)

    session_id = envelope.get("session_id")

    return Verdict(
        session_id=session_id if isinstance(session_id, str) else "",
        rubric_version=rubric_version,
        judge_model=judge_model,
        dimensions=dimensions,
        overall_score=float(overall_score),
        suggested_fixes=suggested_fixes,
        judge_cost_usd=float(total_cost_usd) if isinstance(total_cost_usd, (int, float)) else 0.0,
        judge_input_tokens=int(input_tokens) if isinstance(input_tokens, (int, float)) else 0,
        judge_output_tokens=int(output_tokens) if isinstance(output_tokens, (int, float)) else 0,
    )


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
        if not isinstance(score, int) or not isinstance(evidence, list) or not all(
            isinstance(item, str) for item in evidence
        ):
            raise JudgeError(f"structured_output.dimensions[{name!r}] has an invalid shape")
        dimensions[name] = DimensionScore(score=score, evidence=evidence)
    return dimensions
