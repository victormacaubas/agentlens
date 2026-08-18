"""The terminal summary: what was observed, with no score presented."""

from pathlib import Path

from agentlens.render.summary import build_session_summary
from tests.factories import build_fact_session, build_session_facts


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
