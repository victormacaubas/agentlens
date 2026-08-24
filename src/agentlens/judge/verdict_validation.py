"""Hand-written validation of a judge's structured output against the rubric.

``--json-schema`` constrains the *shape* the transport accepts, but a
schema-conformant response can still carry a dimension the rubric does not
define, or the transport can hand back nothing at all. A verdict is
validated in full or rejected outright; nothing here repairs a bad field and
returns a partial :class:`~agentlens.models.judging.Verdict`.
"""

from collections.abc import Mapping

from agentlens.errors import JudgeResponseError
from agentlens.judge.rubric import MAX_SCORE, MIN_SCORE
from agentlens.models.judging import (
    VERDICT_PROVENANCE,
    DimensionScore,
    RubricDimension,
    SuggestedFix,
    Verdict,
)

_REQUIRED_FIX_FIELDS: tuple[str, ...] = ("dimension", "target", "recommendation", "rationale")


def validate_verdict(structured_output: Mapping[str, object] | None) -> Verdict:
    """Validate ``structured_output`` and return a frozen, provenance-tagged ``Verdict``.

    Raises:
        JudgeResponseError: The output is empty, missing a required field, or
            carries a value the rubric's shape or ranges do not allow. The
            message names what was wrong.
    """
    if not structured_output:
        raise JudgeResponseError("Judge returned no structured output to validate.")
    overall_score = _validate_score(structured_output.get("overall_score"), field="overall_score")
    dimensions = _validate_dimensions(structured_output.get("dimensions"))
    suggested_fixes = _validate_suggested_fixes(structured_output.get("suggested_fixes"))
    return Verdict(
        overall_score=overall_score,
        dimensions=dimensions,
        suggested_fixes=suggested_fixes,
        provenance=VERDICT_PROVENANCE,
    )


def _validate_score(value: object, *, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise JudgeResponseError(f"Judge verdict field {field!r} must be an integer score.")
    if not MIN_SCORE <= value <= MAX_SCORE:
        raise JudgeResponseError(
            f"Judge verdict field {field!r} scored {value}, outside {MIN_SCORE}-{MAX_SCORE}."
        )
    return value


def _validate_dimensions(value: object) -> Mapping[RubricDimension, DimensionScore]:
    if not isinstance(value, Mapping):
        raise JudgeResponseError("Judge verdict is missing its 'dimensions' object.")
    known_names = {dimension.value for dimension in RubricDimension}
    present_names = set(value.keys())
    unknown = present_names - known_names
    if unknown:
        raise JudgeResponseError(
            f"Judge verdict named unrecognized dimension(s): {sorted(unknown)}."
        )
    missing = known_names - present_names
    if missing:
        raise JudgeResponseError(f"Judge verdict is missing dimension(s): {sorted(missing)}.")
    return {
        dimension: _validate_dimension_entry(value[dimension.value], dimension=dimension)
        for dimension in RubricDimension
    }


def _validate_dimension_entry(entry: object, *, dimension: RubricDimension) -> DimensionScore:
    if not isinstance(entry, Mapping):
        raise JudgeResponseError(f"Judge verdict dimension {dimension.value!r} is not an object.")
    score = _validate_score(entry.get("score"), field=f"dimensions.{dimension.value}.score")
    evidence = entry.get("evidence")
    if not isinstance(evidence, list) or not all(isinstance(item, str) for item in evidence):
        raise JudgeResponseError(
            f"Judge verdict dimension {dimension.value!r} carries non-string evidence."
        )
    return DimensionScore(score=score, evidence=tuple(evidence))


def _validate_suggested_fixes(value: object) -> tuple[SuggestedFix, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise JudgeResponseError("Judge verdict's 'suggested_fixes' must be a list.")
    return tuple(_validate_fix(item) for item in value)


def _validate_fix(item: object) -> SuggestedFix:
    if not isinstance(item, Mapping):
        raise JudgeResponseError("Judge verdict named a suggested fix that is not an object.")
    missing = [field for field in _REQUIRED_FIX_FIELDS if field not in item]
    if missing:
        raise JudgeResponseError(f"Judge verdict's suggested fix is missing field(s): {missing}.")
    dimension_value = item["dimension"]
    try:
        dimension = RubricDimension(dimension_value)
    except ValueError as error:
        raise JudgeResponseError(
            f"Judge verdict's suggested fix names unrecognized dimension {dimension_value!r}."
        ) from error
    return SuggestedFix(
        dimension=dimension,
        target=_validate_str(item["target"], field="target"),
        recommendation=_validate_str(item["recommendation"], field="recommendation"),
        rationale=_validate_str(item["rationale"], field="rationale"),
    )


def _validate_str(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise JudgeResponseError(f"Judge verdict's suggested fix field {field!r} is not a string.")
    return value
