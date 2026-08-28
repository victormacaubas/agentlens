"""The scoring path through ``analyze_session``: deterministic work first, scoring second."""

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agentlens.core.session import FORMAT_JSON, analyze_session
from agentlens.errors import ConfigError, JudgeResponseError, JudgeUnavailableError
from agentlens.ingest.context import SubagentContextCache
from agentlens.ingest.narrative import build_spawn_narrative
from agentlens.ingest.reading import read_transcript
from agentlens.ingest.sidecar import read_sidecar
from agentlens.ingest.transcript import parse_transcript
from agentlens.judge.prompt import render_prompt
from agentlens.judge.rubric import RUBRIC_VERSION
from agentlens.models.judging import RubricDimension
from agentlens.models.scoring import ScoringRequest
from agentlens.store import Store
from agentlens.utils.hashing import hash_text
from tests.factories import (
    build_fact_verdict,
    build_judge_response,
    build_subagent_source_bundle,
    build_tool_invocation_pair,
    build_transcript_path,
    build_verdict_claim,
    build_verdict_claim_identity,
    build_verdict_identity,
    write_transcript,
)
from tests.fakes import FakeClock, FakeJudgeBackend

_JUDGE_MODEL_ALIAS = "sonnet"
_CLOCK = FakeClock(instant=datetime(2026, 1, 1, tzinfo=UTC))
_CLAIM_LEASE = timedelta(minutes=3)


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
    requested_model: str = _JUDGE_MODEL_ALIAS,
    owner: str = "scorer-one",
) -> ScoringRequest:
    return ScoringRequest(
        requested_model=requested_model,
        owner=owner,
        claim_lease=_CLAIM_LEASE,
    )


def _analyze(
    *,
    transcript_path: Path,
    store_path: Path,
    scoring: ScoringRequest | None,
    judge: FakeJudgeBackend | None,
    dry_run: bool = False,
    output_format: str | None = FORMAT_JSON,
) -> str:
    return analyze_session(
        transcript_path=transcript_path,
        store_path=store_path,
        clock=_CLOCK,
        output_format=output_format,
        dry_run=dry_run,
        claude_root=transcript_path.parents[4],
        scoring=scoring,
        judge=judge,
    )


def _run(
    tmp_path: Path,
    *,
    scoring: ScoringRequest | None,
    judge: FakeJudgeBackend | None = None,
    dry_run: bool = False,
    output_format: str | None = FORMAT_JSON,
) -> tuple[str, Path, Path]:
    transcript_path = build_transcript_path(tmp_path)
    write_transcript(transcript_path)
    store_path = tmp_path / "store" / "agentlens.db"

    output = _analyze(
        transcript_path=transcript_path,
        store_path=store_path,
        scoring=scoring,
        judge=judge,
        dry_run=dry_run,
        output_format=output_format,
    )
    return output, store_path, transcript_path


def _source_prompt(transcript_path: Path) -> str:
    bundle = build_subagent_source_bundle(transcript_path=transcript_path)
    transcript = read_transcript(bundle.transcript_path)
    sidecar = read_sidecar(bundle.sidecar_path)
    return render_prompt(build_spawn_narrative(transcript.records, sidecar=sidecar))


def _source_session_id(transcript_path: Path) -> str:
    bundle = build_subagent_source_bundle(transcript_path=transcript_path)
    facts = parse_transcript(bundle, context_cache=SubagentContextCache(transcript_path.parents[4]))
    return facts.session.identity.session_id


def _session_id_from_store(store_path: Path) -> str:
    with Store(store_path, clock=_CLOCK) as store:
        rows = store.read_spawns_in_window(
            datetime(2000, 1, 1, tzinfo=UTC), datetime(2100, 1, 1, tzinfo=UTC), None
        )
    assert len(rows) == 1
    return rows[0].identity.session_id


def test_scoring_not_requested_makes_no_judge_call_and_matches_pre_scoring_shape(
    tmp_path: Path,
) -> None:
    judge = FakeJudgeBackend(error=JudgeUnavailableError("must not be called"))

    output, store_path, _ = _run(tmp_path, scoring=None, judge=judge)

    assert judge.calls == []
    document = json.loads(output)
    assert document["scoring_status"] == "unscored"
    assert "verdict" not in document["spawns"][0]
    session_id = _session_id_from_store(store_path)
    with Store(store_path, clock=_CLOCK) as store:
        assert store.read_verdicts_for_session(session_id) == ()


