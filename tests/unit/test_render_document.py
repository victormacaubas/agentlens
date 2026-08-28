"""The JSON report document: versioned, unscored, one row per qualified spawn."""

from dataclasses import asdict
from datetime import UTC, datetime

import pytest

from agentlens.models.judging import VERDICT_PROVENANCE, RubricDimension
from agentlens.models.report_aggregates import TrendStatus
from agentlens.models.scoring import ScoringStatus
from agentlens.render.document import build_report_document_json, build_session_document
from tests.factories import (
    build_agent_rollup,
    build_dimension_score,
    build_fact_session,
    build_fact_verdict,
    build_report_document,
    build_report_spawn,
    build_resolved_window,
    build_run_judge_usage,
    build_scoring_outcome,
    build_session_facts,
    build_session_skill_signal,
    build_suggested_fix,
    build_verdict,
    build_window_selector,
)
from tests.fakes import FakeClock

FORBIDDEN_KEYS = {"score", "verdict", "fix"}


def _assert_no_scoring_keys(value: object) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            assert key not in FORBIDDEN_KEYS, f"forbidden key {key!r} found in document"
            _assert_no_scoring_keys(nested)
    elif isinstance(value, list):
        for item in value:
            _assert_no_scoring_keys(item)


def test_document_carries_schema_version_and_unscored_marker() -> None:
    facts = build_session_facts()
    clock = FakeClock(instant=datetime(2026, 1, 1, tzinfo=UTC))

    document = build_session_document(facts, clock=clock)

    assert document["schema_version"] == 3
    assert document["scoring_status"] == "unscored"


def test_document_has_one_row_for_the_qualified_spawn() -> None:
    facts = build_session_facts()
    clock = FakeClock(instant=datetime(2026, 1, 1, tzinfo=UTC))

    document = build_session_document(facts, clock=clock)

    spawns = document["spawns"]
    assert isinstance(spawns, list)
    assert len(spawns) == 1
    row = spawns[0]
    assert row["session_id"] == facts.session.identity.session_id
    assert row["source_project"] == facts.session.identity.source_project
    assert row["raw_session_id"] == facts.session.identity.raw_session_id


def test_document_generated_at_is_timezone_aware_utc_from_the_injected_clock() -> None:
    facts = build_session_facts()
    fixed = datetime(2026, 3, 4, 12, 30, tzinfo=UTC)
    clock = FakeClock(instant=fixed)

    document = build_session_document(facts, clock=clock)

    generated_at = datetime.fromisoformat(str(document["generated_at"]))
    assert generated_at == fixed
    assert generated_at.tzinfo is not None
    assert generated_at.utcoffset() == fixed.utcoffset()


def test_ingested_but_unscored_spawn_row_carries_its_deterministic_fields() -> None:
    session = build_fact_session(
        n_turns=3,
        n_invocations=5,
        n_reads=2,
        n_edits=1,
        n_writes=1,
        n_bash=1,
        n_distinct_files=2,
        n_errors=1,
        duration_ms=4200,
        input_tokens=100,
        output_tokens=50,
        cache_read_tokens=30,
        cache_creation_tokens=10,
        unreadable_line_count=2,
    )
    facts = build_session_facts(session=session)
    clock = FakeClock(instant=datetime(2026, 1, 1, tzinfo=UTC))

    document = build_session_document(facts, clock=clock)

    row = document["spawns"][0]  # type: ignore[index]
    assert row["n_turns"] == 3
    assert row["n_invocations"] == 5
    assert row["n_errors"] == 1
    assert row["duration_ms"] == 4200
    assert row["cache_read_tokens"] == 30
    assert row["unreadable_line_count"] == 2


def test_no_score_verdict_or_fix_key_appears_anywhere_in_the_document() -> None:
    facts = build_session_facts()
    clock = FakeClock(instant=datetime(2026, 1, 1, tzinfo=UTC))

    document = build_session_document(facts, clock=clock)

    _assert_no_scoring_keys(document)


