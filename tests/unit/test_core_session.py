"""The scoring path through ``analyze_session``: deterministic work first, scoring second."""

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentlens.core.session import FORMAT_JSON, analyze_session
from agentlens.errors import ConfigError, JudgeResponseError, JudgeUnavailableError
from agentlens.judge.rubric import RUBRIC_VERSION
from agentlens.models.judging import RubricDimension
from agentlens.store import Store
from tests.factories import (
    build_judge_response,
    build_sidecar,
    build_tool_invocation_pair,
    build_transcript_path,
    build_transcript_text,
)
from tests.fakes import FakeClock, FakeJudgeBackend

_JUDGE_MODEL_ALIAS = "sonnet"
_CLOCK = FakeClock(instant=datetime(2026, 1, 1, tzinfo=UTC))


def _write_transcript(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_transcript_text(build_tool_invocation_pair()))
    path.with_suffix(".meta.json").write_text(json.dumps(build_sidecar()))


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


def _run(
    tmp_path: Path,
    *,
    score: bool,
    judge: FakeJudgeBackend | None = None,
    dry_run: bool = False,
    output_format: str | None = FORMAT_JSON,
) -> tuple[str, Path, str]:
    transcript_path = build_transcript_path(tmp_path)
    _write_transcript(transcript_path)
    store_path = tmp_path / "store" / "agentlens.db"

    output = analyze_session(
        transcript_path=transcript_path,
        store_path=store_path,
        clock=_CLOCK,
        output_format=output_format,
        dry_run=dry_run,
        claude_root=tmp_path / ".claude",
        score=score,
        judge=judge,
        judge_model=_JUDGE_MODEL_ALIAS if score else None,
    )
    return output, store_path, transcript_path.name


def _session_id_from_store(store_path: Path) -> str:
    with Store(store_path) as store:
        rows = store.read_spawns_in_window(
            datetime(2000, 1, 1, tzinfo=UTC), datetime(2100, 1, 1, tzinfo=UTC), None
        )
    assert len(rows) == 1
    return rows[0].identity.session_id


def test_scoring_not_requested_makes_no_judge_call_and_matches_pre_scoring_shape(
    tmp_path: Path,
) -> None:
    judge = FakeJudgeBackend(error=JudgeUnavailableError("must not be called"))

    output, store_path, _ = _run(tmp_path, score=False, judge=judge)

    assert judge.calls == []
    document = json.loads(output)
    assert document["scoring_status"] == "unscored"
    assert "verdict" not in document["spawns"][0]
    session_id = _session_id_from_store(store_path)
    with Store(store_path) as store:
        assert store.read_verdicts_for_session(session_id) == ()


def test_scoring_requested_and_succeeds_persists_exactly_one_verdict(tmp_path: Path) -> None:
    response = build_judge_response(structured_output=_valid_structured_output())
    judge = FakeJudgeBackend(response=response)

    output, store_path, _ = _run(tmp_path, score=True, judge=judge)

    assert len(judge.calls) == 1
    assert judge.calls[0][1] == _JUDGE_MODEL_ALIAS
    document = json.loads(output)
    assert document["scoring_status"] == "scored"
    assert document["spawns"][0]["verdict"]["judge_model"] == response.resolved_model

    session_id = _session_id_from_store(store_path)
    with Store(store_path) as store:
        verdicts = store.read_verdicts_for_session(session_id)
    assert len(verdicts) == 1
    assert verdicts[0].judge_model == response.resolved_model
    assert verdicts[0].rubric_version == RUBRIC_VERSION


def test_rescoring_the_same_unchanged_spawn_replaces_the_stored_row(tmp_path: Path) -> None:
    response = build_judge_response(structured_output=_valid_structured_output())
    first_judge = FakeJudgeBackend(response=response)
    second_judge = FakeJudgeBackend(response=response)

    _, store_path, _ = _run(tmp_path, score=True, judge=first_judge)
    _, store_path, _ = _run(tmp_path, score=True, judge=second_judge)

    session_id = _session_id_from_store(store_path)
    with Store(store_path) as store:
        verdicts = store.read_verdicts_for_session(session_id)
    assert len(verdicts) == 1