def test_scoring_requested_and_succeeds_persists_exactly_one_verdict(tmp_path: Path) -> None:
    response = build_judge_response(structured_output=_valid_structured_output())
    judge = FakeJudgeBackend(response=response)

    output, store_path, _ = _run(tmp_path, scoring=_scoring_request(), judge=judge)

    assert len(judge.calls) == 1
    assert judge.calls[0][1] == _JUDGE_MODEL_ALIAS
    document = json.loads(output)
    assert document["scoring_status"] == "scored"
    assert document["spawns"][0]["verdict"]["judge_model"] == response.resolved_model

    session_id = _session_id_from_store(store_path)
    with Store(store_path, clock=_CLOCK) as store:
        verdicts = store.read_verdicts_for_session(session_id)
    assert len(verdicts) == 1
    assert verdicts[0].judge_model == response.resolved_model
    assert verdicts[0].rubric_version == RUBRIC_VERSION


def test_rescoring_the_same_unchanged_spawn_reuses_the_stored_verdict(tmp_path: Path) -> None:
    response = build_judge_response(structured_output=_valid_structured_output())
    first_judge = FakeJudgeBackend(response=response)
    second_judge = FakeJudgeBackend(error=JudgeUnavailableError("must not be called"))
    scoring = _scoring_request(requested_model=response.resolved_model)

    _, store_path, _ = _run(tmp_path, scoring=scoring, judge=first_judge)
    output, store_path, _ = _run(tmp_path, scoring=scoring, judge=second_judge)

    session_id = _session_id_from_store(store_path)
    with Store(store_path, clock=_CLOCK) as store:
        verdicts = store.read_verdicts_for_session(session_id)
    assert len(verdicts) == 1
    assert len(first_judge.calls) == 1
    assert second_judge.calls == []
    document = json.loads(output)
    assert document["scoring_status"] == "reused"
    assert document["spawns"][0]["is_reused"] is True
    assert document["spawns"][0]["verdict"]["judge_model"] == response.resolved_model


@pytest.mark.parametrize(
    "miss_kind",
    ("changed_input", "bumped_rubric", "different_model", "requested_alias"),
)
def test_nonmatching_verdict_identity_calls_the_judge(
    tmp_path: Path,
    miss_kind: str,
) -> None:
    _, store_path, transcript_path = _run(tmp_path, scoring=None)
    session_id = _source_session_id(transcript_path)
    original_hash = hash_text(_source_prompt(transcript_path))
    stored_hash = original_hash
    stored_rubric = RUBRIC_VERSION
    stored_model = "claude-sonnet-5"
    requested_model = stored_model

    if miss_kind == "changed_input":
        write_transcript(
            transcript_path,
            records=build_tool_invocation_pair(tool_input={"file_path": "/workspace/changed.txt"}),
        )
    elif miss_kind == "bumped_rubric":
        stored_rubric = "previous-rubric"
    elif miss_kind == "different_model":
        stored_model = "claude-opus-5"
    else:
        requested_model = _JUDGE_MODEL_ALIAS

    with Store(store_path, clock=_CLOCK) as store:
        store.upsert_verdict(
            build_fact_verdict(
                session_id=session_id,
                judge_input_hash=stored_hash,
                rubric_version=stored_rubric,
                judge_model=stored_model,
            )
        )

    response = build_judge_response(
        resolved_model="claude-sonnet-5",
        structured_output=_valid_structured_output(),
    )
    judge = FakeJudgeBackend(response=response)

    _analyze(
        transcript_path=transcript_path,
        store_path=store_path,
        scoring=_scoring_request(requested_model=requested_model),
        judge=judge,
    )

    assert len(judge.calls) == 1


def test_dryrun_with_scoring_requested_makes_no_call_and_writes_nothing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    judge = FakeJudgeBackend(error=JudgeUnavailableError("must not be called"))

    with caplog.at_level(logging.INFO, logger="agentlens"):
        _, store_path, _ = _run(
            tmp_path,
            scoring=_scoring_request(),
            judge=judge,
            dry_run=True,
        )

    assert judge.calls == []
    assert not store_path.exists()
    dry_run_messages = [
        record.message for record in caplog.records if "would score" in record.message
    ]
    assert len(dry_run_messages) == 1
    message = dry_run_messages[0]
    assert _JUDGE_MODEL_ALIAS in message
    assert RUBRIC_VERSION in message


