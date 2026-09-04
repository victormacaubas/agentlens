"""Scoring every qualifying spawn in a resolved window in one pass."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agentlens.core.window_scoring import score_window
from agentlens.errors import JudgeUnavailableError
from agentlens.ingest.context import SubagentContextCache
from agentlens.ingest.narrative import build_spawn_narrative
from agentlens.ingest.reading import read_transcript
from agentlens.ingest.sidecar import read_sidecar
from agentlens.ingest.transcript import parse_transcript
from agentlens.judge.prompt import render_prompt
from agentlens.judge.rubric import RUBRIC_VERSION
from agentlens.models.judging import RubricDimension
from agentlens.models.scoring import ScoringRequest
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


def _valid_structured_output() -> dict[str, object]:
    return {
        "overall_score": 4,
        "dimensions": {
            dimension.value: {"score": 4, "evidence": ["Evidence."]}
            for dimension in RubricDimension
        },
        "suggested_fixes": [],
    }


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


def test_a_window_of_unscored_spawns_is_scored_and_persists_one_verdict_each(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    claude_root = home / ".claude"
    store_path = tmp_path / "store" / "agentlens.db"
    write_transcript(build_transcript_path(home, raw_session_id="a"))
    write_transcript(build_transcript_path(home, raw_session_id="b"))
    response = build_judge_response(structured_output=_valid_structured_output())
    assert response.cost_usd is not None
    assert response.input_tokens is not None
    assert response.output_tokens is not None
    judge = FakeJudgeBackend(response=response)

    outcome = score_window(
        projects_root=claude_root / "projects",
        claude_root=claude_root,
        store_path=store_path,
        clock=_CLOCK,
        judge=judge,
        request=_scoring_request(),
        agent_type=None,
        window=_window(),
    )

    assert outcome.scored == 2
    assert outcome.reused == 0
    assert outcome.skipped == 0
    assert outcome.failed == 0
    assert outcome.unattempted == 0
    assert outcome.stop_reason is None
    assert len(judge.calls) == 2
    assert outcome.judge_usage.cost_usd == pytest.approx(2 * response.cost_usd)
    assert outcome.judge_usage.input_tokens == 2 * response.input_tokens
    assert outcome.judge_usage.output_tokens == 2 * response.output_tokens
    with Store(store_path, clock=_CLOCK) as store:
        rows = store.read_spawns_in_window(_WINDOW_START, _WINDOW_END, None)
        assert len(rows) == 2
        for row in rows:
            assert len(store.read_verdicts_for_session(row.identity.session_id)) == 1


def test_a_single_spawn_window_is_scored(tmp_path: Path) -> None:
    home = tmp_path / "home"
    claude_root = home / ".claude"
    store_path = tmp_path / "store" / "agentlens.db"
    write_transcript(build_transcript_path(home, raw_session_id="only"))
    response = build_judge_response(structured_output=_valid_structured_output())
    judge = FakeJudgeBackend(response=response)

    outcome = score_window(
        projects_root=claude_root / "projects",
        claude_root=claude_root,
        store_path=store_path,
        clock=_CLOCK,
        judge=judge,
        request=_scoring_request(),
        agent_type=None,
        window=_window(),
    )

    assert outcome.scored == 1
    assert outcome.reused == 0
    assert outcome.skipped == 0
    assert outcome.failed == 0
    assert outcome.unattempted == 0
    assert outcome.stop_reason is None
    assert len(judge.calls) == 1


def test_an_empty_window_succeeds_with_nothing_covered_and_no_judge_call(tmp_path: Path) -> None:
    claude_root = tmp_path / "home" / ".claude"
    store_path = tmp_path / "store" / "agentlens.db"
    judge = FakeJudgeBackend(error=JudgeUnavailableError("must not be called"))

    outcome = score_window(
        projects_root=claude_root / "projects",
        claude_root=claude_root,
        store_path=store_path,
        clock=_CLOCK,
        judge=judge,
        request=_scoring_request(),
        agent_type=None,
        window=_window(),
    )

    assert outcome.scored == 0
    assert outcome.reused == 0
    assert outcome.skipped == 0
    assert outcome.failed == 0
    assert outcome.unattempted == 0
    assert outcome.stop_reason is None
    assert judge.calls == []


def test_an_agent_filter_matching_nothing_succeeds_with_nothing_covered(tmp_path: Path) -> None:
    home = tmp_path / "home"
    claude_root = home / ".claude"
    store_path = tmp_path / "store" / "agentlens.db"
    write_transcript(build_transcript_path(home, raw_session_id="only"), agent_type="implementer")
    judge = FakeJudgeBackend(error=JudgeUnavailableError("must not be called"))

    outcome = score_window(
        projects_root=claude_root / "projects",
        claude_root=claude_root,
        store_path=store_path,
        clock=_CLOCK,
        judge=judge,
        request=_scoring_request(),
        agent_type="pathfinder",
        window=_window(),
    )

    assert outcome.scored == 0
    assert outcome.reused == 0
    assert outcome.skipped == 0
    assert outcome.failed == 0
    assert judge.calls == []


def test_scoring_the_same_window_twice_reuses_every_verdict_without_a_second_judge_call(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    claude_root = home / ".claude"
    store_path = tmp_path / "store" / "agentlens.db"
    paths = [build_transcript_path(home, raw_session_id=raw_id) for raw_id in ("a", "b")]
    for path in paths:
        write_transcript(path)
    request = _scoring_request()

    first_response = build_judge_response(structured_output=_valid_structured_output())
    first_judge = FakeJudgeBackend(response=first_response)
    first_outcome = score_window(
        projects_root=claude_root / "projects",
        claude_root=claude_root,
        store_path=store_path,
        clock=_CLOCK,
        judge=first_judge,
        request=request,
        agent_type=None,
        window=_window(),
    )
    assert first_outcome.scored == 2
    assert len(first_judge.calls) == 2

    second_judge = FakeJudgeBackend(error=JudgeUnavailableError("must not be called"))
    second_outcome = score_window(
        projects_root=claude_root / "projects",
        claude_root=claude_root,
        store_path=store_path,
        clock=_CLOCK,
        judge=second_judge,
        request=request,
        agent_type=None,
        window=_window(),
    )

    assert second_outcome.scored == 0
    assert second_outcome.reused == 2
    assert second_outcome.skipped == 0
    assert second_outcome.failed == 0
    assert second_outcome.judge_usage.cost_usd == 0.0
    assert second_outcome.judge_usage.input_tokens == 0
    assert second_outcome.judge_usage.output_tokens == 0
    assert second_judge.calls == []
    for path in paths:
        session_id = _source_session_id(path, claude_root)
        with Store(store_path, clock=_CLOCK) as store:
            assert len(store.read_verdicts_for_session(session_id)) == 1


def test_a_window_mixing_reusable_and_unscored_spawns_sends_only_the_latter_to_the_judge(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    claude_root = home / ".claude"
    store_path = tmp_path / "store" / "agentlens.db"
    write_transcript(build_transcript_path(home, raw_session_id="reused"))
    request = _scoring_request()

    first_response = build_judge_response(structured_output=_valid_structured_output())
    first_judge = FakeJudgeBackend(response=first_response)
    score_window(
        projects_root=claude_root / "projects",
        claude_root=claude_root,
        store_path=store_path,
        clock=_CLOCK,
        judge=first_judge,
        request=request,
        agent_type=None,
        window=_window(),
    )
    assert len(first_judge.calls) == 1

    write_transcript(build_transcript_path(home, raw_session_id="fresh-one"))
    write_transcript(build_transcript_path(home, raw_session_id="fresh-two"))

    second_response = build_judge_response(structured_output=_valid_structured_output())
    second_judge = FakeJudgeBackend(response=second_response)
    outcome = score_window(
        projects_root=claude_root / "projects",
        claude_root=claude_root,
        store_path=store_path,
        clock=_CLOCK,
        judge=second_judge,
        request=request,
        agent_type=None,
        window=_window(),
    )

    assert outcome.scored == 2
    assert outcome.reused == 1
    assert outcome.skipped == 0
    assert outcome.failed == 0
    assert len(second_judge.calls) == 2


def test_four_spawns_in_one_parent_session_are_counted_as_four_not_one(tmp_path: Path) -> None:
    home = tmp_path / "home"
    claude_root = home / ".claude"
    store_path = tmp_path / "store" / "agentlens.db"
    for raw_session_id in ("a", "b", "c", "d"):
        write_transcript(build_transcript_path(home, raw_session_id=raw_session_id))
    response = build_judge_response(structured_output=_valid_structured_output())
    judge = FakeJudgeBackend(response=response)

    outcome = score_window(
        projects_root=claude_root / "projects",
        claude_root=claude_root,
        store_path=store_path,
        clock=_CLOCK,
        judge=judge,
        request=_scoring_request(),
        agent_type=None,
        window=_window(),
    )

    assert outcome.scored == 4
    assert len(judge.calls) == 4


def test_a_spawn_outside_the_window_is_neither_scored_nor_counted_even_with_a_verdict(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    claude_root = home / ".claude"
    store_path = tmp_path / "store" / "agentlens.db"
    outside_path = build_transcript_path(home, raw_session_id="outside")
    write_transcript(outside_path, records=_records_at("2025-12-31T00:00:00.000Z"))
    write_transcript(build_transcript_path(home, raw_session_id="inside"))

    outside_session_id = _source_session_id(outside_path, claude_root)
    with Store(store_path, clock=_CLOCK) as store:
        store.upsert_verdict(build_fact_verdict(session_id=outside_session_id))

    response = build_judge_response(structured_output=_valid_structured_output())
    judge = FakeJudgeBackend(response=response)

    outcome = score_window(
        projects_root=claude_root / "projects",
        claude_root=claude_root,
        store_path=store_path,
        clock=_CLOCK,
        judge=judge,
        request=_scoring_request(),
        agent_type=None,
        window=_window(),
    )

    assert outcome.scored == 1
    assert outcome.reused == 0
    assert outcome.skipped == 0
    assert outcome.failed == 0
    assert len(judge.calls) == 1


def test_counts_sum_to_the_number_of_spawns_covered_across_scored_reused_and_skipped(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    claude_root = home / ".claude"
    store_path = tmp_path / "store" / "agentlens.db"
    request = _scoring_request()

    write_transcript(build_transcript_path(home, raw_session_id="fresh"))
    reused_path = build_transcript_path(home, raw_session_id="reused")
    write_transcript(reused_path)
    claimed_path = build_transcript_path(home, raw_session_id="claimed")
    write_transcript(claimed_path)

    reused_session_id = _source_session_id(reused_path, claude_root)
    reused_hash = hash_text(_source_prompt(reused_path))
    with Store(store_path, clock=_CLOCK) as store:
        store.upsert_verdict(
            build_fact_verdict(
                session_id=reused_session_id,
                judge_input_hash=reused_hash,
                rubric_version=RUBRIC_VERSION,
                judge_model=request.requested_model,
            )
        )

    claimed_session_id = _source_session_id(claimed_path, claude_root)
    claimed_hash = hash_text(_source_prompt(claimed_path))
    claim_identity = build_verdict_claim_identity(
        session_id=claimed_session_id,
        judge_input_hash=claimed_hash,
        rubric_version=RUBRIC_VERSION,
        requested_model=request.requested_model,
    )
    with Store(store_path, clock=_CLOCK) as store:
        store.acquire_verdict_claim(
            build_verdict_claim(
                identity=claim_identity,
                owner="other-owner",
                expires_at=_CLOCK.now() + _CLAIM_LEASE,
            )
        )

    response = build_judge_response(structured_output=_valid_structured_output())
    judge = FakeJudgeBackend(response=response)

    outcome = score_window(
        projects_root=claude_root / "projects",
        claude_root=claude_root,
        store_path=store_path,
        clock=_CLOCK,
        judge=judge,
        request=request,
        agent_type=None,
        window=_window(),
    )

    assert outcome.scored == 1
    assert outcome.reused == 1
    assert outcome.skipped == 1
    assert outcome.failed == 0
    assert outcome.scored + outcome.reused + outcome.skipped + outcome.failed == 3
    assert len(judge.calls) == 1


def _label_from_prompt(prompt: str) -> str:
    for label in ("first", "second", "third"):
        if label in prompt:
            return label
    raise AssertionError(f"no known label found in prompt: {prompt!r}")


def test_spawns_are_scored_oldest_first(tmp_path: Path) -> None:
    """Discovery order (alphabetical by raw id) differs from chronological order.

    Raw id ``a`` is discovered first but started last; ``b`` is discovered
    second but started first. Relying on discovery or insertion order instead
    of an explicit sort on ``started_at`` would score them out of order.
    """
    home = tmp_path / "home"
    claude_root = home / ".claude"
    store_path = tmp_path / "store" / "agentlens.db"
    write_transcript(
        build_transcript_path(home, raw_session_id="a"),
        records=_records_at("2026-01-01T00:00:30.000Z", file_path="/workspace/third.txt"),
    )
    write_transcript(
        build_transcript_path(home, raw_session_id="b"),
        records=_records_at("2026-01-01T00:00:00.000Z", file_path="/workspace/first.txt"),
    )
    write_transcript(
        build_transcript_path(home, raw_session_id="c"),
        records=_records_at("2026-01-01T00:00:10.000Z", file_path="/workspace/second.txt"),
    )
    response = build_judge_response(structured_output=_valid_structured_output())
    judge = FakeJudgeBackend(response=response)

    score_window(
        projects_root=claude_root / "projects",
        claude_root=claude_root,
        store_path=store_path,
        clock=_CLOCK,
        judge=judge,
        request=_scoring_request(),
        agent_type=None,
        window=_window(),
    )

    order = [_label_from_prompt(prompt) for prompt, _ in judge.calls]
    assert order == ["first", "second", "third"]