def test_scored_row_carries_every_field_the_spec_names() -> None:
    facts = build_session_facts()
    clock = FakeClock(instant=datetime(2026, 1, 1, tzinfo=UTC))
    scored = build_verdict(
        overall_score=3,
        dimensions={
            dimension: build_dimension_score(score=3, evidence=("Evidence.",))
            for dimension in RubricDimension
        },
        suggested_fixes=(build_suggested_fix(),),
    )
    fact_verdict = build_fact_verdict(
        verdict=scored,
        rubric_version="v1",
        judge_model="claude-sonnet-5",
        judge_cost_usd=0.02,
        judge_input_tokens=100,
        judge_output_tokens=20,
        scored_at=datetime(2026, 1, 1, 1, tzinfo=UTC),
    )

    document = build_session_document(
        facts,
        clock=clock,
        scoring_outcome=build_scoring_outcome(verdict=fact_verdict),
    )

    row = document["spawns"][0]  # type: ignore[index]
    verdict_row = row["verdict"]
    assert verdict_row["rubric_version"] == "v1"
    assert verdict_row["judge_model"] == "claude-sonnet-5"
    assert verdict_row["judge_cost_usd"] == 0.02
    assert verdict_row["judge_input_tokens"] == 100
    assert verdict_row["judge_output_tokens"] == 20
    assert verdict_row["scored_at"] == datetime(2026, 1, 1, 1, tzinfo=UTC).isoformat()
    assert verdict_row["overall_score"] == 3
    for dimension in RubricDimension:
        dimension_row = verdict_row["dimensions"][dimension.value]
        assert dimension_row["score"] == 3
        assert dimension_row["evidence"] == ["Evidence."]
    assert verdict_row["suggested_fixes"] == [
        {
            "dimension": "efficiency",
            "target": "the retry loop in ingest/session.py",
            "recommendation": "Cap the retry count instead of retrying unconditionally.",
            "rationale": "The transcript shows five retries of the same read.",
        }
    ]
    assert document["scoring_status"] == "scored"


def test_document_built_without_verdict_matches_pre_scoring_shape_except_schema_version() -> None:
    facts = build_session_facts()
    clock = FakeClock(instant=datetime(2026, 1, 1, tzinfo=UTC))

    document = build_session_document(facts, clock=clock, scoring_outcome=None)

    assert document["schema_version"] == 3
    assert document["scoring_status"] == "unscored"
    row = document["spawns"][0]  # type: ignore[index]
    assert "verdict" not in row


@pytest.mark.parametrize(
    ("status", "has_verdict"),
    (
        (ScoringStatus.SCORED, True),
        (ScoringStatus.REUSED, True),
        (ScoringStatus.CLAIMED_ELSEWHERE, False),
        (ScoringStatus.FAILED, False),
    ),
)
def test_document_distinguishes_every_scoring_outcome(
    status: ScoringStatus,
    has_verdict: bool,
) -> None:
    document = build_session_document(
        build_session_facts(),
        clock=FakeClock(instant=datetime(2026, 1, 1, tzinfo=UTC)),
        scoring_outcome=build_scoring_outcome(status=status),
    )

    row = document["spawns"][0]  # type: ignore[index]
    assert document["scoring_status"] == status.value
    assert ("verdict" in row) is has_verdict


def test_reused_document_marks_the_historical_verdict_and_this_runs_zero_usage() -> None:
    historical_verdict = build_fact_verdict(
        judge_cost_usd=0.25,
        judge_input_tokens=200,
        judge_output_tokens=40,
    )
    document = build_session_document(
        build_session_facts(),
        clock=FakeClock(instant=datetime(2026, 1, 1, tzinfo=UTC)),
        scoring_outcome=build_scoring_outcome(
            status=ScoringStatus.REUSED,
            verdict=historical_verdict,
        ),
    )

    row = document["spawns"][0]  # type: ignore[index]
    assert row["is_reused"] is True
    assert row["verdict"]["scored_at"] == historical_verdict.scored_at.isoformat()
    assert row["run_judge_usage"] == {
        "cost_usd": 0.0,
        "input_tokens": 0,
        "output_tokens": 0,
    }


