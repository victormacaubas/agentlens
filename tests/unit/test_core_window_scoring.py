"""Window scoring policy across coverage, retries, the breaker, and cost bounds."""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agentlens.core.window_scoring import (
    WindowScoringContext,
    WindowScoringRun,
)
from agentlens.errors import JudgeUnavailableError
from agentlens.ingest.context import SubagentContextCache
from agentlens.ingest.narrative import build_spawn_narrative
from agentlens.ingest.reading import read_transcript
from agentlens.ingest.sidecar import read_sidecar
from agentlens.ingest.transcript import parse_transcript
from agentlens.judge.prompt import render_prompt
from agentlens.judge.rubric import RUBRIC_VERSION
from agentlens.models.judging import JudgeResponse, RubricDimension
from agentlens.models.scoring import ScoringRequest, WindowScoringOutcome, WindowStopReason
from agentlens.models.windows import ResolvedWindow
from agentlens.store import Store
from agentlens.utils.hashing import hash_text
from tests.factories import (
    build_assistant_record,
    build_fact_verdict,
    build_judge_response,
    build_resolved_window,
    build_subagent_source_bundle,
    build_tool_result_block,
    build_tool_use_block,
    build_transcript_path,
    build_user_record,
    build_verdict_claim,
    build_verdict_claim_identity,
    write_transcript,
)
from tests.fakes import FakeClock, FakeJudgeBackend

