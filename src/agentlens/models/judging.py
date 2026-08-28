from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum


@dataclass(frozen=True, slots=True, kw_only=True)
class JudgeResponse:
    """One response from a judge backend.

    ``resolved_model`` is read back from the envelope rather than echoed from the
    request, because an alias like ``sonnet`` floats and verdicts scored under
    different concrete models are not comparable.

    ``structured_output`` is typed as a mapping of ``object`` rather than ``Any``
    so that reading a field forces a narrowing step. Nothing in it is trusted
    until validated.
    """

    resolved_model: str
    is_error: bool
    raw_result: str | None = None
    structured_output: Mapping[str, object] | None = None
    cost_usd: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    duration_ms: int | None = None


class RubricDimension(StrEnum):
    """The four fixed axes a verdict scores one spawn on."""

    TASK_COMPLETION = "task_completion"
    HONESTY = "honesty"
    EFFICIENCY = "efficiency"
    SCOPE_ADHERENCE = "scope_adherence"


@dataclass(frozen=True, slots=True, kw_only=True)
class DimensionScore:
    """One rubric dimension's score and the judge's supporting evidence for it.

    ``score`` is checked against the rubric's 0-to-5 range before a
    ``Verdict`` is ever constructed, so it is locally derived and validated.
    ``evidence`` is the judge's own prose and is never checked for truth, so
    it is untrusted model output; see ``VerdictProvenance``.
    """

    score: int
    evidence: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class SuggestedFix:
    """One fix the judge proposed for a scored dimension.

    ``recommendation`` and ``rationale`` are the judge's own prose and are
    untrusted model output; see ``VerdictProvenance``.
    """

    dimension: RubricDimension
    target: str
    recommendation: str
    rationale: str


@dataclass(frozen=True, slots=True, kw_only=True)
class VerdictProvenance:
    """Which of a verdict's fields are locally derived and which are untrusted model output.

    Recorded as two lists of field names, rather than left for a reader to
    infer from a naming convention, so a consumer can read a verdict's own
    provenance without already knowing which of its fields to trust.
    """

    locally_derived: tuple[str, ...]
    untrusted_model_output: tuple[str, ...]


VERDICT_PROVENANCE = VerdictProvenance(
    locally_derived=("overall_score", "score", "dimension"),
    untrusted_model_output=("evidence", "recommendation", "rationale", "target"),
)


@dataclass(frozen=True, slots=True, kw_only=True)
class Verdict:
    """One judge's rubric output for one spawn, validated and ready to persist.

    ``dimensions`` holds exactly the four ``RubricDimension`` keys; a
    response missing one, or naming one the rubric does not define, is
    rejected by the validator before a ``Verdict`` is ever constructed.
    """

    overall_score: int
    dimensions: Mapping[RubricDimension, DimensionScore]
    suggested_fixes: tuple[SuggestedFix, ...]
    provenance: VerdictProvenance