def test_failed_document_serializes_its_run_usage_without_a_verdict() -> None:
    document = build_session_document(
        build_session_facts(),
        clock=FakeClock(instant=datetime(2026, 1, 1, tzinfo=UTC)),
        scoring_outcome=build_scoring_outcome(
            status=ScoringStatus.FAILED,
            run_judge_usage=build_run_judge_usage(
                cost_usd=0.045,
                input_tokens=200,
                output_tokens=40,
            ),
        ),
    )

    row = document["spawns"][0]  # type: ignore[index]
    assert row["run_judge_usage"] == {
        "cost_usd": 0.045,
        "input_tokens": 200,
        "output_tokens": 40,
    }


def test_claimed_elsewhere_document_has_no_verdict_score_or_fix_field() -> None:
    document = build_session_document(
        build_session_facts(),
        clock=FakeClock(instant=datetime(2026, 1, 1, tzinfo=UTC)),
        scoring_outcome=build_scoring_outcome(status=ScoringStatus.CLAIMED_ELSEWHERE),
    )

    assert document["scoring_status"] == ScoringStatus.CLAIMED_ELSEWHERE.value
    _assert_no_scoring_keys(document)


def test_document_marks_a_verdict_behind_its_current_input() -> None:
    document = build_session_document(
        build_session_facts(),
        clock=FakeClock(instant=datetime(2026, 1, 1, tzinfo=UTC)),
        scoring_outcome=build_scoring_outcome(is_behind_current_input=True),
    )

    row = document["spawns"][0]  # type: ignore[index]
    assert row["is_behind_current_input"] is True


def test_provenance_in_document_exposes_exactly_verdict_provenances_two_lists() -> None:
    facts = build_session_facts()
    clock = FakeClock(instant=datetime(2026, 1, 1, tzinfo=UTC))
    fact_verdict = build_fact_verdict()

    document = build_session_document(
        facts,
        clock=clock,
        scoring_outcome=build_scoring_outcome(verdict=fact_verdict),
    )

    row = document["spawns"][0]  # type: ignore[index]
    provenance = row["verdict"]["provenance"]
    assert provenance == {
        "locally_derived": list(VERDICT_PROVENANCE.locally_derived),
        "untrusted_model_output": list(VERDICT_PROVENANCE.untrusted_model_output),
    }


def test_report_document_json_carries_schema_version_generated_at_and_window() -> None:
    window = build_resolved_window(
        selector=build_window_selector(since_duration="7d"), local_timezone="America/Sao_Paulo"
    )
    document = build_report_document(
        window=window, agent_filter="implementer", generated_at=datetime(2026, 1, 15, tzinfo=UTC)
    )

    rendered = build_report_document_json(document)

    assert rendered["schema_version"] == document.schema_version
    assert rendered["generated_at"] == document.generated_at.isoformat()
    assert rendered["agent_filter"] == "implementer"
    window_row = rendered["window"]
    assert isinstance(window_row, dict)
    assert window_row["selector"] == {
        "since_duration": "7d",
        "named_window": None,
        "range_from": None,
        "range_to": None,
    }
    assert window_row["current_start"] == window.current_start.isoformat()
    assert window_row["current_end"] == window.current_end.isoformat()
    assert window_row["prior_start"] == window.prior_start.isoformat()
    assert window_row["prior_end"] == window.prior_end.isoformat()
    assert window_row["local_timezone"] == "America/Sao_Paulo"
    assert window_row["min_sessions_for_trend"] == window.min_sessions_for_trend


