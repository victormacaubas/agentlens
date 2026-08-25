"""Orchestrates one session analysis run across the sibling packages.

``ingest``, ``store``, and ``render`` cannot import each other, so this module
is where a transcript path becomes a rendered report: parse it, persist it,
read back what the store actually holds, and hand that to the renderer.
"""

import logging
from collections.abc import Sequence
from pathlib import Path

from agentlens.errors import ConfigError, JudgeResponseError, StoreError
from agentlens.ingest.context import SubagentContextCache
from agentlens.ingest.identity import SubagentSourceBundle, build_subagent_source_bundle
from agentlens.ingest.narrative import build_spawn_narrative
from agentlens.ingest.reading import read_transcript
from agentlens.ingest.sidecar import read_sidecar
from agentlens.ingest.transcript import parse_transcript
from agentlens.judge.prompt import render_prompt
from agentlens.judge.rubric import RUBRIC_VERSION
from agentlens.judge.verdict_validation import validate_verdict
from agentlens.models.agent_definitions import AgentDefinition
from agentlens.models.facts import FactVerdict
from agentlens.models.protocols import Clock, JudgeBackend
from agentlens.models.session_facts import SessionFacts
from agentlens.render.artifact import session_artifact_path, write_session_artifact
from agentlens.render.document import build_session_document, render_document_json
from agentlens.render.summary import build_session_summary
from agentlens.store import Store
from agentlens.utils.hashing import hash_text

logger = logging.getLogger(__name__)

FORMAT_JSON = "json"


def analyze_session(
    *,
    transcript_path: Path,
    store_path: Path,
    clock: Clock,
    output_format: str | None,
    dry_run: bool,
    claude_root: Path,
    score: bool,
    judge: JudgeBackend | None,
    judge_model: str | None,
) -> str:
    """Ingest, persist, and render one subagent transcript.

    Parses ``transcript_path``, resolving its agent-definition and
    skill-inventory context from its own ``cwd`` under ``claude_root``, writes
    it to the store at ``store_path``, then reads the session back rather than
    rendering what was just parsed: a snapshot skipped as identical or
    refused as stale means the row actually stored can differ from the one
    just read off disk, and the report must reflect what is stored.

    When ``dry_run`` is set, neither the store nor the report artifact is
    written; the session just parsed is rendered directly instead, and what
    would have been written is logged on the diagnostic stream.

    The deterministic facts are always parsed and persisted first, regardless
    of ``score``, so a scoring failure never costs that write. When ``score``
    is set, ``judge`` and ``judge_model`` must both be supplied: scoring
    builds the spawn's narrative, renders it into a prompt, and calls
    ``judge.score`` unless ``dry_run`` is also set, in which case no call is
    made and no verdict is written. ``judge`` and ``judge_model`` are unused
    when ``score`` is ``False``.

    Returns the JSON document verbatim when ``output_format`` is
    ``FORMAT_JSON``; otherwise writes the report artifact and returns the
    human-readable summary naming its path.

    Raises:
        ~agentlens.errors.SourceError: The transcript could not be read
            soundly.
        ~agentlens.errors.StoreError: The store could not be opened, written,
            or read back.
        ~agentlens.errors.ConfigError: Scoring was requested without both a
            judge backend and a model to request.
        ~agentlens.errors.JudgeError: Scoring was requested and the judge
            call, or the verdict it returned, could not be used.
    """
    context_cache = SubagentContextCache(claude_root)
    bundle = build_subagent_source_bundle(transcript_path)
    facts = parse_transcript(bundle, context_cache=context_cache)
    session_id = facts.session.identity.session_id
    logger.info(
        "Parsed session %s (agent_type=%s, name_source=%s)",
        session_id,
        facts.session.agent_type,
        facts.session.name_source,
    )

    stored = _persist_or_log(
        facts,
        store_path=store_path,
        dry_run=dry_run,
        definitions=context_cache.discovered_definitions(),
    )

    verdict: FactVerdict | None = None
    if score:
        verdict = _score_spawn(
            bundle,
            stored=stored,
            store_path=store_path,
            dry_run=dry_run,
            judge=judge,
            judge_model=judge_model,
            clock=clock,
        )

    document = build_session_document(stored, clock=clock, verdict=verdict)
    if output_format == FORMAT_JSON:
        return render_document_json(document)

    artifact_path = _write_artifact_or_log(document, session_id=session_id, dry_run=dry_run)
    return build_session_summary(stored, artifact_path=artifact_path, verdict=verdict)


