"""Turning one discovered subagent source into ``SessionFacts``.

:func:`parse_transcript` is the package's sole entry point: read the
transcript the bundle names while verifying it did not change, resolve the
agent's name, and derive the session row from what was read. This is the
only place in the package where a filesystem ``OSError`` is caught and
translated, since the read itself happens here and nowhere lower.

The bundle already resolved the transcript's identity, its optional
sidecar's path, and its qualified parent session at discovery time; this
module consumes those fields rather than re-deriving any of them from
``bundle.transcript_path``.

Name resolution may read one more file beyond the transcript and its
sidecar: the parent transcript that carries the spawning invocation. That
read reuses the same soundness-checked streaming reader as the subagent's own
transcript, but its content is never persisted and its absence, unreadability,
or mid-read change is never a hard failure — the ordered name-resolution chain
simply falls through to its remaining links.

Binding a spawn to an agent-definition catalog, and folding a discovered
skill inventory into its bridge rows, both need the spawn's project root —
its ``cwd``, a field that sits inside the transcript itself. So the sequence
here is: read the transcript, then resolve project context from what was
read, then derive the session row from both together.
"""

from dataclasses import dataclass

from agentlens.errors import MalformedSourceError, SourceChangedError
from agentlens.ingest.agent_definitions import resolve_agent_definition_binding
from agentlens.ingest.context import SubagentContextCache
from agentlens.ingest.identity import (
    SubagentSourceBundle,
    build_session_identity,
    derive_parent_evidence_path,
)
from agentlens.ingest.name_resolution import resolve_agent_type
from agentlens.ingest.reading import read_transcript
from agentlens.ingest.records import (
    earliest_timestamp,
    find_spawning_invocation_subagent_type,
    resolve_agent_id,
    resolve_attribution_agent_types,
    resolve_cwd,
)
from agentlens.ingest.session import build_fact_session
from agentlens.ingest.sidecar import Sidecar, read_sidecar
from agentlens.ingest.tool_events import pair_tool_events
from agentlens.models.identity import SourceRevision
from agentlens.models.session_facts import SessionFacts


@dataclass(frozen=True, slots=True, kw_only=True)
class _ParentEvidence:
    """What the parent-transcript name-resolution link found, if it was read at all."""

    subagent_type: str | None
    revision: SourceRevision | None


_NO_PARENT_EVIDENCE = _ParentEvidence(subagent_type=None, revision=None)


def parse_transcript(
    bundle: SubagentSourceBundle, *, context_cache: SubagentContextCache
) -> SessionFacts:
    """Parse the subagent source in ``bundle`` into one session and its rows.

    ``context_cache`` resolves the spawn's project-scoped agent-definition
    catalog and skill inventory from the ``cwd`` this transcript itself
    carries, memoizing that resolution across every transcript the caller
    parses with the same cache instance.

    An instance this function returns is always safe to persist: every case
    that would make it unsound raises instead of producing a flagged value.

    Raises:
        MalformedSourceError: The sidecar named by ``bundle`` could not be
            parsed, or the transcript yielded no usable records.
        ~agentlens.errors.SourceChangedError: The file changed while being
            read.
    """
    identity = build_session_identity(bundle)

    try:
        contents = read_transcript(bundle.transcript_path)
    except OSError as exc:
        raise MalformedSourceError(f"could not read {bundle.transcript_path}") from exc
    if not contents.records:
        raise MalformedSourceError(f"{bundle.transcript_path} yielded no usable records")

    context = context_cache.resolve(resolve_cwd(contents.records))

    sidecar = read_sidecar(bundle.sidecar_path)
    sidecar_agent_type = sidecar.agent_type if sidecar is not None and sidecar.agent_type else None
    attribution_agent_types = resolve_attribution_agent_types(contents.records)
    parent_evidence = _NO_PARENT_EVIDENCE
    if sidecar is not None and sidecar_agent_type is None and not attribution_agent_types:
        parent_evidence = _resolve_parent_evidence(bundle=bundle, sidecar=sidecar)

    name_resolution = resolve_agent_type(
        sidecar_agent_type=sidecar_agent_type,
        attribution_agent_types=attribution_agent_types,
        parent_subagent_type=parent_evidence.subagent_type,
        raw_session_id=identity.raw_session_id,
    )
    agent_definition = resolve_agent_definition_binding(
        effective_definitions=context.effective_definitions,
        agent_type=name_resolution.agent_type,
        started_at=earliest_timestamp(contents.records),
    )
    agent_id = resolve_agent_id(contents.records, fallback=identity.raw_session_id)
    tool_events = pair_tool_events(contents.records, session_id=identity.session_id)
    session, skill_signals = build_fact_session(
        identity=identity,
        revision=contents.revision,
        agent_id=agent_id,
        agent_definition=agent_definition,
        parent_session_id=bundle.parent_session_id,
        records=contents.records,
        tool_events=tool_events,
        sidecar=sidecar,
        name_resolution=name_resolution,
        attribution_agent_types=attribution_agent_types,
        parent_evidence_revision=parent_evidence.revision,
        unreadable_line_count=contents.unreadable_line_count,
        skill_inventory=context.skill_inventory,
    )
    return SessionFacts(session=session, tool_events=tool_events, skill_signals=skill_signals)


def _resolve_parent_evidence(*, bundle: SubagentSourceBundle, sidecar: Sidecar) -> _ParentEvidence:
    """Read the file expected to hold the spawning invocation, tolerating its absence.

    A missing, unreadable, or mid-read-changed parent transcript is never a
    hard failure for the subagent being parsed: it leaves ``parent_evidence``
    at its default, and name resolution falls through to the raw-id hash.
    """
    parent_path = derive_parent_evidence_path(bundle, parent_agent_id=sidecar.parent_agent_id)
    try:
        parent_contents = read_transcript(parent_path)
    except (OSError, SourceChangedError):
        return _NO_PARENT_EVIDENCE
    subagent_type = find_spawning_invocation_subagent_type(
        parent_contents.records, tool_use_id=sidecar.tool_use_id
    )
    return _ParentEvidence(subagent_type=subagent_type, revision=parent_contents.revision)