def test_report_document_json_has_one_row_for_every_qualifying_spawn() -> None:
    spawns = (
        build_report_spawn(session=build_fact_session(agent_type="implementer")),
        build_report_spawn(session=build_fact_session(agent_type="pathfinder")),
        build_report_spawn(session=build_fact_session(agent_type="researcher")),
    )
    document = build_report_document(spawns=spawns)

    rendered = build_report_document_json(document)

    spawn_rows = rendered["spawns"]
    assert isinstance(spawn_rows, list)
    assert len(spawn_rows) == 3
    assert {row["agent_type"] for row in spawn_rows} == {"implementer", "pathfinder", "researcher"}


def test_report_spawn_row_exposes_identity_definition_parent_and_skill_facts() -> None:
    session = build_fact_session(
        agent_id="agent-raw-123",
        agent_definition_id="definition-abc",
        parent_session_id="parent-session-xyz",
        started_at=datetime(2026, 1, 10, tzinfo=UTC),
        task_prompt_len=42,
    )
    signal = build_session_skill_signal(skill_name="python-engineering-standards", fired=True)
    spawn = build_report_spawn(session=session, skill_signals=(signal,))
    document = build_report_document(spawns=(spawn,))

    rendered = build_report_document_json(document)

    row = rendered["spawns"][0]  # type: ignore[index]
    assert row["agent_id"] == "agent-raw-123"
    assert row["agent_definition_id"] == "definition-abc"
    assert row["parent_session_id"] == "parent-session-xyz"
    assert row["started_at"] == session.started_at.isoformat()
    assert row["task_prompt_len"] == 42
    signal_rows = row["skill_signals"]
    assert signal_rows == [
        {
            "skill_name": "python-engineering-standards",
            "declared": "unknown",
            "available": "unknown",
            "fired": True,
        }
    ]


def test_report_spawn_row_keeps_unknown_definition_and_skill_context_explicit() -> None:
    session = build_fact_session(agent_definition_id=None, parent_session_id=None)
    spawn = build_report_spawn(session=session, skill_signals=())
    document = build_report_document(spawns=(spawn,))

    rendered = build_report_document_json(document)

    row = rendered["spawns"][0]  # type: ignore[index]
    assert row["agent_definition_id"] is None
    assert row["parent_session_id"] is None
    assert row["skill_signals"] == []


def test_report_document_json_carries_one_rollup_per_agent_type() -> None:
    rollup = build_agent_rollup(agent_type="implementer", n_spawns=6, n_spawns_prior=6)
    document = build_report_document(agent_rollups=(rollup,))

    rendered = build_report_document_json(document)

    rollup_rows = rendered["agent_rollups"]
    assert isinstance(rollup_rows, list)
    assert len(rollup_rows) == 1
    row = rollup_rows[0]
    assert row["agent_type"] == "implementer"
    assert row["n_spawns"] == 6
    assert row["n_spawns_prior"] == 6
    assert row["totals"] == asdict(rollup.totals)


def test_low_volume_agent_rollup_carries_no_directional_trend() -> None:
    rollup = build_agent_rollup(
        agent_type="implementer",
        n_spawns=4,
        n_spawns_prior=1,
        trend_status=TrendStatus.INSUFFICIENT_DATA,
        prior_averages=None,
        average_deltas=None,
    )
    document = build_report_document(agent_rollups=(rollup,))

    rendered = build_report_document_json(document)

    row = rendered["agent_rollups"][0]  # type: ignore[index]
    assert row["trend_status"] == "insufficient_data"
    assert row["prior_averages"] is None
    assert row["average_deltas"] is None


def test_no_modeled_field_appears_anywhere_in_the_report_document() -> None:
    spawn = build_report_spawn(skill_signals=(build_session_skill_signal(),))
    rollup = build_agent_rollup()
    document = build_report_document(spawns=(spawn,), agent_rollups=(rollup,))

    rendered = build_report_document_json(document)

    _assert_no_scoring_keys(rendered)
