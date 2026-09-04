"""Coordinates the scoring lifecycle for one subagent spawn."""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from agentlens.errors import JudgeResponseError
from agentlens.ingest.identity import SubagentSourceBundle
from agentlens.ingest.narrative import build_spawn_narrative
from agentlens.ingest.reading import read_transcript
from agentlens.ingest.sidecar import read_sidecar
from agentlens.judge.prompt import render_prompt
from agentlens.judge.rubric import RUBRIC_VERSION
from agentlens.judge.verdict_validation import validate_verdict
from agentlens.models.claims import VerdictClaim, VerdictClaimIdentity
from agentlens.models.facts import FactVerdict
from agentlens.models.protocols import Clock, JudgeBackend
from agentlens.models.scoring import ScoringOutcome, ScoringRequest, ScoringStatus, SpawnJudgeUsage
from agentlens.models.session_facts import SessionFacts
from agentlens.store import ClaimOutcome, Store
from agentlens.utils.hashing import hash_text

logger = logging.getLogger(__name__)

_ZERO_SPAWN_JUDGE_USAGE: Final = SpawnJudgeUsage(
    cost_usd=0.0,
    input_tokens=0,
    output_tokens=0,
)


@dataclass(frozen=True, slots=True)
class _ScoringInputs:
    prompt: str
    judge_input_hash: str
    claim_identity: VerdictClaimIdentity


class SpawnScoringPreview:
    """Reads a spawn's reusable verdict without claiming or scoring it."""

    def __init__(self, *, store_path: Path, clock: Clock, request: ScoringRequest) -> None:
        self._store_path = store_path
        self._clock = clock
        self._request = request

    def check_reusable(
        self, bundle: SubagentSourceBundle, stored: SessionFacts
    ) -> FactVerdict | None:
        """Return the reusable verdict for one spawn, or ``None`` if it has none."""
        inputs = _build_scoring_inputs(bundle, stored, self._request)
        return _read_existing_verdict(self._store_path, self._clock, inputs.claim_identity)

    def preview(self, bundle: SubagentSourceBundle, stored: SessionFacts) -> FactVerdict | None:
        """Log whether scoring would reuse or score a spawn, without a write."""
        inputs = _build_scoring_inputs(bundle, stored, self._request)
        verdict = _read_existing_verdict(self._store_path, self._clock, inputs.claim_identity)
        session = stored.session
        if verdict is not None:
            logger.info(
                "Dry run: would reuse verdict for session_id=%s agent_type=%s "
                "identity=%s at zero cost",
                session.identity.session_id,
                session.agent_type,
                inputs.claim_identity,
            )
        else:
            logger.info(
                "Dry run: would score session_id=%s agent_type=%s after claiming identity=%s",
                session.identity.session_id,
                session.agent_type,
                inputs.claim_identity,
            )
        return verdict


