from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class DimensionScore:
    """One rubric dimension's score and supporting evidence."""

    score: int
    evidence: list[str]


@dataclass(frozen=True)
class Verdict:
    """A judge's scoring of one session against a pinned rubric version."""

    session_id: str
    rubric_version: str
    judge_model: str
    dimensions: dict[str, DimensionScore]
    overall_score: float
    suggested_fixes: list[str]
    judge_cost_usd: float
    judge_input_tokens: int
    judge_output_tokens: int

    def to_verdict_json(self) -> dict[str, Any]:
        """Serialize the qualitative payload for the `fact_verdict.verdict_json` column.

        Excludes `session_id`, `rubric_version`, `judge_model`, and the judge
        cost fields — those are stored as dedicated columns on `fact_verdict`.
        """
        return {
            "dimensions": {
                name: {"score": dim.score, "evidence": dim.evidence}
                for name, dim in self.dimensions.items()
            },
            "overall_score": self.overall_score,
            "suggested_fixes": self.suggested_fixes,
        }


class Judge(Protocol):
    """Structural interface for a verdict-scoring backend."""

    def score(self, transcript_view: str, rubric_version: str) -> Verdict: ...
