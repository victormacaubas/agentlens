"""Tests for agentlens.judge.protocol."""

from __future__ import annotations

import dataclasses
import json

import pytest

from agentlens.errors import JudgeError
from agentlens.judge.protocol import (
    DIAGNOSTIC_EXCERPT_MAX_CHARS,
    DimensionScore,
    SuggestedFix,
    Verdict,
    bounded_diagnostic,
    validate_verdict,
)
from agentlens.judge.rubric import (
    MAX_EVIDENCE_ITEM_LENGTH,
    MAX_EVIDENCE_ITEMS,
    MAX_FIX_RATIONALE_LENGTH,
    MAX_FIX_RECOMMENDATION_LENGTH,
    MAX_SUGGESTED_FIXES,
)

_FIX = SuggestedFix(
    dimension="efficiency",
    target="agent_instructions",
    recommendation="avoid re-reading files already read",
    rationale="the agent read the same file twice in this run",
)


def _verdict(**overrides: object) -> Verdict:
    defaults: dict[str, object] = {
        "session_id": "session-1",
        "rubric_version": "v2",
        "judge_model": "claude-sonnet-5",
        "dimensions": {
            "task_completion": DimensionScore(score=4, evidence=["did the task"]),
            "honesty": DimensionScore(score=5, evidence=["disclosed limitations"]),
            "efficiency": DimensionScore(score=3, evidence=["some redundant reads"]),
            "scope_adherence": DimensionScore(score=4, evidence=["stayed in scope"]),
        },
        "overall_score": 4.0,
        "suggested_fixes": [_FIX],
        "judge_cost_usd": 0.02,
        "judge_input_tokens": 1500,
        "judge_output_tokens": 200,
    }
    defaults.update(overrides)
    return Verdict(**defaults)  # type: ignore[arg-type]


def test_verdict_overall_score_is_mean_of_dimensions() -> None:
    verdict = _verdict(
        dimensions={
            "task_completion": DimensionScore(score=4, evidence=[]),
            "honesty": DimensionScore(score=5, evidence=[]),
            "efficiency": DimensionScore(score=3, evidence=[]),
            "scope_adherence": DimensionScore(score=4, evidence=[]),
        },
        overall_score=4.0,
    )
    scores = [dim.score for dim in verdict.dimensions.values()]
    assert verdict.overall_score == sum(scores) / len(scores)
    assert verdict.overall_score == 4.0


def test_verdict_to_verdict_json_serialization() -> None:
    verdict = _verdict()

    payload = verdict.to_verdict_json()
    serialized = json.dumps(payload)
    reloaded = json.loads(serialized)

    assert reloaded["overall_score"] == 4.0
    assert reloaded["suggested_fixes"] == [
        {
            "dimension": "efficiency",
            "target": "agent_instructions",
            "recommendation": "avoid re-reading files already read",
            "rationale": "the agent read the same file twice in this run",
        }
    ]
    assert reloaded["dimensions"]["task_completion"] == {
        "score": 4,
        "evidence": ["did the task"],
    }
    assert set(reloaded["dimensions"]) == {
        "task_completion",
        "honesty",
        "efficiency",
        "scope_adherence",
    }
    # Cost/identity fields are stored as dedicated fact_verdict columns, not
    # duplicated inside verdict_json.
    for excluded_key in (
        "session_id",
        "rubric_version",
        "judge_model",
        "judge_cost_usd",
        "judge_input_tokens",
        "judge_output_tokens",
    ):
        assert excluded_key not in reloaded


def test_verdict_to_verdict_json_with_no_fixes_serializes_empty_list() -> None:
    verdict = _verdict(suggested_fixes=[])

    payload = verdict.to_verdict_json()

    assert payload["suggested_fixes"] == []


def test_verdict_to_verdict_json_marks_provenance() -> None:
    verdict = _verdict()

    payload = verdict.to_verdict_json()

    provenance = payload["provenance"]
    assert "overall_score" in provenance["locally_derived"]
    assert "dimensions.*.score" in provenance["locally_derived"]
    assert "dimensions.*.evidence" in provenance["untrusted_model_output"]
    assert "suggested_fixes[].recommendation" in provenance["untrusted_model_output"]
    assert "suggested_fixes[].rationale" in provenance["untrusted_model_output"]
    # Provenance itself must survive a JSON round-trip.
    assert json.loads(json.dumps(payload))["provenance"] == provenance


def test_suggested_fix_construction() -> None:
    fix = SuggestedFix(
        dimension="honesty",
        target="caller_task_phrasing",
        recommendation="ask the caller to state the acceptance criteria explicitly",
        rationale="the report was ambiguous about whether the task was complete",
    )
    assert fix.dimension == "honesty"
    assert fix.target == "caller_task_phrasing"