def _score_spawn(
    bundle: SubagentSourceBundle,
    *,
    stored: SessionFacts,
    store_path: Path,
    dry_run: bool,
    judge: JudgeBackend | None,
    judge_model: str | None,
    clock: Clock,
) -> FactVerdict | None:
    """Score the spawn ``stored`` represents, or log what scoring it would do.

    Builds the narrative and renders the prompt in every case, since that
    costs nothing; a real judge call happens only when ``dry_run`` is unset.

    Raises:
        ~agentlens.errors.ConfigError: ``judge`` or ``judge_model`` was not
            supplied.
        ~agentlens.errors.JudgeError: The judge could not be reached, or its
            response could not be validated into a usable verdict.
    """
    if judge is None or judge_model is None:
        raise ConfigError("Scoring was requested but no judge backend or model was configured.")

    session_id = stored.session.identity.session_id
    transcript = read_transcript(bundle.transcript_path)
    sidecar = read_sidecar(bundle.sidecar_path)
    narrative = build_spawn_narrative(transcript.records, sidecar=sidecar)
    prompt = render_prompt(narrative)
    judge_input_hash = hash_text(prompt)

    if dry_run:
        logger.info(
            "Dry run: would score session %s with model %s "
            "(judge_input_hash=%s, rubric_version=%s)",
            session_id,
            judge_model,
            judge_input_hash,
            RUBRIC_VERSION,
        )
        return None

    response = judge.score(prompt, model=judge_model)
    try:
        verdict = validate_verdict(response.structured_output)
    except JudgeResponseError:
        logger.error(
            "Judge call for session %s spent cost_usd=%s input_tokens=%s output_tokens=%s "
            "before its verdict was rejected",
            session_id,
            response.cost_usd,
            response.input_tokens,
            response.output_tokens,
        )
        raise

    if response.cost_usd is None:
        raise JudgeResponseError(
            f"Judge call for session {session_id!r} reported no cost; a verdict cannot be "
            "recorded with an unknowable cost."
        )

    fact_verdict = FactVerdict(
        session_id=session_id,
        judge_input_hash=judge_input_hash,
        rubric_version=RUBRIC_VERSION,
        judge_model=response.resolved_model,
        verdict=verdict,
        judge_cost_usd=response.cost_usd,
        # Token counts are a secondary signal; a response that reports a
        # usable cost but omits them should still yield a persisted verdict.
        judge_input_tokens=response.input_tokens if response.input_tokens is not None else 0,
        judge_output_tokens=response.output_tokens if response.output_tokens is not None else 0,
        scored_at=clock.now(),
    )
    with Store(store_path) as store:
        store.upsert_verdict(fact_verdict)
    return fact_verdict


def _persist_or_log(
    facts: SessionFacts,
    *,
    store_path: Path,
    dry_run: bool,
    definitions: Sequence[AgentDefinition],
) -> SessionFacts:
    session_id = facts.session.identity.session_id
    if dry_run:
        logger.info("Dry run: would upsert session %s at %s", session_id, store_path)
        return facts

    with Store(store_path) as store:
        outcome = store.upsert_batch(definitions=definitions, facts=(facts,))[0]
        logger.info("Store outcome for session %s: %s", session_id, outcome)
        stored = store.read_session(session_id)

    if stored is None:
        raise StoreError(f"session {session_id!r} was written but could not be read back")
    return stored


def _write_artifact_or_log(document: dict[str, object], *, session_id: str, dry_run: bool) -> Path:
    if dry_run:
        artifact_path = session_artifact_path(session_id)
        logger.info("Dry run: would write artifact to %s", artifact_path)
        return artifact_path
    return write_session_artifact(document, session_id=session_id)
