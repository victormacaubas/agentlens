"""Shape and provenance checks on the verdict domain types.

A verdict's scores are locally derived and validated before construction; its
evidence and fix prose are untrusted model output. These tests pin that split
to the data itself, and pin that no dataclass field can default in a way that
would let a missing score read as a real one.
"""

from dataclasses import fields

import pytest

from agentlens.models.judging import (
    VERDICT_PROVENANCE,
    DimensionScore,
    RubricDimension,
    SuggestedFix,
    Verdict,
    VerdictProvenance,
)
from tests.factories import build_dimension_score, build_suggested_fix, build_verdict


def test_fully_populated_verdict_covers_all_four_rubric_dimensions() -> None:
    verdict = build_verdict()

    assert set(verdict.dimensions) == set(RubricDimension)


def test_provenance_split_names_every_scored_field() -> None:
    """Every leaf field a verdict's dimensions and fixes carry has a provenance home.

    Structural fields (``dimensions``, ``suggested_fixes``, ``provenance``
    itself) are containers, not scored content, and are deliberately excluded.
    """
    verdict = build_verdict()
    scored_fields = (
        {field.name for field in fields(DimensionScore)}
        | {field.name for field in fields(SuggestedFix)}
        | {"overall_score"}
    )

    named_fields = set(verdict.provenance.locally_derived) | set(
        verdict.provenance.untrusted_model_output
    )

    assert named_fields == scored_fields


def test_provenance_marks_scores_as_locally_derived_and_prose_as_untrusted() -> None:
    provenance = VERDICT_PROVENANCE

    assert set(provenance.locally_derived) == {"overall_score", "score", "dimension"}
    assert set(provenance.untrusted_model_output) == {
        "evidence",
        "recommendation",
        "rationale",
        "target",
    }
    assert not set(provenance.locally_derived) & set(provenance.untrusted_model_output)


def test_dimension_score_of_zero_is_a_valid_low_score_not_a_missing_one() -> None:
    """A score of 0 must read as a real low score, never as an unset field."""
    scored = build_dimension_score(score=0, evidence=())

    assert scored.score == 0


@pytest.mark.parametrize(
    ("builder", "kwargs"),
    [
        (DimensionScore, {"evidence": ()}),
        (DimensionScore, {"score": 3}),
        (SuggestedFix, {"target": "x", "recommendation": "y", "rationale": "z"}),
        (Verdict, {"dimensions": {}, "suggested_fixes": (), "provenance": VERDICT_PROVENANCE}),
        (VerdictProvenance, {"untrusted_model_output": ()}),
    ],
    ids=[
        "dimension_score_missing_score",
        "dimension_score_missing_evidence",
        "suggested_fix_missing_dimension",
        "verdict_missing_overall_score",
        "provenance_missing_locally_derived",
    ],
)
def test_omitting_a_required_field_raises_rather_than_defaulting(
    builder: type[object], kwargs: dict[str, object]
) -> None:
    with pytest.raises(TypeError):
        builder(**kwargs)


def test_suggested_fix_names_the_dimension_it_targets() -> None:
    fix = build_suggested_fix(dimension=RubricDimension.HONESTY)

    assert fix.dimension is RubricDimension.HONESTY
