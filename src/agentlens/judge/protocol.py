from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


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

    def to_verdict_json(self) -> dict[str, Any]:
        """Serialize the qualitative payload for the `fact_verdict.verdict_json` column.

        Excludes `session_id`, `rubric_version`, `judge_model`, and the judge
        cost fields — those are stored as dedicated columns on `fact_verdict`.

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


class Judge(Protocol):
    """Structural interface for a verdict-scoring backend.

    `resolved_model` exposes the concrete model identifier a backend
    resolved a possibly-floating `model` configuration to. It is `None`
    until a call to `score()` succeeds, so a caller that needs the resolved
    identity (e.g. to key a store query on it) must call `score()` first.
    """

    resolved_model: str | None

    def score(self, transcript_view: str, rubric_version: str) -> Verdict: ...
