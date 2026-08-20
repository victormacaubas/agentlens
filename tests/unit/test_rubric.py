"""Tests for agentlens.judge.rubric."""

from __future__ import annotations

from agentlens.judge.rubric import (
    DIMENSION_NAMES,
    FIX_TARGETS,
    MAX_EVIDENCE_ITEM_LENGTH,
    MAX_EVIDENCE_ITEMS,
    MAX_FIX_RATIONALE_LENGTH,
    MAX_FIX_RECOMMENDATION_LENGTH,
    MAX_SUGGESTED_FIXES,
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

EXPECTED_FIX_TARGETS = {
    "agent_instructions",
    "declared_tools",
    "declared_skills",
    "caller_task_phrasing",
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


def test_schema_bounds_evidence_volume() -> None:
    dimension_properties = VERDICT_JSON_SCHEMA["properties"]["dimensions"]["properties"]
    for dimension_schema in dimension_properties.values():
        evidence_schema = dimension_schema["properties"]["evidence"]
        assert evidence_schema["type"] == "array"
        assert evidence_schema["maxItems"] == MAX_EVIDENCE_ITEMS
        assert evidence_schema["items"]["type"] == "string"
        assert evidence_schema["items"]["maxLength"] == MAX_EVIDENCE_ITEM_LENGTH


def test_schema_suggested_fixes_is_bounded_array_of_typed_objects() -> None:
    fixes_schema = VERDICT_JSON_SCHEMA["properties"]["suggested_fixes"]
    assert fixes_schema["type"] == "array"
    assert fixes_schema["maxItems"] == MAX_SUGGESTED_FIXES

    fix_item_schema = fixes_schema["items"]
    assert fix_item_schema["type"] == "object"
    assert fix_item_schema["additionalProperties"] is False
    assert set(fix_item_schema["required"]) == {
        "dimension",
        "target",
        "recommendation",
        "rationale",
    }

    fix_properties = fix_item_schema["properties"]
    assert set(fix_properties["dimension"]["enum"]) == EXPECTED_DIMENSION_NAMES
    assert set(fix_properties["target"]["enum"]) == EXPECTED_FIX_TARGETS
    assert fix_properties["recommendation"]["maxLength"] == MAX_FIX_RECOMMENDATION_LENGTH
    assert fix_properties["rationale"]["maxLength"] == MAX_FIX_RATIONALE_LENGTH


def test_fix_targets_closed_set_matches_expected_values() -> None:
    assert set(FIX_TARGETS) == EXPECTED_FIX_TARGETS
    assert "agent_instructions" in FIX_TARGETS


def test_rubric_version_is_v2() -> None:
    assert RUBRIC_VERSION == "v2"


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


def test_rubric_prompt_requires_typed_fix_shape() -> None:
    prompt_lower = RUBRIC_PROMPT_TEMPLATE.lower()
    assert "dimension" in prompt_lower
    assert "target" in prompt_lower
    assert "recommendation" in prompt_lower
    assert "rationale" in prompt_lower
    for fix_target in FIX_TARGETS:
        assert fix_target in RUBRIC_PROMPT_TEMPLATE


def test_rubric_prompt_forbids_executable_fix_content() -> None:
    prompt_lower = RUBRIC_PROMPT_TEMPLATE.lower()
    assert "command" in prompt_lower
    assert "file path" in prompt_lower
    assert "diff" in prompt_lower