_CLOCK = FakeClock(instant=datetime(2026, 1, 1, tzinfo=UTC))
_CLAIM_LEASE = timedelta(minutes=3)
_DEFAULT_REQUESTED_MODEL = "claude-sonnet-5"
_WINDOW_START = datetime(2026, 1, 1, tzinfo=UTC)
_WINDOW_END = datetime(2026, 1, 2, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class _WindowScenario:
    home: Path
    claude_root: Path
    store_path: Path
    clock: FakeClock
    request: ScoringRequest
    agent_type: str | None
    window: ResolvedWindow
    max_run_cost_usd: float

    def score(self, judge: FakeJudgeBackend) -> WindowScoringOutcome:
        return WindowScoringRun(
            context=WindowScoringContext(
                projects_root=self.claude_root / "projects",
                claude_root=self.claude_root,
                store_path=self.store_path,
                clock=self.clock,
                request=self.request,
                agent_type=self.agent_type,
                window=self.window,
            ),
            judge=judge,
            max_run_cost_usd=self.max_run_cost_usd,
        ).score()


def _scenario(
    tmp_path: Path,
    *,
    request: ScoringRequest | None = None,
    agent_type: str | None = None,
    max_run_cost_usd: float = 2.00,
) -> _WindowScenario:
    home = tmp_path / "home"
    return _WindowScenario(
        home=home,
        claude_root=home / ".claude",
        store_path=tmp_path / "store" / "agentlens.db",
        clock=_CLOCK,
        request=_scoring_request() if request is None else request,
        agent_type=agent_type,
        window=_window(),
        max_run_cost_usd=max_run_cost_usd,
    )


def _valid_structured_output() -> dict[str, object]:
    return {
        "overall_score": 4,
        "dimensions": {
            dimension.value: {"score": 4, "evidence": ["Evidence."]}
            for dimension in RubricDimension
        },
        "suggested_fixes": [],
    }


def _structured_output_missing_a_dimension() -> dict[str, object]:
    dimensions = {
        dimension.value: {"score": 4, "evidence": ["Evidence."]}
        for dimension in RubricDimension
        if dimension is not RubricDimension.HONESTY
    }
    return {"overall_score": 4, "dimensions": dimensions, "suggested_fixes": []}


def _scoring_request(
    *,
    requested_model: str = _DEFAULT_REQUESTED_MODEL,
    owner: str = "scorer-one",
) -> ScoringRequest:
    return ScoringRequest(requested_model=requested_model, owner=owner, claim_lease=_CLAIM_LEASE)


def _window() -> ResolvedWindow:
    return build_resolved_window(current_start=_WINDOW_START, current_end=_WINDOW_END)


def _records_at(
    timestamp: str, *, file_path: str = "/workspace/example.txt"
) -> list[dict[str, object]]:
    return [
        build_assistant_record(
            content=[build_tool_use_block(input={"file_path": file_path})],
            stop_reason="tool_use",
            timestamp=timestamp,
        ),
        build_user_record(
            content=[build_tool_result_block()],
            timestamp=timestamp,
        ),
    ]


def _source_prompt(transcript_path: Path) -> str:
    bundle = build_subagent_source_bundle(transcript_path=transcript_path)
    transcript = read_transcript(bundle.transcript_path)
    sidecar = read_sidecar(bundle.sidecar_path)
    return render_prompt(build_spawn_narrative(transcript.records, sidecar=sidecar))


def _source_session_id(transcript_path: Path, claude_root: Path) -> str:
    bundle = build_subagent_source_bundle(transcript_path=transcript_path)
    facts = parse_transcript(bundle, context_cache=SubagentContextCache(claude_root))
    return facts.session.identity.session_id


@pytest.mark.parametrize("raw_session_ids", [("only",), ("a", "b"), ("a", "b", "c", "d")])
def test_unscored_window_spawns_are_scored_and_persist_one_verdict_each(
    tmp_path: Path,
    raw_session_ids: tuple[str, ...],
) -> None:
    scenario = _scenario(tmp_path)
    for raw_session_id in raw_session_ids:
        write_transcript(build_transcript_path(scenario.home, raw_session_id=raw_session_id))
    response = build_judge_response(structured_output=_valid_structured_output())
    assert response.cost_usd is not None
    assert response.input_tokens is not None
    assert response.output_tokens is not None
    judge = FakeJudgeBackend(response=response)

    outcome = scenario.score(judge)

    assert outcome.scored == len(raw_session_ids)
    assert outcome.reused == 0
    assert outcome.skipped == 0
    assert outcome.failed == 0
    assert outcome.unattempted == 0
    assert outcome.stop_reason is None
    assert len(judge.calls) == len(raw_session_ids)
    assert outcome.judge_usage.cost_usd == pytest.approx(len(raw_session_ids) * response.cost_usd)
    assert outcome.judge_usage.input_tokens == len(raw_session_ids) * response.input_tokens
    assert outcome.judge_usage.output_tokens == len(raw_session_ids) * response.output_tokens
    with Store(scenario.store_path, clock=_CLOCK) as store:
        rows = store.read_spawns_in_window(_WINDOW_START, _WINDOW_END, None)
        assert len(rows) == len(raw_session_ids)
        assert all(
            len(store.read_verdicts_for_session(row.identity.session_id)) == 1 for row in rows
        )


@pytest.mark.parametrize(
    ("agent_type", "write_transcript_fixture"),
    [(None, False), ("pathfinder", True)],
)
def test_zero_coverage_window_succeeds_without_a_judge_call(
    tmp_path: Path,
    agent_type: str | None,
    write_transcript_fixture: bool,
) -> None:
    scenario = _scenario(tmp_path, agent_type=agent_type)
    if write_transcript_fixture:
        write_transcript(build_transcript_path(scenario.home), agent_type="implementer")
    judge = FakeJudgeBackend(error=JudgeUnavailableError("must not be called"))

    outcome = scenario.score(judge)

    assert outcome.scored == 0
    assert outcome.reused == 0
    assert outcome.skipped == 0
    assert outcome.failed == 0
    assert outcome.unattempted == 0
    assert outcome.stop_reason is None
    assert judge.calls == []


@pytest.mark.parametrize(
    ("max_run_cost_usd", "responses", "stop_prefix"),
    [
        (
            0.05,
            [build_judge_response(structured_output=_valid_structured_output(), cost_usd=0.05)],
            "Window scoring stopped at cost ceiling",
        ),
        (
            2.00,
            [JudgeUnavailableError("judge did not respond")],
            "Window scoring stopped after",
        ),
    ],
)
def test_run_level_logs_identify_the_window_and_agent_filter(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    max_run_cost_usd: float,
    responses: list[JudgeResponse | JudgeUnavailableError],
    stop_prefix: str,
) -> None:
    scenario = _scenario(tmp_path, agent_type="implementer", max_run_cost_usd=max_run_cost_usd)
    for raw_session_id in ("a", "b", "c"):
        write_transcript(
            build_transcript_path(scenario.home, raw_session_id=raw_session_id),
            agent_type="implementer",
        )
    judge = FakeJudgeBackend(responses=responses)

    with caplog.at_level(logging.INFO, logger="agentlens.core.window_scoring"):
        scenario.score(judge)

    expected_context = (
        f"[{scenario.window.current_start}, {scenario.window.current_end}) "
        f"agent_type={scenario.agent_type}"
    )
    messages = [
        record.message
        for record in caplog.records
        if record.message.startswith(("Window scoring", "Scoring window"))
    ]
    assert any(message.startswith("Window scoring ingest applied") for message in messages)
    assert any(message.startswith("Scoring window") for message in messages)
    assert any(message.startswith(stop_prefix) for message in messages)
    assert any(message.startswith("Window scoring complete") for message in messages)
    assert all(expected_context in message for message in messages)


def test_scoring_the_same_window_twice_reuses_every_verdict_without_a_second_judge_call(
    tmp_path: Path,
) -> None:
    scenario = _scenario(tmp_path)
    paths = [build_transcript_path(scenario.home, raw_session_id=raw_id) for raw_id in ("a", "b")]
    for path in paths:
        write_transcript(path)

    first_judge = FakeJudgeBackend(
        response=build_judge_response(structured_output=_valid_structured_output())
    )
    first_outcome = scenario.score(first_judge)
    second_judge = FakeJudgeBackend(error=JudgeUnavailableError("must not be called"))
    second_outcome = scenario.score(second_judge)

    assert first_outcome.scored == 2
    assert len(first_judge.calls) == 2
    assert second_outcome.scored == 0
    assert second_outcome.reused == 2
    assert second_outcome.skipped == 0
    assert second_outcome.failed == 0
    assert second_outcome.judge_usage.cost_usd == 0.0
    assert second_outcome.judge_usage.input_tokens == 0
    assert second_outcome.judge_usage.output_tokens == 0
    assert second_judge.calls == []
    with Store(scenario.store_path, clock=_CLOCK) as store:
        assert all(
            len(store.read_verdicts_for_session(_source_session_id(path, scenario.claude_root)))
            == 1
            for path in paths
        )


def test_window_mixing_reusable_and_unscored_spawns_sends_only_the_latter_to_the_judge(
    tmp_path: Path,
) -> None:
    scenario = _scenario(tmp_path)
    write_transcript(build_transcript_path(scenario.home, raw_session_id="reused"))
    first_judge = FakeJudgeBackend(
        response=build_judge_response(structured_output=_valid_structured_output())
    )
    scenario.score(first_judge)
    for raw_session_id in ("fresh-one", "fresh-two"):
        write_transcript(build_transcript_path(scenario.home, raw_session_id=raw_session_id))
    second_judge = FakeJudgeBackend(
        response=build_judge_response(structured_output=_valid_structured_output())
    )

    outcome = scenario.score(second_judge)

    assert outcome.scored == 2
    assert outcome.reused == 1
    assert outcome.skipped == 0
    assert outcome.failed == 0
    assert len(second_judge.calls) == 2


def test_a_spawn_outside_the_window_is_neither_scored_nor_counted_even_with_a_verdict(
    tmp_path: Path,
) -> None:
    scenario = _scenario(tmp_path)
    outside_path = build_transcript_path(scenario.home, raw_session_id="outside")
    write_transcript(outside_path, records=_records_at("2025-12-31T00:00:00.000Z"))
    write_transcript(build_transcript_path(scenario.home, raw_session_id="inside"))
    outside_session_id = _source_session_id(outside_path, scenario.claude_root)
    with Store(scenario.store_path, clock=_CLOCK) as store:
        store.upsert_verdict(build_fact_verdict(session_id=outside_session_id))
    judge = FakeJudgeBackend(
        response=build_judge_response(structured_output=_valid_structured_output())
    )

    outcome = scenario.score(judge)

    assert (outcome.scored, outcome.reused, outcome.skipped, outcome.failed) == (1, 0, 0, 0)
    assert len(judge.calls) == 1


def test_counts_sum_to_the_number_of_spawns_covered_across_scored_reused_and_skipped(
    tmp_path: Path,
) -> None:
    scenario = _scenario(tmp_path)
    write_transcript(build_transcript_path(scenario.home, raw_session_id="fresh"))
    reused_path = build_transcript_path(scenario.home, raw_session_id="reused")
    claimed_path = build_transcript_path(scenario.home, raw_session_id="claimed")
    write_transcript(reused_path)
    write_transcript(claimed_path)
    reused_session_id = _source_session_id(reused_path, scenario.claude_root)
    claimed_session_id = _source_session_id(claimed_path, scenario.claude_root)
    with Store(scenario.store_path, clock=_CLOCK) as store:
        store.upsert_verdict(
            build_fact_verdict(
                session_id=reused_session_id,
                judge_input_hash=hash_text(_source_prompt(reused_path)),
                rubric_version=RUBRIC_VERSION,
                judge_model=scenario.request.requested_model,
            )
        )
        store.acquire_verdict_claim(
            build_verdict_claim(
                identity=build_verdict_claim_identity(
                    session_id=claimed_session_id,
                    judge_input_hash=hash_text(_source_prompt(claimed_path)),
                    rubric_version=RUBRIC_VERSION,
                    requested_model=scenario.request.requested_model,
                ),
                owner="other-owner",
                expires_at=_CLOCK.now() + _CLAIM_LEASE,
            )
        )
    judge = FakeJudgeBackend(
        response=build_judge_response(structured_output=_valid_structured_output())
    )

    outcome = scenario.score(judge)

    assert (outcome.scored, outcome.reused, outcome.skipped, outcome.failed) == (1, 1, 1, 0)
    assert outcome.scored + outcome.reused + outcome.skipped + outcome.failed == 3
    assert len(judge.calls) == 1


def _label_from_prompt(prompt: str) -> str:
    for label in ("first", "second", "third"):
        if label in prompt:
            return label
    raise AssertionError(f"no known label found in prompt: {prompt!r}")


def test_spawns_are_scored_oldest_first(tmp_path: Path) -> None:
    scenario = _scenario(tmp_path)
    for raw_session_id, seconds, label in (
        ("a", 30, "third"),
        ("b", 0, "first"),
        ("c", 10, "second"),
    ):
        write_transcript(
            build_transcript_path(scenario.home, raw_session_id=raw_session_id),
            records=_records_at(
                f"2026-01-01T00:00:{seconds:02d}.000Z",
                file_path=f"/workspace/{label}.txt",
            ),
        )
    judge = FakeJudgeBackend(
        response=build_judge_response(structured_output=_valid_structured_output())
    )

    scenario.score(judge)

    assert [_label_from_prompt(prompt) for prompt, _ in judge.calls] == ["first", "second", "third"]


def test_a_spawn_failing_mid_window_does_not_stop_the_spawns_after_it(tmp_path: Path) -> None:
    scenario = _scenario(tmp_path)
    for raw_session_id in ("a", "b", "c"):
        write_transcript(build_transcript_path(scenario.home, raw_session_id=raw_session_id))
    response = build_judge_response(structured_output=_valid_structured_output())
    judge = FakeJudgeBackend(
        responses=[
            JudgeUnavailableError("judge did not respond"),
            JudgeUnavailableError("judge did not respond"),
            JudgeUnavailableError("judge did not respond"),
            response,
        ]
    )

    outcome = scenario.score(judge)

    assert (outcome.scored, outcome.failed, outcome.unattempted, outcome.stop_reason) == (
        2,
        1,
        0,
        None,
    )
    assert len(judge.calls) == 5


def test_a_failed_spawns_deterministic_facts_remain_recorded(tmp_path: Path) -> None:
    scenario = _scenario(tmp_path)
    transcript_path = build_transcript_path(scenario.home, raw_session_id="only")
    write_transcript(transcript_path)
    session_id = _source_session_id(transcript_path, scenario.claude_root)

    outcome = scenario.score(FakeJudgeBackend(error=JudgeUnavailableError("judge did not respond")))

    assert (outcome.failed, outcome.scored) == (1, 0)
    with Store(scenario.store_path, clock=_CLOCK) as store:
        assert store.read_session(session_id) is not None
        assert store.read_verdicts_for_session(session_id) == ()


def test_a_failed_spawn_holds_no_claim_afterward(tmp_path: Path) -> None:
    scenario = _scenario(tmp_path)
    transcript_path = build_transcript_path(scenario.home, raw_session_id="only")
    write_transcript(transcript_path)
    session_id = _source_session_id(transcript_path, scenario.claude_root)

    scenario.score(FakeJudgeBackend(error=JudgeUnavailableError("judge did not respond")))

    identity = build_verdict_claim_identity(
        session_id=session_id,
        judge_input_hash=hash_text(_source_prompt(transcript_path)),
        rubric_version=RUBRIC_VERSION,
        requested_model=scenario.request.requested_model,
    )
    with Store(scenario.store_path, clock=_CLOCK) as store:
        assert store.read_verdict_claim(identity) is None


def test_a_failed_spawn_records_no_verdict_and_is_attempted_again_by_a_later_run(
    tmp_path: Path,
) -> None:
    scenario = _scenario(tmp_path)
    write_transcript(build_transcript_path(scenario.home, raw_session_id="only"))
    first_outcome = scenario.score(
        FakeJudgeBackend(error=JudgeUnavailableError("judge did not respond"))
    )
    succeeding_judge = FakeJudgeBackend(
        response=build_judge_response(structured_output=_valid_structured_output())
    )

    second_outcome = scenario.score(succeeding_judge)

    assert (first_outcome.failed, first_outcome.scored) == (1, 0)
    assert (second_outcome.scored, second_outcome.reused, second_outcome.failed) == (1, 0, 0)
    assert len(succeeding_judge.calls) == 1


def test_a_rejected_verdicts_already_spent_cost_appears_in_the_runs_total(tmp_path: Path) -> None:
    scenario = _scenario(tmp_path)
    write_transcript(build_transcript_path(scenario.home, raw_session_id="only"))
    judge = FakeJudgeBackend(
        response=build_judge_response(
            structured_output=_structured_output_missing_a_dimension(),
            cost_usd=0.05,
            input_tokens=10,
            output_tokens=5,
        )
    )

    outcome = scenario.score(judge)

    assert (outcome.failed, outcome.scored) == (1, 0)
    assert outcome.judge_usage.cost_usd == pytest.approx(0.05)
    assert (outcome.judge_usage.input_tokens, outcome.judge_usage.output_tokens) == (10, 5)
    assert len(judge.calls) == 1


def test_a_window_whose_every_spawn_fails_still_reports_counts_without_raising(
    tmp_path: Path,
) -> None:
    scenario = _scenario(tmp_path)
    for raw_session_id in ("a", "b"):
        write_transcript(build_transcript_path(scenario.home, raw_session_id=raw_session_id))
    judge = FakeJudgeBackend(error=JudgeUnavailableError("judge did not respond"))

    outcome = scenario.score(judge)

    assert (outcome.scored, outcome.reused, outcome.skipped, outcome.failed) == (0, 0, 0, 2)
    assert (outcome.unattempted, outcome.stop_reason, len(judge.calls)) == (0, None, 6)


@pytest.mark.parametrize("unavailable_attempts", [1, 2])
def test_unavailable_attempts_are_retried_until_a_spawn_scores_once(
    tmp_path: Path,
    unavailable_attempts: int,
) -> None:
    scenario = _scenario(tmp_path)
    transcript_path = build_transcript_path(scenario.home, raw_session_id="only")
    write_transcript(transcript_path)
    response = build_judge_response(structured_output=_valid_structured_output())
    judge = FakeJudgeBackend(
        responses=[
            *[JudgeUnavailableError("judge did not respond") for _ in range(unavailable_attempts)],
            response,
        ]
    )

    outcome = scenario.score(judge)

    assert (outcome.scored, outcome.failed, outcome.stop_reason) == (1, 0, None)
    assert len(judge.calls) == unavailable_attempts + 1
    with Store(scenario.store_path, clock=_CLOCK) as store:
        assert (
            len(
                store.read_verdicts_for_session(
                    _source_session_id(transcript_path, scenario.claude_root)
                )
            )
            == 1
        )


def test_exhausting_the_attempt_budget_reports_failed_and_makes_no_further_attempt(
    tmp_path: Path,
) -> None:
    scenario = _scenario(tmp_path)
    write_transcript(build_transcript_path(scenario.home, raw_session_id="only"))
    judge = FakeJudgeBackend(error=JudgeUnavailableError("judge did not respond"))

    outcome = scenario.score(judge)

    assert (outcome.failed, outcome.scored, len(judge.calls)) == (1, 0, 3)


def test_one_spawns_exhausted_attempt_budget_does_not_starve_the_next_spawns_own_budget(
    tmp_path: Path,
) -> None:
    scenario = _scenario(tmp_path)
    for raw_session_id, seconds in (("a", 0), ("b", 10)):
        write_transcript(
            build_transcript_path(scenario.home, raw_session_id=raw_session_id),
            records=_records_at(f"2026-01-01T00:00:{seconds:02d}.000Z"),
        )
    response = build_judge_response(structured_output=_valid_structured_output())
    judge = FakeJudgeBackend(
        responses=[
            JudgeUnavailableError("judge did not respond"),
            JudgeUnavailableError("judge did not respond"),
            JudgeUnavailableError("judge did not respond"),
            JudgeUnavailableError("judge did not respond"),
            response,
        ]
    )

    outcome = scenario.score(judge)

    assert (outcome.failed, outcome.scored, outcome.unattempted, outcome.stop_reason) == (
        1,
        1,
        0,
        None,
    )
    assert len(judge.calls) == 5


def test_an_absent_judge_stops_the_run_at_the_breaker_bound_short_of_the_full_window(
    tmp_path: Path,
) -> None:
    scenario = _scenario(tmp_path)
    for index in range(10):
        write_transcript(build_transcript_path(scenario.home, raw_session_id=f"spawn-{index}"))
    judge = FakeJudgeBackend(error=JudgeUnavailableError("judge could not be found"))

    outcome = scenario.score(judge)

    assert (outcome.scored, outcome.reused, outcome.skipped, outcome.failed) == (0, 0, 0, 3)
    assert (outcome.unattempted, outcome.stop_reason, len(judge.calls)) == (
        7,
        WindowStopReason.JUDGE_UNUSABLE,
        9,
    )


def test_failures_separated_by_a_success_do_not_reach_the_breaker_bound(tmp_path: Path) -> None:
    scenario = _scenario(tmp_path)
    for raw_session_id, seconds in zip(("a", "b", "c", "d"), range(0, 40, 10), strict=True):
        write_transcript(
            build_transcript_path(scenario.home, raw_session_id=raw_session_id),
            records=_records_at(f"2026-01-01T00:00:{seconds:02d}.000Z"),
        )
    response = build_judge_response(structured_output=_valid_structured_output())
    judge = FakeJudgeBackend(
        responses=[
            JudgeUnavailableError("judge did not respond"),
            JudgeUnavailableError("judge did not respond"),
            JudgeUnavailableError("judge did not respond"),
            response,
            JudgeUnavailableError("judge did not respond"),
            JudgeUnavailableError("judge did not respond"),
            JudgeUnavailableError("judge did not respond"),
            response,
        ]
    )

    outcome = scenario.score(judge)

    assert (outcome.scored, outcome.failed, outcome.unattempted, outcome.stop_reason) == (
        2,
        2,
        0,
        None,
    )
    assert len(judge.calls) == 8


def test_a_breaker_stopped_run_keeps_recorded_verdicts_and_reports_counts_and_unattempted(
    tmp_path: Path,
) -> None:
    scenario = _scenario(tmp_path)
    paths = []
    for raw_session_id, seconds in zip(
        ("a", "b", "c", "d", "e", "f"), range(0, 60, 10), strict=True
    ):
        path = build_transcript_path(scenario.home, raw_session_id=raw_session_id)
        write_transcript(path, records=_records_at(f"2026-01-01T00:00:{seconds:02d}.000Z"))
        paths.append(path)
    judge = FakeJudgeBackend(
        responses=[
            build_judge_response(structured_output=_valid_structured_output()),
            build_judge_response(structured_output=_valid_structured_output()),
            JudgeUnavailableError("judge did not respond"),
        ]
    )

    outcome = scenario.score(judge)

    assert (outcome.scored, outcome.failed, outcome.reused, outcome.skipped) == (2, 3, 0, 0)
    assert (outcome.unattempted, outcome.stop_reason) == (1, WindowStopReason.JUDGE_UNUSABLE)
    with Store(scenario.store_path, clock=_CLOCK) as store:
        assert all(
            len(store.read_verdicts_for_session(_source_session_id(path, scenario.claude_root)))
            == 1
            for path in paths[:2]
        )


def test_a_run_reaching_its_ceiling_with_spawns_remaining_stops_and_reports_the_real_spend(
    tmp_path: Path,
) -> None:
    scenario = _scenario(tmp_path, max_run_cost_usd=1.50)
    for raw_session_id in ("a", "b", "c"):
        write_transcript(build_transcript_path(scenario.home, raw_session_id=raw_session_id))
    judge = FakeJudgeBackend(
        response=build_judge_response(structured_output=_valid_structured_output(), cost_usd=1.00)
    )

    outcome = scenario.score(judge)

    assert (outcome.scored, outcome.failed, outcome.unattempted) == (2, 0, 1)
    assert outcome.stop_reason is WindowStopReason.COST_CEILING_REACHED
    assert outcome.judge_usage.cost_usd == pytest.approx(2.00)
    assert len(judge.calls) == 2


def test_verdicts_recorded_before_a_ceiling_stop_are_reused_by_a_later_run(tmp_path: Path) -> None:
    scenario = _scenario(tmp_path, max_run_cost_usd=1.00)
    for raw_session_id in ("a", "b"):
        write_transcript(build_transcript_path(scenario.home, raw_session_id=raw_session_id))
    first_outcome = scenario.score(
        FakeJudgeBackend(
            response=build_judge_response(
                structured_output=_valid_structured_output(), cost_usd=1.00
            )
        )
    )
    second_scenario = _scenario(
        tmp_path,
        request=scenario.request,
        max_run_cost_usd=2.00,
    )
    second_judge = FakeJudgeBackend(
        response=build_judge_response(structured_output=_valid_structured_output(), cost_usd=1.00)
    )

    second_outcome = second_scenario.score(second_judge)

    assert (first_outcome.scored, first_outcome.unattempted, first_outcome.stop_reason) == (
        1,
        1,
        WindowStopReason.COST_CEILING_REACHED,
    )
    assert (second_outcome.reused, second_outcome.scored, second_outcome.stop_reason) == (
        1,
        1,
        None,
    )
    assert len(second_judge.calls) == 1


def test_a_window_of_only_reusable_spawns_runs_to_completion_under_a_small_ceiling(
    tmp_path: Path,
) -> None:
    scenario = _scenario(tmp_path)
    for raw_session_id in ("a", "b"):
        write_transcript(build_transcript_path(scenario.home, raw_session_id=raw_session_id))
    scenario.score(
        FakeJudgeBackend(
            response=build_judge_response(structured_output=_valid_structured_output())
        )
    )
    constrained_scenario = _scenario(
        tmp_path,
        request=scenario.request,
        max_run_cost_usd=0.01,
    )
    judge = FakeJudgeBackend(error=JudgeUnavailableError("must not be called"))

    outcome = constrained_scenario.score(judge)

    assert (outcome.reused, outcome.scored, outcome.unattempted, outcome.stop_reason) == (
        2,
        0,
        0,
        None,
    )
    assert judge.calls == []


def test_a_ceiling_smaller_than_one_calls_own_spend_bound_still_scores_exactly_one_spawn(
    tmp_path: Path,
) -> None:
    scenario = _scenario(tmp_path, max_run_cost_usd=0.01)
    for raw_session_id in ("a", "b"):
        write_transcript(build_transcript_path(scenario.home, raw_session_id=raw_session_id))
    judge = FakeJudgeBackend(
        response=build_judge_response(structured_output=_valid_structured_output(), cost_usd=0.10)
    )

    outcome = scenario.score(judge)

    assert (outcome.scored, outcome.unattempted, outcome.stop_reason) == (
        1,
        1,
        WindowStopReason.COST_CEILING_REACHED,
    )
    assert outcome.judge_usage.cost_usd == pytest.approx(0.10)
    assert len(judge.calls) == 1


def test_the_ceiling_accrual_includes_a_rejected_verdicts_spend(tmp_path: Path) -> None:
    scenario = _scenario(tmp_path, max_run_cost_usd=0.05)
    for raw_session_id in ("a", "b"):
        write_transcript(build_transcript_path(scenario.home, raw_session_id=raw_session_id))
    judge = FakeJudgeBackend(
        response=build_judge_response(
            structured_output=_structured_output_missing_a_dimension(),
            cost_usd=0.05,
        )
    )

    outcome = scenario.score(judge)

    assert (outcome.failed, outcome.scored, outcome.unattempted) == (1, 0, 1)
    assert outcome.stop_reason is WindowStopReason.COST_CEILING_REACHED
    assert outcome.judge_usage.cost_usd == pytest.approx(0.05)
