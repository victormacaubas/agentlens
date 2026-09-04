"""``SpawnScoringPreview.check_reusable`` reads reusable verdicts without a judge."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from agentlens.core.spawn_scoring import SpawnScoringPreview
from agentlens.ingest.context import SubagentContextCache
from agentlens.ingest.identity import SubagentSourceBundle
from agentlens.ingest.narrative import build_spawn_narrative
from agentlens.ingest.reading import read_transcript
from agentlens.ingest.sidecar import read_sidecar
from agentlens.ingest.transcript import parse_transcript
from agentlens.judge.prompt import render_prompt
from agentlens.judge.rubric import RUBRIC_VERSION
from agentlens.models.scoring import ScoringRequest
from agentlens.models.session_facts import SessionFacts
from agentlens.store import Store
from agentlens.utils.hashing import hash_text
from tests.factories import (
    build_fact_verdict,
    build_subagent_source_bundle,
    build_transcript_path,
    build_verdict_claim_identity,
    write_transcript,
)
from tests.fakes import FakeClock

_CLOCK = FakeClock(instant=datetime(2026, 1, 1, tzinfo=UTC))
_REQUESTED_MODEL = "claude-sonnet-5"


def _bundle_and_stored(tmp_path: Path) -> tuple[SubagentSourceBundle, SessionFacts]:
    home = tmp_path / "home"
    claude_root = home / ".claude"
    transcript_path = build_transcript_path(home)
    write_transcript(transcript_path)
    bundle = build_subagent_source_bundle(transcript_path=transcript_path)
    stored = parse_transcript(bundle, context_cache=SubagentContextCache(claude_root))
    return bundle, stored


def _prompt(bundle: SubagentSourceBundle) -> str:
    transcript = read_transcript(bundle.transcript_path)
    sidecar = read_sidecar(bundle.sidecar_path)
    return render_prompt(build_spawn_narrative(transcript.records, sidecar=sidecar))


def _request() -> ScoringRequest:
    return ScoringRequest(
        requested_model=_REQUESTED_MODEL, owner="scorer-one", claim_lease=timedelta(minutes=3)
    )


def test_check_reusable_returns_none_when_the_store_does_not_exist_yet(tmp_path: Path) -> None:
    store_path = tmp_path / "store" / "agentlens.db"
    bundle, stored = _bundle_and_stored(tmp_path)
    preview = SpawnScoringPreview(store_path=store_path, clock=_CLOCK, request=_request())

    assert preview.check_reusable(bundle, stored) is None


def test_check_reusable_returns_none_when_no_matching_verdict_is_stored(tmp_path: Path) -> None:
    store_path = tmp_path / "store" / "agentlens.db"
    bundle, stored = _bundle_and_stored(tmp_path)
    with Store(store_path, clock=_CLOCK):
        pass
    preview = SpawnScoringPreview(store_path=store_path, clock=_CLOCK, request=_request())

    assert preview.check_reusable(bundle, stored) is None


def test_check_reusable_returns_the_stored_verdict_for_a_matching_identity(
    tmp_path: Path,
) -> None:
    store_path = tmp_path / "store" / "agentlens.db"
    bundle, stored = _bundle_and_stored(tmp_path)
    session_id = stored.session.identity.session_id
    judge_input_hash = hash_text(_prompt(bundle))
    stored_verdict = build_fact_verdict(
        session_id=session_id,
        judge_input_hash=judge_input_hash,
        rubric_version=RUBRIC_VERSION,
        judge_model=_REQUESTED_MODEL,
    )
    with Store(store_path, clock=_CLOCK) as store:
        store.upsert_verdict(stored_verdict)
    preview = SpawnScoringPreview(store_path=store_path, clock=_CLOCK, request=_request())

    reusable = preview.check_reusable(bundle, stored)

    assert reusable == stored_verdict


def test_check_reusable_never_acquires_a_claim(tmp_path: Path) -> None:
    store_path = tmp_path / "store" / "agentlens.db"
    bundle, stored = _bundle_and_stored(tmp_path)
    session_id = stored.session.identity.session_id
    judge_input_hash = hash_text(_prompt(bundle))
    with Store(store_path, clock=_CLOCK):
        pass
    preview = SpawnScoringPreview(store_path=store_path, clock=_CLOCK, request=_request())

    preview.check_reusable(bundle, stored)

    identity = build_verdict_claim_identity(
        session_id=session_id,
        judge_input_hash=judge_input_hash,
        rubric_version=RUBRIC_VERSION,
        requested_model=_REQUESTED_MODEL,
    )
    with Store(store_path, clock=_CLOCK) as store:
        assert store.read_verdict_claim(identity) is None
