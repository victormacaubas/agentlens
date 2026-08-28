"""The terminal summary: what was observed, with no score presented."""

from pathlib import Path

from agentlens.models.judging import RubricDimension
from agentlens.models.report_aggregates import TrendStatus
from agentlens.render.summary import build_report_summary, build_session_summary
from tests.factories import (
    build_agent_rollup,
    build_dimension_score,
    build_fact_session,
    build_fact_verdict,
    build_report_document,
    build_report_spawn,
    build_resolved_window,
    build_session_facts,
    build_suggested_fix,
    build_verdict,
)

_ADVERSARIAL_EVIDENCE = ('"; rm -rf /"', "\x1b[31mALERT\x1b[0m", "line one\nline two")


def test_summary_names_agent_type_task_and_artifact_path() -> None:
    session = build_fact_session(agent_type="implementer", task_description="Ship the report")
    facts = build_session_facts(session=session)
    artifact_path = Path("reports/session_session-abc123.json")

    summary = build_session_summary(facts, artifact_path=artifact_path)

    assert "implementer" in summary
    assert "Ship the report" in summary
    assert str(artifact_path) in summary


def test_summary_reports_volume_and_health_counts() -> None:
    session = build_fact_session(
        n_turns=4, n_invocations=6, n_errors=2, n_denials=1, unreadable_line_count=3
    )
    facts = build_session_facts(session=session)

    summary = build_session_summary(facts, artifact_path=Path("reports/session_x.json"))

    assert "turns=4" in summary
    assert "invocations=6" in summary
    assert "errors=2" in summary
    assert "denials=1" in summary
    assert "unreadable_lines=3" in summary


def test_summary_reports_cache_read_proportion() -> None:
    session = build_fact_session(cache_read_tokens=25, input_tokens=75)
    facts = build_session_facts(session=session)

    summary = build_session_summary(facts, artifact_path=Path("reports/session_x.json"))

    assert "cache_read_proportion=25.0%" in summary


def test_summary_handles_zero_tokens_without_dividing_by_zero() -> None:
    session = build_fact_session(cache_read_tokens=0, input_tokens=0)
    facts = build_session_facts(session=session)

    summary = build_session_summary(facts, artifact_path=Path("reports/session_x.json"))

    assert "cache_read_proportion=0.0%" in summary


def test_summary_presents_no_score_and_states_the_session_is_unscored() -> None:
    facts = build_session_facts()

    summary = build_session_summary(facts, artifact_path=Path("reports/session_x.json"))

    assert "unscored" in summary
    assert "verdict" not in summary.lower()
    assert "fix" not in summary.lower()
    assert "score:" not in summary.lower()


def test_scored_summary_names_overall_and_dimension_scores() -> None:
    facts = build_session_facts()
    scored = build_verdict(overall_score=4)
    fact_verdict = build_fact_verdict(verdict=scored)

    summary = build_session_summary(
        facts, artifact_path=Path("reports/session_x.json"), verdict=fact_verdict
    )

    assert "overall_score: 4" in summary
    for dimension in RubricDimension:
        assert f"{dimension.value}: {scored.dimensions[dimension].score}" in summary
    assert "unscored" not in summary


def test_scored_summary_names_where_fixes_were_recorded_without_fix_text() -> None:
    facts = build_session_facts()
    fix = build_suggested_fix(
        target="the retry loop",
        recommendation="Cap the retries.",
        rationale="Five retries observed.",
    )
    scored = build_verdict(suggested_fixes=(fix,))
    fact_verdict = build_fact_verdict(verdict=scored)
    artifact_path = Path("reports/session_x.json")

    summary = build_session_summary(facts, artifact_path=artifact_path, verdict=fact_verdict)

    assert str(artifact_path) in summary
    assert "Cap the retries." not in summary
    assert "Five retries observed." not in summary
    assert "the retry loop" not in summary


def test_scored_summary_reports_judge_cost_and_tokens() -> None:
    facts = build_session_facts()
    fact_verdict = build_fact_verdict(
        judge_cost_usd=0.0123, judge_input_tokens=200, judge_output_tokens=40
    )

    summary = build_session_summary(
        facts, artifact_path=Path("reports/session_x.json"), verdict=fact_verdict
    )

    assert "$0.0123" in summary
    assert "judge_input_tokens=200" in summary
    assert "judge_output_tokens=40" in summary