class SpawnScoringRun:
    """Coordinates reusable verdict scoring for one spawn."""

    def __init__(
        self,
        *,
        store_path: Path,
        clock: Clock,
        judge: JudgeBackend,
        request: ScoringRequest,
    ) -> None:
        self._store_path = store_path
        self._clock = clock
        self._judge = judge
        self._request = request

    def score(
        self,
        bundle: SubagentSourceBundle,
        stored: SessionFacts,
    ) -> ScoringOutcome:
        """Score ``stored`` after reusing or claiming its requested-model identity.

        Both the cache probe and the claim are keyed on the model the caller
        requested, because that is the only model string in existence before the
        judge answers. A request under a floating alias therefore always reaches
        the judge, since an alias is never stored as a concrete ``judge_model``.

        Raises:
            ~agentlens.errors.JudgeError: The judge could not be reached, or
                its response could not be validated into a usable verdict.
        """
        inputs = _build_scoring_inputs(bundle, stored, self._request)
        existing_verdict = _read_existing_verdict(
            self._store_path,
            self._clock,
            inputs.claim_identity,
        )
        if existing_verdict is not None:
            return self._reuse_outcome(existing_verdict, inputs, stored)

        claim_outcome = self._acquire_claim(inputs.claim_identity)
        if claim_outcome is ClaimOutcome.HELD_ELSEWHERE:
            return self._claimed_elsewhere_outcome(inputs, stored)
        return self._score_claimed(bundle, inputs, stored)

    def _reuse_outcome(
        self,
        verdict: FactVerdict,
        inputs: _ScoringInputs,
        stored: SessionFacts,
    ) -> ScoringOutcome:
        session = stored.session
        logger.info(
            "Reusing verdict for session_id=%s agent_type=%s identity=%s at zero cost",
            session.identity.session_id,
            session.agent_type,
            inputs.claim_identity,
        )
        return ScoringOutcome(
            status=ScoringStatus.REUSED,
            verdict=verdict,
            spawn_judge_usage=_ZERO_SPAWN_JUDGE_USAGE,
            is_behind_current_input=False,
        )

    def _acquire_claim(self, identity: VerdictClaimIdentity) -> ClaimOutcome:
        claim = VerdictClaim(
            identity=identity,
            owner=self._request.owner,
            expires_at=self._clock.now() + self._request.claim_lease,
        )
        with Store(self._store_path, clock=self._clock) as store:
            return store.acquire_verdict_claim(claim)

    def _claimed_elsewhere_outcome(
        self, inputs: _ScoringInputs, stored: SessionFacts
    ) -> ScoringOutcome:
        session = stored.session
        logger.info(
            "Scoring skipped because claim is held elsewhere for "
            "session_id=%s agent_type=%s identity=%s",
            session.identity.session_id,
            session.agent_type,
            inputs.claim_identity,
        )
        return ScoringOutcome(
            status=ScoringStatus.CLAIMED_ELSEWHERE,
            verdict=None,
            spawn_judge_usage=_ZERO_SPAWN_JUDGE_USAGE,
            is_behind_current_input=False,
        )

    def _score_claimed(
        self,
        bundle: SubagentSourceBundle,
        inputs: _ScoringInputs,
        stored: SessionFacts,
    ) -> ScoringOutcome:
        finalized = False
        try:
            fact_verdict, spawn_judge_usage = self._score_with_judge(inputs, stored)
            is_behind_current_input = self._finalize_scored_verdict(
                bundle,
                fact_verdict,
                inputs,
                stored,
            )
            finalized = True
            return ScoringOutcome(
                status=ScoringStatus.SCORED,
                verdict=fact_verdict,
                spawn_judge_usage=spawn_judge_usage,
                is_behind_current_input=is_behind_current_input,
            )
        finally:
            if not finalized:
                self._release_claim(inputs.claim_identity)

    def _score_with_judge(
        self,
        inputs: _ScoringInputs,
        stored: SessionFacts,
    ) -> tuple[FactVerdict, SpawnJudgeUsage]:
        session = stored.session
        response = self._judge.score(inputs.prompt, model=self._request.requested_model)
        try:
            verdict = validate_verdict(response.structured_output)
        except JudgeResponseError as error:
            logger.exception(
                "Judge call for session_id=%s agent_type=%s identity=%s spent "
                "cost_usd=%s input_tokens=%s output_tokens=%s before verdict rejection",
                session.identity.session_id,
                session.agent_type,
                inputs.claim_identity,
                response.cost_usd,
                response.input_tokens,
                response.output_tokens,
            )
            raise JudgeResponseError(
                str(error),
                cost_usd=response.cost_usd if response.cost_usd is not None else 0.0,
                input_tokens=response.input_tokens if response.input_tokens is not None else 0,
                output_tokens=response.output_tokens if response.output_tokens is not None else 0,
            ) from error

        if response.cost_usd is None:
            raise JudgeResponseError(
                f"Judge call for session {session.identity.session_id!r} reported no cost; "
                "a verdict cannot be recorded with an unknowable cost."
            )

        spawn_judge_usage = SpawnJudgeUsage(
            cost_usd=response.cost_usd,
            input_tokens=response.input_tokens if response.input_tokens is not None else 0,
            output_tokens=response.output_tokens if response.output_tokens is not None else 0,
        )
        return (
            FactVerdict(
                session_id=session.identity.session_id,
                judge_input_hash=inputs.judge_input_hash,
                rubric_version=RUBRIC_VERSION,
                judge_model=response.resolved_model,
                verdict=verdict,
                judge_cost_usd=spawn_judge_usage.cost_usd,
                judge_input_tokens=spawn_judge_usage.input_tokens,
                judge_output_tokens=spawn_judge_usage.output_tokens,
                scored_at=self._clock.now(),
            ),
            spawn_judge_usage,
        )

    def _finalize_scored_verdict(
        self,
        bundle: SubagentSourceBundle,
        verdict: FactVerdict,
        inputs: _ScoringInputs,
        stored: SessionFacts,
    ) -> bool:
        is_behind_current_input = (
            hash_text(_render_prompt_from_source(bundle)) != inputs.judge_input_hash
        )
        self._finalize_verdict(verdict, claim_identity=inputs.claim_identity)
        if is_behind_current_input:
            session = stored.session
            logger.warning(
                "Scored verdict is behind current input for session_id=%s agent_type=%s "
                "identity=%s",
                session.identity.session_id,
                session.agent_type,
                inputs.claim_identity,
            )
        return is_behind_current_input

    def _finalize_verdict(
        self,
        verdict: FactVerdict,
        *,
        claim_identity: VerdictClaimIdentity,
    ) -> None:
        with Store(self._store_path, clock=self._clock) as store:
            store.finalize_verdict(verdict, claim_identity=claim_identity)

    def _release_claim(self, identity: VerdictClaimIdentity) -> None:
        with Store(self._store_path, clock=self._clock) as store:
            store.release_verdict_claim(identity)


def _build_scoring_inputs(
    bundle: SubagentSourceBundle,
    stored: SessionFacts,
    request: ScoringRequest,
) -> _ScoringInputs:
    prompt = _render_prompt_from_source(bundle)
    judge_input_hash = hash_text(prompt)
    session_id = stored.session.identity.session_id
    return _ScoringInputs(
        prompt=prompt,
        judge_input_hash=judge_input_hash,
        claim_identity=VerdictClaimIdentity(
            session_id=session_id,
            judge_input_hash=judge_input_hash,
            rubric_version=RUBRIC_VERSION,
            requested_model=request.requested_model,
        ),
    )


def _read_existing_verdict(
    store_path: Path,
    clock: Clock,
    identity: VerdictClaimIdentity,
) -> FactVerdict | None:
    if not store_path.exists():
        return None
    with Store(store_path, clock=clock) as store:
        return store.read_verdict_for_request(identity)


def _render_prompt_from_source(bundle: SubagentSourceBundle) -> str:
    transcript = read_transcript(bundle.transcript_path)
    sidecar = read_sidecar(bundle.sidecar_path)
    narrative = build_spawn_narrative(transcript.records, sidecar=sidecar)
    return render_prompt(narrative)