def test_dryrun_with_scoring_requested_makes_no_call_and_writes_nothing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    judge = FakeJudgeBackend(error=JudgeUnavailableError("must not be called"))

    with caplog.at_level(logging.INFO, logger="agentlens.core.session"):
        _, store_path, _ = _run(tmp_path, score=True, judge=judge, dry_run=True)

    assert judge.calls == []
    assert not store_path.exists()
    dry_run_messages = [
        record.message for record in caplog.records if "would score" in record.message
    ]
    assert len(dry_run_messages) == 1
    message = dry_run_messages[0]
    assert _JUDGE_MODEL_ALIAS in message
    assert RUBRIC_VERSION in message


def test_judge_call_failure_leaves_deterministic_facts_stored_and_propagates(
    tmp_path: Path,
) -> None:
    judge = FakeJudgeBackend(error=JudgeUnavailableError("judge is not authenticated"))
    transcript_path = build_transcript_path(tmp_path)
    _write_transcript(transcript_path)
    store_path = tmp_path / "store" / "agentlens.db"

    with pytest.raises(JudgeUnavailableError):
        analyze_session(
            transcript_path=transcript_path,
            store_path=store_path,
            clock=_CLOCK,
            output_format=FORMAT_JSON,
            dry_run=False,
            claude_root=tmp_path / ".claude",
            score=True,
            judge=judge,
            judge_model=_JUDGE_MODEL_ALIAS,
        )

    session_id = _session_id_from_store(store_path)
    with Store(store_path) as store:
        assert store.read_session(session_id) is not None
        assert store.read_verdicts_for_session(session_id) == ()


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
    _write_transcript(transcript_path)
    store_path = tmp_path / "store" / "agentlens.db"

    with (
        caplog.at_level(logging.ERROR, logger="agentlens.core.session"),
        pytest.raises(JudgeResponseError),
    ):
        analyze_session(
            transcript_path=transcript_path,
            store_path=store_path,
            clock=_CLOCK,
            output_format=FORMAT_JSON,
            dry_run=False,
            claude_root=tmp_path / ".claude",
            score=True,
            judge=judge,
            judge_model=_JUDGE_MODEL_ALIAS,
        )

    error_messages = [
        record.message for record in caplog.records if record.levelno == logging.ERROR
    ]
    assert len(error_messages) == 1
    assert "0.05" in error_messages[0]

    session_id = _session_id_from_store(store_path)
    with Store(store_path) as store:
        assert store.read_session(session_id) is not None
        assert store.read_verdicts_for_session(session_id) == ()


def test_response_with_no_cost_raises_and_persists_no_verdict(tmp_path: Path) -> None:
    response = build_judge_response(structured_output=_valid_structured_output(), cost_usd=None)
    judge = FakeJudgeBackend(response=response)
    transcript_path = build_transcript_path(tmp_path)
    _write_transcript(transcript_path)
    store_path = tmp_path / "store" / "agentlens.db"

    with pytest.raises(JudgeResponseError):
        analyze_session(
            transcript_path=transcript_path,
            store_path=store_path,
            clock=_CLOCK,
            output_format=FORMAT_JSON,
            dry_run=False,
            claude_root=tmp_path / ".claude",
            score=True,
            judge=judge,
            judge_model=_JUDGE_MODEL_ALIAS,
        )

    session_id = _session_id_from_store(store_path)
    with Store(store_path) as store:
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

    _, store_path, _ = _run(tmp_path, score=True, judge=judge)

    session_id = _session_id_from_store(store_path)
    with Store(store_path) as store:
        verdicts = store.read_verdicts_for_session(session_id)
    assert len(verdicts) == 1
    assert verdicts[0].judge_cost_usd == 0.03
    assert verdicts[0].judge_input_tokens == 0
    assert verdicts[0].judge_output_tokens == 0


def test_scoring_without_a_judge_raises_a_config_error(tmp_path: Path) -> None:
    transcript_path = build_transcript_path(tmp_path)
    _write_transcript(transcript_path)
    store_path = tmp_path / "store" / "agentlens.db"

    with pytest.raises(ConfigError):
        analyze_session(
            transcript_path=transcript_path,
            store_path=store_path,
            clock=_CLOCK,
            output_format=FORMAT_JSON,
            dry_run=False,
            claude_root=tmp_path / ".claude",
            score=True,
            judge=None,
            judge_model=None,
        )