def test_scored_summary_reports_analyzed_usage_with_no_currency_figure() -> None:
    session = build_fact_session(
        input_tokens=111, output_tokens=22, cache_read_tokens=33, cache_creation_tokens=44
    )
    facts = build_session_facts(session=session)
    fact_verdict = build_fact_verdict()

    summary = build_session_summary(
        facts, artifact_path=Path("reports/session_x.json"), verdict=fact_verdict
    )

    analyzed_line = next(
        line for line in summary.splitlines() if line.startswith("analyzed_tokens")
    )
    assert "$" not in analyzed_line
    assert "input=111" in analyzed_line
    assert "output=22" in analyzed_line
    assert "cache_read=33" in analyzed_line
    assert "cache_creation=44" in analyzed_line


def test_scored_summary_never_reaches_adversarial_evidence_or_fix_text() -> None:
    benign_dimensions = {
        dimension: build_dimension_score(score=2, evidence=("Benign evidence.",))
        for dimension in RubricDimension
    }
    benign_fix = build_suggested_fix(
        target="benign target", recommendation="benign rec", rationale="benign rationale"
    )
    benign_verdict = build_verdict(dimensions=benign_dimensions, suggested_fixes=(benign_fix,))
    benign_fact_verdict = build_fact_verdict(verdict=benign_verdict)
    facts = build_session_facts()
    artifact_path = Path("reports/session_x.json")

    benign_summary = build_session_summary(
        facts, artifact_path=artifact_path, verdict=benign_fact_verdict
    )

    for adversarial in _ADVERSARIAL_EVIDENCE:
        hostile_dimensions = {
            dimension: build_dimension_score(score=2, evidence=(adversarial,))
            for dimension in RubricDimension
        }
        hostile_fix = build_suggested_fix(
            target=adversarial, recommendation=adversarial, rationale=adversarial
        )
        hostile_verdict = build_verdict(
            dimensions=hostile_dimensions, suggested_fixes=(hostile_fix,)
        )
        hostile_fact_verdict = build_fact_verdict(verdict=hostile_verdict)

        hostile_summary = build_session_summary(
            facts, artifact_path=artifact_path, verdict=hostile_fact_verdict
        )

        assert adversarial not in hostile_summary
        assert len(hostile_summary.splitlines()) == len(benign_summary.splitlines())


def test_unscored_summary_is_unchanged_from_before_scoring() -> None:
    facts = build_session_facts()

    summary = build_session_summary(facts, artifact_path=Path("reports/session_x.json"))

    assert "unscored" in summary
    assert "overall_score" not in summary


def test_report_summary_names_the_window_scope_and_artifact_path() -> None:
    window = build_resolved_window(local_timezone="America/Sao_Paulo")
    document = build_report_document(window=window, agent_filter="implementer")
    artifact_path = Path("reports/report_abc123.json")

    summary = build_report_summary(document, artifact_path=artifact_path)

    assert window.current_start.isoformat() in summary
    assert window.current_end.isoformat() in summary
    assert "America/Sao_Paulo" in summary
    assert "agent_scope: implementer" in summary
    assert str(artifact_path) in summary


def test_report_summary_shows_all_as_the_scope_when_no_agent_filter_is_applied() -> None:
    document = build_report_document(agent_filter=None)

    summary = build_report_summary(document, artifact_path=Path("reports/report_all.json"))

    assert "agent_scope: all" in summary


def test_report_summary_reports_total_spawns() -> None:
    spawns = (build_report_spawn(), build_report_spawn(), build_report_spawn())
    document = build_report_document(spawns=spawns)

    summary = build_report_summary(document, artifact_path=Path("reports/report_x.json"))

    assert "total_spawns: 3" in summary


def test_report_summary_names_each_agent_rollups_current_and_prior_population_and_trend() -> None:
    comparable = build_agent_rollup(
        agent_type="implementer", n_spawns=6, n_spawns_prior=5, trend_status=TrendStatus.COMPARABLE
    )
    insufficient = build_agent_rollup(
        agent_type="pathfinder",
        n_spawns=2,
        n_spawns_prior=0,
        trend_status=TrendStatus.INSUFFICIENT_DATA,
    )
    document = build_report_document(agent_rollups=(comparable, insufficient))

    summary = build_report_summary(document, artifact_path=Path("reports/report_x.json"))

    assert "implementer: n_spawns=6 n_spawns_prior=5 trend=comparable" in summary
    assert "pathfinder: n_spawns=2 n_spawns_prior=0 trend=insufficient_data" in summary


def test_report_summary_presents_no_score_and_no_task_description() -> None:
    session = build_fact_session(task_description="Implement the ingest pipeline")
    document = build_report_document(spawns=(build_report_spawn(session=session),))

    summary = build_report_summary(document, artifact_path=Path("reports/report_x.json"))

    assert "score" not in summary.lower()
    assert "verdict" not in summary.lower()
    assert "Implement the ingest pipeline" not in summary
