"""The pinned rubric: its dimensions, schema, and the version/content pin.

The pin itself is verified by hand: after writing the rubric, its digest was
computed once and hardcoded below as ``_PINNED_DIGEST``. Editing any of
``_DIMENSION_DESCRIPTIONS``, ``VERDICT_JSON_SCHEMA``, or ``JUDGE_INSTRUCTIONS``
in ``rubric.py`` without updating ``_PINNED_DIGEST`` here (or bumping
``RUBRIC_VERSION``) makes ``test_rubric_content_digest_matches_pinned_version``
fail, which is the mechanical check the hand-bumped version depends on.
"""

from typing import cast

from agentlens.judge.rubric import (
    MAX_SCORE,
    MIN_SCORE,
    RUBRIC_VERSION,
    VERDICT_JSON_SCHEMA,
    rubric_content_digest,
)
from agentlens.models.judging import RubricDimension

_PINNED_DIGEST = "71fc8533903cbb28f9de25218280a1e03b3998b82c15d1c88cf5dc25533d8c2d"


def _as_object_dict(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return cast(dict[str, object], value)


def test_rubric_version_is_v1() -> None:
    assert RUBRIC_VERSION == "v1"


def test_rubric_content_digest_matches_pinned_version() -> None:
    """The pin fails if the rubric's content changes without a version bump.

    Verified by hand: temporarily changing a dimension description's text in
    ``rubric.py`` and rerunning this test makes it fail, before this change
    is trusted to catch that drift in ``make check``.
    """
    assert rubric_content_digest() == _PINNED_DIGEST


def test_schema_requires_every_rubric_dimension() -> None:
    dimensions_schema = _as_object_dict(
        _as_object_dict(VERDICT_JSON_SCHEMA["properties"])["dimensions"]
    )
    assert set(cast(list[str], dimensions_schema["required"])) == {d.value for d in RubricDimension}
    assert dimensions_schema["additionalProperties"] is False


def test_schema_bounds_every_score_to_the_rubric_scale() -> None:
    top_properties = _as_object_dict(VERDICT_JSON_SCHEMA["properties"])
    dimensions_properties = _as_object_dict(
        _as_object_dict(top_properties["dimensions"])["properties"]
    )
    for dimension in RubricDimension:
        dimension_schema = _as_object_dict(dimensions_properties[dimension.value])
        score_schema = _as_object_dict(_as_object_dict(dimension_schema["properties"])["score"])
        assert score_schema["minimum"] == MIN_SCORE
        assert score_schema["maximum"] == MAX_SCORE
    overall_schema = _as_object_dict(top_properties["overall_score"])
    assert overall_schema["minimum"] == MIN_SCORE
    assert overall_schema["maximum"] == MAX_SCORE


def test_schema_forbids_unknown_top_level_fields() -> None:
    assert VERDICT_JSON_SCHEMA["additionalProperties"] is False
    assert set(cast(list[str], VERDICT_JSON_SCHEMA["required"])) == {
        "overall_score",
        "dimensions",
        "suggested_fixes",
    }
