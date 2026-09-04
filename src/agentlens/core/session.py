"""Orchestrates one session analysis run across the sibling packages.

``ingest``, ``store``, and ``render`` cannot import each other, so this module
is where a transcript path becomes a rendered report: parse it, persist it,
read back what the store actually holds, and hand that to the renderer.
"""

import logging
from collections.abc import Sequence
from pathlib import Path

from agentlens.core.spawn_scoring import SpawnScoringPreview, SpawnScoringRun
from agentlens.errors import ConfigError, StoreError
from agentlens.ingest.context import SubagentContextCache
from agentlens.ingest.identity import build_subagent_source_bundle
from agentlens.ingest.transcript import parse_transcript
from agentlens.models.agent_definitions import AgentDefinition
from agentlens.models.protocols import Clock, JudgeBackend
from agentlens.models.scoring import ScoringOutcome, ScoringRequest
from agentlens.models.session_facts import SessionFacts
from agentlens.render.artifact import session_artifact_path, write_session_artifact
from agentlens.render.document import build_session_document, render_document_json
from agentlens.render.summary import build_session_summary
from agentlens.store import Store

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
    scoring: ScoringRequest | None,
    judge: JudgeBackend | None,
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
    of ``scoring``, so a scoring failure never costs that write. A scoring
    request requires ``judge`` and coordinates a reusable verdict before
    calling it. During a dry run, no claim or verdict is written and no judge
    call is made.

    Returns the JSON document verbatim when ``output_format`` is
    ``FORMAT_JSON``; otherwise writes the report artifact and returns the
    human-readable summary naming its path.

    Raises:
        ~agentlens.errors.SourceError: The transcript could not be read
            soundly.
        ~agentlens.errors.StoreError: The store could not be opened, written,
            or read back.
        ~agentlens.errors.ConfigError: Scoring was requested without a judge
            backend.
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
        clock=clock,
    )

    scoring_outcome: ScoringOutcome | None = None
    if scoring is not None:
        if dry_run:
            SpawnScoringPreview(store_path=store_path, clock=clock, request=scoring).preview(
                bundle,
                stored,
            )
        else:
            if judge is None:
                raise ConfigError("Scoring was requested but no judge backend was configured.")
            scoring_outcome = SpawnScoringRun(
                store_path=store_path,
                clock=clock,
                judge=judge,
                request=scoring,
            ).score(bundle, stored)

    document = build_session_document(stored, clock=clock, scoring_outcome=scoring_outcome)
    if output_format == FORMAT_JSON:
        return render_document_json(document)

    artifact_path = _write_artifact_or_log(document, session_id=session_id, dry_run=dry_run)
    return build_session_summary(
        stored,
        artifact_path=artifact_path,
        scoring_outcome=scoring_outcome,
    )


def _persist_or_log(
    facts: SessionFacts,
    *,
    store_path: Path,
    dry_run: bool,
    definitions: Sequence[AgentDefinition],
    clock: Clock,
) -> SessionFacts:
    session_id = facts.session.identity.session_id
    if dry_run:
        logger.info("Dry run: would upsert session %s at %s", session_id, store_path)
        return facts

    with Store(store_path, clock=clock) as store:
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
