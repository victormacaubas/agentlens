from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any, Final, Protocol

from agentlens.errors import JudgeError
from agentlens.judge.rubric import (
    DIMENSION_NAMES,
    FIX_TARGETS,
    MAX_EVIDENCE_ITEM_LENGTH,
    MAX_EVIDENCE_ITEMS,
    MAX_FIX_RATIONALE_LENGTH,
    MAX_FIX_RECOMMENDATION_LENGTH,
    MAX_SUGGESTED_FIXES,
    MODEL_ALIASES,
)

DIAGNOSTIC_EXCERPT_MAX_CHARS: Final[int] = 200
_TRUNCATION_MARKER: Final[str] = "... [truncated]"


@dataclass(frozen=True)
class DimensionScore:
    """One rubric dimension's score and supporting evidence."""

    score: int
    evidence: list[str]


@dataclass(frozen=True)
class SuggestedFix:
    """A judge-recommended change to an agent's own guidance.

    `dimension` ties the fix to the rubric dimension it addresses.  `target`
    names what the fix applies to, drawn from the closed set defined in
    `agentlens.judge.rubric.FIX_TARGETS` — never an arbitrary file path or
    command. `recommendation` and `rationale` are natural-language text
    supplied by the judge model and are untrusted, unlike the other two
    fields, which the parse boundary validates against fixed sets.
    """

    dimension: str
    target: str
    recommendation: str
    rationale: str


@dataclass(frozen=True)
class Verdict:
    """A judge's scoring of one session against a pinned rubric version."""

    session_id: str
    rubric_version: str
    judge_model: str
    dimensions: dict[str, DimensionScore]
    overall_score: float
    suggested_fixes: list[SuggestedFix]
    judge_cost_usd: float
    judge_input_tokens: int
    judge_output_tokens: int
    judge_input_hash: str = ""

    def to_verdict_json(self) -> dict[str, Any]:
        """Serialize the qualitative payload for the `fact_verdict.verdict_json` column.

        Excludes identity and judge cost fields, which are stored as dedicated
        columns on `fact_verdict`.

        The payload carries a `provenance` manifest alongside the existing
        fields so any consumer can tell, without reimplementing pipeline
        knowledge, which values are locally derived and validated versus
        which are free text authored by the judge model from untrusted
        transcript content.
        """
        return {
            "dimensions": {
                name: {"score": dim.score, "evidence": dim.evidence}
                for name, dim in self.dimensions.items()
            },
            "overall_score": self.overall_score,
            "suggested_fixes": [
                {
                    "dimension": fix.dimension,
                    "target": fix.target,
                    "recommendation": fix.recommendation,
                    "rationale": fix.rationale,
                }
                for fix in self.suggested_fixes
            ],
            "provenance": {
                "locally_derived": ["overall_score", "dimensions.*.score"],
                "untrusted_model_output": [
                    "dimensions.*.evidence",
                    "suggested_fixes[].recommendation",
                    "suggested_fixes[].rationale",
                ],
            },
        }


def bounded_diagnostic(value: object) -> str:
    """Render an external value without exposing an unbounded payload."""
    if isinstance(value, BaseException):
        text = str(value)
    elif isinstance(value, str):
        text = value
    elif value is None or isinstance(value, (bool, int, float)):
        text = repr(value)
    elif isinstance(value, dict):
        return f"<object with {len(value)} keys>"
    elif isinstance(value, (list, tuple, set, frozenset)):
        return f"<{type(value).__name__} with {len(value)} items>"
    else:
        return f"<{type(value).__name__}>"

    text = text.strip()
    if len(text) <= DIAGNOSTIC_EXCERPT_MAX_CHARS:
        return text
    return text[:DIAGNOSTIC_EXCERPT_MAX_CHARS] + _TRUNCATION_MARKER


