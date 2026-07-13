"""Tests for agentlens.judge.rubric."""

from __future__ import annotations

from agentlens.judge.rubric import (
    DIMENSION_NAMES,
    RUBRIC_PROMPT_TEMPLATE,
    RUBRIC_VERSION,
    VERDICT_JSON_SCHEMA,
)

EXPECTED_DIMENSION_NAMES = {
    "task_completion",
    "honesty",
    "efficiency",
    "scope_adherence",
}


def test_verdict_json_schema_validates_good_verdict() -> None:
    assert set(VERDICT_JSON_SCHEMA) == {"type", "properties", "required", "additionalProperties"}
    assert VERDICT_JSON_SCHEMA["type"] == "object"
    assert set(VERDICT_JSON_SCHEMA["required"]) == {
        "dimensions",
        "suggested_fixes",
    }

    dimensions_schema = VERDICT_JSON_SCHEMA["properties"]["dimensions"]
    assert set(dimensions_schema["required"]) == EXPECTED_DIMENSION_NAMES
    assert len(dimensions_schema["required"]) == 4


def test_schema_requires_all_four_dimensions() -> None:
    dimension_properties = VERDICT_JSON_SCHEMA["properties"]["dimensions"]["properties"]
    assert set(dimension_properties) == EXPECTED_DIMENSION_NAMES
    assert len(dimension_properties) == 4
    for dimension_schema in dimension_properties.values():
        assert set(dimension_schema["required"]) == {"score", "evidence"}


def test_schema_score_maximum() -> None:
    dimension_properties = VERDICT_JSON_SCHEMA["properties"]["dimensions"]["properties"]
    for dimension_schema in dimension_properties.values():
        score_schema = dimension_schema["properties"]["score"]
        assert score_schema["maximum"] == 5
        assert score_schema["minimum"] == 0
        assert score_schema["type"] == "integer"


def test_rubric_version_is_v1() -> None:
    assert RUBRIC_VERSION == "v1"


def test_rubric_prompt_template_mentions_all_dimensions() -> None:
    assert set(DIMENSION_NAMES) == EXPECTED_DIMENSION_NAMES
    for dimension_name in DIMENSION_NAMES:
        assert dimension_name in RUBRIC_PROMPT_TEMPLATE


def test_schema_does_not_include_overall_score() -> None:
    assert "overall_score" not in VERDICT_JSON_SCHEMA["properties"]
    assert "overall_score" not in VERDICT_JSON_SCHEMA["required"]


def test_rubric_prompt_contains_untrusted_warning() -> None:
    prompt_lower = RUBRIC_PROMPT_TEMPLATE.lower()
    assert "untrusted" in prompt_lower


def test_rubric_prompt_does_not_ask_model_to_compute_overall() -> None:
    prompt_lower = RUBRIC_PROMPT_TEMPLATE.lower()
    assert "compute `overall_score`" not in prompt_lower
    assert "compute an overall score" not in prompt_lower
