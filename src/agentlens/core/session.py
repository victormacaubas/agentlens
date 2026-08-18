"""Orchestrates one session analysis run across the sibling packages.

``ingest``, ``store``, and ``render`` cannot import each other, so this module
is where a transcript path becomes a rendered report: parse it, persist it,
read back what the store actually holds, and hand that to the renderer.
"""

import logging
from pathlib import Path

from agentlens.errors import StoreError
from agentlens.ingest.transcript import parse_transcript
from agentlens.models.protocols import Clock
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
) -> str:
    """Ingest, persist, and render one subagent transcript.

    Parses ``transcript_path``, writes it to the store at ``store_path``, then
    reads the session back rather than rendering what was just parsed: a
    snapshot skipped as identical or refused as stale means the row actually
    stored can differ from the one just read off disk, and the report must
    reflect what is stored.

    When ``dry_run`` is set, neither the store nor the report artifact is
    written; the session just parsed is rendered directly instead, and what
    would have been written is logged on the diagnostic stream.

    Returns the JSON document verbatim when ``output_format`` is
    ``FORMAT_JSON``; otherwise writes the report artifact and returns the
    human-readable summary naming its path.

    Raises:
        ~agentlens.errors.SourceError: The transcript could not be read
            soundly.
        ~agentlens.errors.StoreError: The store could not be opened, written,
            or read back.
    """
    facts = parse_transcript(transcript_path)
    session_id = facts.session.identity.session_id
    logger.info(
        "Parsed session %s (agent_type=%s, name_source=%s)",
        session_id,
        facts.session.agent_type,
        facts.session.name_source,
    )

    stored = _persist_or_log(facts, store_path=store_path, dry_run=dry_run)

    document = build_session_document(stored, clock=clock)
    if output_format == FORMAT_JSON:
        return render_document_json(document)

    artifact_path = _write_artifact_or_log(document, session_id=session_id, dry_run=dry_run)
    return build_session_summary(stored, artifact_path=artifact_path)


def _persist_or_log(facts: SessionFacts, *, store_path: Path, dry_run: bool) -> SessionFacts:
    session_id = facts.session.identity.session_id
    if dry_run:
        logger.info("Dry run: would upsert session %s at %s", session_id, store_path)
        return facts

    with Store(store_path) as store:
        outcome = store.upsert_session(facts)
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