def test_suggested_fix_frozen() -> None:
    fix = SuggestedFix(
        dimension="honesty",
        target="agent_instructions",
        recommendation="be explicit",
        rationale="the report omitted a known failure",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        fix.recommendation = "changed"  # type: ignore[misc]


def test_dimension_score_frozen() -> None:
    dimension = DimensionScore(score=3, evidence=["some evidence"])
    with pytest.raises(dataclasses.FrozenInstanceError):
        dimension.score = 5  # type: ignore[misc]


def test_verdict_frozen() -> None:
    verdict = _verdict()
    with pytest.raises(dataclasses.FrozenInstanceError):
        verdict.overall_score = 1.0  # type: ignore[misc]


def test_validate_verdict_derives_overall_score_locally() -> None:
    verdict = validate_verdict(_verdict(overall_score=99.0))

    assert verdict.overall_score == 4.0


@pytest.mark.parametrize(
    "dimensions",
    [
        {
            "task_completion": DimensionScore(score=4, evidence=[]),
            "honesty": DimensionScore(score=4, evidence=[]),
            "efficiency": DimensionScore(score=4, evidence=[]),
        },
        {
            "task_completion": DimensionScore(score=4, evidence=[]),
            "honesty": DimensionScore(score=4, evidence=[]),
            "efficiency": DimensionScore(score=4, evidence=[]),
            "scope_adherence": DimensionScore(score=4, evidence=[]),
            "unexpected": DimensionScore(score=4, evidence=[]),
        },
    ],
)
def test_validate_verdict_rejects_non_exact_dimension_set(
    dimensions: dict[str, DimensionScore],
) -> None:
    with pytest.raises(JudgeError):
        validate_verdict(_verdict(dimensions=dimensions))


@pytest.mark.parametrize("score", [-1, 6, 2.5, True])
def test_validate_verdict_rejects_invalid_dimension_scores(score: object) -> None:
    dimensions = dict(_verdict().dimensions)
    dimensions["efficiency"] = DimensionScore(score=score, evidence=[])  # type: ignore[arg-type]

    with pytest.raises(JudgeError):
        validate_verdict(_verdict(dimensions=dimensions))


@pytest.mark.parametrize(
    "evidence",
    [
        ["evidence"] * (MAX_EVIDENCE_ITEMS + 1),
        ["x" * (MAX_EVIDENCE_ITEM_LENGTH + 1)],
    ],
)
def test_validate_verdict_rejects_oversized_evidence(evidence: list[str]) -> None:
    dimensions = dict(_verdict().dimensions)
    dimensions["honesty"] = DimensionScore(score=4, evidence=evidence)

    with pytest.raises(JudgeError):
        validate_verdict(_verdict(dimensions=dimensions))


@pytest.mark.parametrize(
    "suggested_fixes",
    [
        [_FIX] * (MAX_SUGGESTED_FIXES + 1),
        [
            SuggestedFix(
                dimension="efficiency",
                target="agent_instructions",
                recommendation="x" * (MAX_FIX_RECOMMENDATION_LENGTH + 1),
                rationale="reason",
            )
        ],
        [
            SuggestedFix(
                dimension="efficiency",
                target="agent_instructions",
                recommendation="change",
                rationale="x" * (MAX_FIX_RATIONALE_LENGTH + 1),
            )
        ],
        ["free-form fix"],
    ],
)
def test_validate_verdict_rejects_invalid_or_oversized_fixes(
    suggested_fixes: list[object],
) -> None:
    with pytest.raises(JudgeError):
        validate_verdict(_verdict(suggested_fixes=suggested_fixes))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("judge_cost_usd", -0.01),
        ("judge_cost_usd", True),
        ("judge_cost_usd", float("nan")),
        ("judge_cost_usd", float("inf")),
        ("judge_cost_usd", "0.01"),
        ("judge_input_tokens", -1),
        ("judge_input_tokens", True),
        ("judge_input_tokens", float("nan")),
        ("judge_input_tokens", float("inf")),
        ("judge_input_tokens", "100"),
        ("judge_output_tokens", -1),
        ("judge_output_tokens", True),
    ],
)
def test_validate_verdict_rejects_invalid_accounting(field: str, value: object) -> None:
    with pytest.raises(JudgeError):
        validate_verdict(_verdict(**{field: value}))


@pytest.mark.parametrize("judge_model", ["", " sonnet", "sonnet", "opusplan"])
def test_validate_verdict_requires_concrete_model_identity(judge_model: str) -> None:
    with pytest.raises(JudgeError):
        validate_verdict(_verdict(judge_model=judge_model))


def test_bounded_diagnostic_truncates_private_sentinel() -> None:
    sentinel = "private-sentinel-" + "x" * 1_000

    diagnostic = bounded_diagnostic(JudgeError(sentinel))

    assert len(diagnostic) <= DIAGNOSTIC_EXCERPT_MAX_CHARS + len("... [truncated]")
    assert sentinel not in diagnostic
    assert diagnostic.endswith("... [truncated]")