def validate_verdict(verdict: Verdict) -> Verdict:
    """Validate and normalize a backend verdict before persistence.

    The returned verdict has a locally derived overall score and normalized
    cost. Invalid backend output raises `JudgeError` without including
    model-authored field contents in the error message.
    """
    if not isinstance(verdict, Verdict):
        raise JudgeError("judge backend must return a Verdict")

    judge_model = verdict.judge_model
    if (
        not isinstance(judge_model, str)
        or not judge_model
        or judge_model != judge_model.strip()
        or judge_model in MODEL_ALIASES
    ):
        raise JudgeError("verdict.judge_model must be a non-empty concrete model identifier")

    dimensions = verdict.dimensions
    if not isinstance(dimensions, dict) or set(dimensions) != set(DIMENSION_NAMES):
        raise JudgeError("verdict.dimensions must contain exactly the four rubric dimensions")

    validated_dimensions: dict[str, DimensionScore] = {}
    for name in DIMENSION_NAMES:
        dimension = dimensions[name]
        if not isinstance(dimension, DimensionScore):
            raise JudgeError(f"verdict.dimensions[{name!r}] must be a DimensionScore")
        score = dimension.score
        if not isinstance(score, int) or isinstance(score, bool) or not 0 <= score <= 5:
            raise JudgeError(
                f"verdict.dimensions[{name!r}].score must be an integer in 0-5"
            )

        evidence = dimension.evidence
        if not isinstance(evidence, list):
            raise JudgeError(f"verdict.dimensions[{name!r}].evidence must be a list")
        if len(evidence) > MAX_EVIDENCE_ITEMS:
            raise JudgeError(
                f"verdict.dimensions[{name!r}].evidence exceeds the item limit"
            )
        validated_evidence: list[str] = []
        for index, item in enumerate(evidence):
            if not isinstance(item, str):
                raise JudgeError(
                    f"verdict.dimensions[{name!r}].evidence[{index}] must be a string"
                )
            if len(item) > MAX_EVIDENCE_ITEM_LENGTH:
                raise JudgeError(
                    f"verdict.dimensions[{name!r}].evidence[{index}] exceeds the length limit"
                )
            validated_evidence.append(item)
        validated_dimensions[name] = DimensionScore(
            score=score,
            evidence=validated_evidence,
        )

    fixes = verdict.suggested_fixes
    if not isinstance(fixes, list):
        raise JudgeError("verdict.suggested_fixes must be a list")
    if len(fixes) > MAX_SUGGESTED_FIXES:
        raise JudgeError("verdict.suggested_fixes exceeds the item limit")

    validated_fixes: list[SuggestedFix] = []
    for index, fix in enumerate(fixes):
        if not isinstance(fix, SuggestedFix):
            raise JudgeError(
                f"verdict.suggested_fixes[{index}] must be a typed SuggestedFix"
            )
        if not isinstance(fix.dimension, str) or fix.dimension not in DIMENSION_NAMES:
            raise JudgeError(
                f"verdict.suggested_fixes[{index}].dimension is not a known dimension"
            )
        if not isinstance(fix.target, str) or fix.target not in FIX_TARGETS:
            raise JudgeError(
                f"verdict.suggested_fixes[{index}].target is not a known target"
            )
        if not isinstance(fix.recommendation, str):
            raise JudgeError(
                f"verdict.suggested_fixes[{index}].recommendation must be a string"
            )
        if len(fix.recommendation) > MAX_FIX_RECOMMENDATION_LENGTH:
            raise JudgeError(
                f"verdict.suggested_fixes[{index}].recommendation exceeds the length limit"
            )
        if not isinstance(fix.rationale, str):
            raise JudgeError(f"verdict.suggested_fixes[{index}].rationale must be a string")
        if len(fix.rationale) > MAX_FIX_RATIONALE_LENGTH:
            raise JudgeError(
                f"verdict.suggested_fixes[{index}].rationale exceeds the length limit"
            )
        validated_fixes.append(fix)

    judge_cost_usd = _validate_cost(verdict.judge_cost_usd)
    judge_input_tokens = _validate_token_count(
        verdict.judge_input_tokens,
        field="judge_input_tokens",
    )
    judge_output_tokens = _validate_token_count(
        verdict.judge_output_tokens,
        field="judge_output_tokens",
    )
    overall_score = sum(
        dimension.score for dimension in validated_dimensions.values()
    ) / len(validated_dimensions)

    return replace(
        verdict,
        dimensions=validated_dimensions,
        overall_score=overall_score,
        suggested_fixes=validated_fixes,
        judge_cost_usd=judge_cost_usd,
        judge_input_tokens=judge_input_tokens,
        judge_output_tokens=judge_output_tokens,
    )


def _validate_cost(value: object) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise JudgeError("verdict.judge_cost_usd must be a finite non-negative number")
    try:
        normalized = float(value)
    except (OverflowError, ValueError) as exc:
        raise JudgeError(
            "verdict.judge_cost_usd must be a finite non-negative number"
        ) from exc
    if not math.isfinite(normalized) or normalized < 0:
        raise JudgeError("verdict.judge_cost_usd must be a finite non-negative number")
    return normalized


def _validate_token_count(value: object, *, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise JudgeError(f"verdict.{field} must be a finite non-negative integer")
    return value


class Judge(Protocol):
    """Structural interface for a verdict-scoring backend.

    `resolved_model` exposes the concrete model identifier a backend
    resolved a possibly-floating `model` configuration to. It is `None`
    until a call to `score()` succeeds, so a caller that needs the resolved
    identity (e.g. to key a store query on it) must call `score()` first.
    """

    resolved_model: str | None

    def score(self, transcript_view: str, rubric_version: str) -> Verdict: ...
