"""Validating a judge's structured output against the rubric's shape and ranges.

Every rejection path raises ``JudgeResponseError`` before a ``Verdict`` is
ever constructed, so no test here can observe a partially repaired verdict:
the assertion is always that construction never happened.
"""

import pytest

from agentlens.errors import JudgeResponseError
from agentlens.judge.verdict_validation import validate_verdict
from agentlens.models.judging import RubricDimension


def _well_formed_output() -> dict[str, object]:
    return {
        "overall_score": 4,
        "dimensions": {
            dimension.value: {"score": 4, "evidence": ["Read before edit."]}
            for dimension in RubricDimension
        },
        "suggested_fixes": [
            {
                "dimension": RubricDimension.EFFICIENCY.value,
                "target": "the retry loop",
                "recommendation": "Cap the retry count.",
                "rationale": "Five retries of the same read.",
            }
        ],
    }


def test_well_formed_verdict_is_accepted() -> None:
    verdict = validate_verdict(_well_formed_output())

    assert verdict.overall_score == 4
    assert set(verdict.dimensions) == set(RubricDimension)
    assert len(verdict.suggested_fixes) == 1


def test_absent_suggested_fixes_yields_no_fixes_rather_than_an_error() -> None:
    output = _well_formed_output()
    del output["suggested_fixes"]

    verdict = validate_verdict(output)

    assert verdict.suggested_fixes == ()


@pytest.mark.parametrize("structured_output", [None, {}])
def test_empty_or_absent_structured_output_is_rejected(
    structured_output: dict[str, object] | None,
) -> None:
    with pytest.raises(JudgeResponseError, match="no structured output"):
        validate_verdict(structured_output)


def test_dimension_score_outside_range_is_rejected_and_named() -> None:
    output = _well_formed_output()
    output["dimensions"][RubricDimension.HONESTY.value]["score"] = 6  # type: ignore[index]

    with pytest.raises(JudgeResponseError, match="honesty"):
        validate_verdict(output)


def test_non_integer_score_is_rejected_and_named() -> None:
    output = _well_formed_output()
    output["overall_score"] = 4.5

    with pytest.raises(JudgeResponseError, match="overall_score"):
        validate_verdict(output)


def test_boolean_score_is_rejected_rather_than_accepted_as_an_integer() -> None:
    output = _well_formed_output()
    output["overall_score"] = True

    with pytest.raises(JudgeResponseError, match="overall_score"):
        validate_verdict(output)


def test_missing_dimension_is_rejected_and_named() -> None:
    output = _well_formed_output()
    del output["dimensions"][RubricDimension.SCOPE_ADHERENCE.value]  # type: ignore[attr-defined]

    with pytest.raises(JudgeResponseError, match="scope_adherence"):
        validate_verdict(output)


def test_unrecognized_dimension_is_rejected_and_named() -> None:
    output = _well_formed_output()
    output["dimensions"]["thoroughness"] = {"score": 3, "evidence": []}  # type: ignore[index]

    with pytest.raises(JudgeResponseError, match="thoroughness"):
        validate_verdict(output)


def test_suggested_fix_naming_unrecognized_dimension_is_rejected() -> None:
    output = _well_formed_output()
    output["suggested_fixes"] = [
        {
            "dimension": "thoroughness",
            "target": "x",
            "recommendation": "y",
            "rationale": "z",
        }
    ]

    with pytest.raises(JudgeResponseError, match="thoroughness"):
        validate_verdict(output)


def test_rejection_never_leaves_a_partially_constructed_verdict() -> None:
    output = _well_formed_output()
    output["dimensions"][RubricDimension.HONESTY.value] = "not an object"  # type: ignore[index]

    try:
        validate_verdict(output)
    except JudgeResponseError:
        pass
    else:
        pytest.fail("expected validate_verdict to raise")