def test_dryrun_with_a_stored_verdict_reports_a_zero_cost_reuse(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _, store_path, transcript_path = _run(tmp_path, scoring=None)
    session_id = _source_session_id(transcript_path)
    identity = build_verdict_identity(
        session_id=session_id,
        judge_input_hash=hash_text(_source_prompt(transcript_path)),
        rubric_version=RUBRIC_VERSION,
        judge_model="claude-sonnet-5",
    )
    stored_verdict = build_fact_verdict(
        session_id=identity.session_id,
        judge_input_hash=identity.judge_input_hash,
        rubric_version=identity.rubric_version,
        judge_model=identity.judge_model,
    )
    claim_identity = build_verdict_claim_identity(
        session_id=identity.session_id,
        judge_input_hash=identity.judge_input_hash,
        rubric_version=identity.rubric_version,
        requested_model=identity.judge_model,
    )
    with Store(store_path, clock=_CLOCK) as store:
        store.upsert_verdict(stored_verdict)

    judge = FakeJudgeBackend(error=JudgeUnavailableError("must not be called"))
    with caplog.at_level(logging.INFO, logger="agentlens"):
        _analyze(
            transcript_path=transcript_path,
            store_path=store_path,
            scoring=_scoring_request(requested_model=identity.judge_model),
            judge=judge,
            dry_run=True,
        )

    assert judge.calls == []
    assert any(
        "would reuse" in record.message and "zero cost" in record.message
        for record in caplog.records
    )
    with Store(store_path, clock=_CLOCK) as store:
        assert store.read_verdict(identity) == stored_verdict
        assert store.read_verdict_claim(claim_identity) is None


def test_dryrun_for_an_unscored_identity_leaves_no_claim_or_verdict(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    transcript_path = build_transcript_path(tmp_path)
    write_transcript(transcript_path)
    store_path = tmp_path / "store" / "agentlens.db"
    identity = build_verdict_identity(
        session_id=_source_session_id(transcript_path),
        judge_input_hash=hash_text(_source_prompt(transcript_path)),
        rubric_version=RUBRIC_VERSION,
        judge_model="claude-sonnet-5",
    )
    claim_identity = build_verdict_claim_identity(
        session_id=identity.session_id,
        judge_input_hash=identity.judge_input_hash,
        rubric_version=identity.rubric_version,
        requested_model=identity.judge_model,
    )
    with Store(store_path, clock=_CLOCK):
        pass

    dry_run_judge = FakeJudgeBackend(error=JudgeUnavailableError("must not be called"))
    with caplog.at_level(logging.INFO, logger="agentlens"):
        _analyze(
            transcript_path=transcript_path,
            store_path=store_path,
            scoring=_scoring_request(requested_model=identity.judge_model),
            judge=dry_run_judge,
            dry_run=True,
        )

    assert dry_run_judge.calls == []
    assert any(
        "would score" in record.message and identity.judge_input_hash in record.message
        for record in caplog.records
    )
    with Store(store_path, clock=_CLOCK) as store:
        assert store.read_verdict(identity) is None
        assert store.read_verdict_claim(claim_identity) is None

    response = build_judge_response(
        resolved_model=identity.judge_model,
        structured_output=_valid_structured_output(),
    )
    real_judge = FakeJudgeBackend(response=response)
    _analyze(
        transcript_path=transcript_path,
        store_path=store_path,
        scoring=_scoring_request(requested_model=identity.judge_model),
        judge=real_judge,
    )

    assert len(real_judge.calls) == 1


def test_live_claim_held_elsewhere_skips_without_calling_the_judge(tmp_path: Path) -> None:
    _, store_path, transcript_path = _run(tmp_path, scoring=None)
    identity = build_verdict_identity(
        session_id=_source_session_id(transcript_path),
        judge_input_hash=hash_text(_source_prompt(transcript_path)),
        rubric_version=RUBRIC_VERSION,
        judge_model="claude-sonnet-5",
    )
    claim_identity = build_verdict_claim_identity(
        session_id=identity.session_id,
        judge_input_hash=identity.judge_input_hash,
        rubric_version=identity.rubric_version,
        requested_model=identity.judge_model,
    )
    claim = build_verdict_claim(
        identity=claim_identity,
        owner="other-scorer",
        expires_at=_CLOCK.now() + _CLAIM_LEASE,
    )
    with Store(store_path, clock=_CLOCK) as store:
        store.acquire_verdict_claim(claim)

    judge = FakeJudgeBackend(error=JudgeUnavailableError("must not be called"))
    output = _analyze(
        transcript_path=transcript_path,
        store_path=store_path,
        scoring=_scoring_request(requested_model=claim_identity.requested_model),
        judge=judge,
    )

    assert judge.calls == []
    document = json.loads(output)
    assert document["scoring_status"] == "claimed_elsewhere"
    assert "verdict" not in document["spawns"][0]
    with Store(store_path, clock=_CLOCK) as store:
        assert store.read_verdict(identity) is None
        assert store.read_verdict_claim(claim_identity) == claim


def test_expired_claim_does_not_block_scoring(tmp_path: Path) -> None:
    _, store_path, transcript_path = _run(tmp_path, scoring=None)
    identity = build_verdict_identity(
        session_id=_source_session_id(transcript_path),
        judge_input_hash=hash_text(_source_prompt(transcript_path)),
        rubric_version=RUBRIC_VERSION,
        judge_model="claude-sonnet-5",
    )
    claim_identity = build_verdict_claim_identity(
        session_id=identity.session_id,
        judge_input_hash=identity.judge_input_hash,
        rubric_version=identity.rubric_version,
        requested_model=identity.judge_model,
    )
    expired_claim = build_verdict_claim(
        identity=claim_identity,
        owner="crashed-scorer",
        expires_at=_CLOCK.now() - timedelta(seconds=1),
    )
    with Store(store_path, clock=_CLOCK) as store:
        store.acquire_verdict_claim(expired_claim)

    response = build_judge_response(
        resolved_model=identity.judge_model,
        structured_output=_valid_structured_output(),
    )
    judge = FakeJudgeBackend(response=response)
    _analyze(
        transcript_path=transcript_path,
        store_path=store_path,
        scoring=_scoring_request(requested_model=identity.judge_model),
        judge=judge,
    )

    assert len(judge.calls) == 1
    with Store(store_path, clock=_CLOCK) as store:
        assert store.read_verdict(identity) is not None
        assert store.read_verdict_claim(claim_identity) is None


def test_store_is_writable_while_the_fake_judge_call_is_in_flight(tmp_path: Path) -> None:
    _, store_path, transcript_path = _run(tmp_path, scoring=None)
    writes: list[str] = []

    def write_unrelated_verdict() -> None:
        with Store(store_path, clock=_CLOCK) as store:
            store.upsert_verdict(build_fact_verdict(session_id="unrelated-session"))
        writes.append("completed")

    response = build_judge_response(
        resolved_model="claude-sonnet-5",
        structured_output=_valid_structured_output(),
    )
    judge = FakeJudgeBackend(response=response, on_score=write_unrelated_verdict)

    _analyze(
        transcript_path=transcript_path,
        store_path=store_path,
        scoring=_scoring_request(requested_model=response.resolved_model),
        judge=judge,
    )

    assert writes == ["completed"]


def test_input_changed_during_the_judge_call_is_stored_under_the_original_hash(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _, store_path, transcript_path = _run(tmp_path, scoring=None)

    def change_judge_input() -> None:
        write_transcript(
            transcript_path,
            records=build_tool_invocation_pair(tool_input={"file_path": "/workspace/changed.txt"}),
        )

    response = build_judge_response(
        resolved_model="claude-sonnet-5",
        structured_output=_valid_structured_output(),
    )
    judge = FakeJudgeBackend(response=response, on_score=change_judge_input)
    with caplog.at_level(logging.WARNING, logger="agentlens"):
        output = _analyze(
            transcript_path=transcript_path,
            store_path=store_path,
            scoring=_scoring_request(requested_model=response.resolved_model),
            judge=judge,
        )

    session_id = _source_session_id(transcript_path)
    original_hash = hash_text(judge.calls[0][0])
    current_hash = hash_text(_source_prompt(transcript_path))
    original_identity = build_verdict_identity(
        session_id=session_id,
        judge_input_hash=original_hash,
        rubric_version=RUBRIC_VERSION,
        judge_model=response.resolved_model,
    )
    original_claim_identity = build_verdict_claim_identity(
        session_id=original_identity.session_id,
        judge_input_hash=original_identity.judge_input_hash,
        rubric_version=original_identity.rubric_version,
        requested_model=response.resolved_model,
    )
    assert original_hash != current_hash
    assert any("behind current input" in record.message for record in caplog.records)
    document = json.loads(output)
    assert document["scoring_status"] == "scored"
    assert document["spawns"][0]["is_behind_current_input"] is True
    with Store(store_path, clock=_CLOCK) as store:
        assert store.read_verdict(original_identity) is not None
        assert store.read_verdict_claim(original_claim_identity) is None


def test_judge_call_failure_leaves_deterministic_facts_stored_and_propagates(
    tmp_path: Path,
) -> None:
    judge = FakeJudgeBackend(error=JudgeUnavailableError("judge is not authenticated"))
    transcript_path = build_transcript_path(tmp_path)
    write_transcript(transcript_path)
    store_path = tmp_path / "store" / "agentlens.db"

    with pytest.raises(JudgeUnavailableError):
        analyze_session(
            transcript_path=transcript_path,
            store_path=store_path,
            clock=_CLOCK,
            output_format=FORMAT_JSON,
            dry_run=False,
            claude_root=tmp_path / ".claude",
            scoring=_scoring_request(),
            judge=judge,
        )

    session_id = _session_id_from_store(store_path)
    identity = build_verdict_claim_identity(
        session_id=session_id,
        judge_input_hash=hash_text(judge.calls[0][0]),
        rubric_version=RUBRIC_VERSION,
        requested_model=_JUDGE_MODEL_ALIAS,
    )
    with Store(store_path, clock=_CLOCK) as store:
        assert store.read_session(session_id) is not None
        assert store.read_verdicts_for_session(session_id) == ()
        assert store.read_verdict_claim(identity) is None


def test_rejected_verdict_logs_the_cost_already_spent_and_propagates_without_persisting(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    response = build_judge_response(
        structured_output=_structured_output_missing_a_dimension(),
        cost_usd=0.05,
        input_tokens=10,
        output_tokens=5,
    )
    judge = FakeJudgeBackend(response=response)
    transcript_path = build_transcript_path(tmp_path)
    write_transcript(transcript_path)
    store_path = tmp_path / "store" / "agentlens.db"

    with (
        caplog.at_level(logging.ERROR, logger="agentlens"),
        pytest.raises(JudgeResponseError),
    ):
        analyze_session(
            transcript_path=transcript_path,
            store_path=store_path,
            clock=_CLOCK,
            output_format=FORMAT_JSON,
            dry_run=False,
            claude_root=tmp_path / ".claude",
            scoring=_scoring_request(),
            judge=judge,
        )

    error_messages = [
        record.message for record in caplog.records if record.levelno == logging.ERROR
    ]
    assert len(error_messages) == 1
    assert "0.05" in error_messages[0]

    session_id = _session_id_from_store(store_path)
    identity = build_verdict_claim_identity(
        session_id=session_id,
        judge_input_hash=hash_text(judge.calls[0][0]),
        rubric_version=RUBRIC_VERSION,
        requested_model=_JUDGE_MODEL_ALIAS,
    )
    with Store(store_path, clock=_CLOCK) as store:
        assert store.read_session(session_id) is not None
        assert store.read_verdicts_for_session(session_id) == ()
        assert store.read_verdict_claim(identity) is None


def test_response_with_no_cost_raises_and_persists_no_verdict(tmp_path: Path) -> None:
    response = build_judge_response(structured_output=_valid_structured_output(), cost_usd=None)
    judge = FakeJudgeBackend(response=response)
    transcript_path = build_transcript_path(tmp_path)
    write_transcript(transcript_path)
    store_path = tmp_path / "store" / "agentlens.db"

    with pytest.raises(JudgeResponseError):
        analyze_session(
            transcript_path=transcript_path,
            store_path=store_path,
            clock=_CLOCK,
            output_format=FORMAT_JSON,
            dry_run=False,
            claude_root=tmp_path / ".claude",
            scoring=_scoring_request(),
            judge=judge,
        )

    session_id = _session_id_from_store(store_path)
    with Store(store_path, clock=_CLOCK) as store:
        assert store.read_verdicts_for_session(session_id) == ()


def test_response_missing_token_counts_persists_a_verdict_with_zero_tokens(
    tmp_path: Path,
) -> None:
    response = build_judge_response(
        structured_output=_valid_structured_output(),
        cost_usd=0.03,
        input_tokens=None,
        output_tokens=None,
    )
    judge = FakeJudgeBackend(response=response)

    _, store_path, _ = _run(tmp_path, scoring=_scoring_request(), judge=judge)

    session_id = _session_id_from_store(store_path)
    with Store(store_path, clock=_CLOCK) as store:
        verdicts = store.read_verdicts_for_session(session_id)
    assert len(verdicts) == 1
    assert verdicts[0].judge_cost_usd == 0.03
    assert verdicts[0].judge_input_tokens == 0
    assert verdicts[0].judge_output_tokens == 0


def test_scoring_without_a_judge_raises_a_config_error(tmp_path: Path) -> None:
    transcript_path = build_transcript_path(tmp_path)
    write_transcript(transcript_path)
    store_path = tmp_path / "store" / "agentlens.db"

    with pytest.raises(ConfigError):
        analyze_session(
            transcript_path=transcript_path,
            store_path=store_path,
            clock=_CLOCK,
            output_format=FORMAT_JSON,
            dry_run=False,
            claude_root=tmp_path / ".claude",
            scoring=_scoring_request(),
            judge=None,
        )
