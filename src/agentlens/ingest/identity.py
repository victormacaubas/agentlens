"""Deriving a subagent session's qualified identity from its transcript path.

Layout is fixed by Claude Code, not chosen here: a subagent transcript lives at
``.claude/projects/<project>/<parent-session-uuid>/subagents/agent-<agentId>.jsonl``.
A path that does not sit in that shape does not identify an owning project, so
guessing one is refused rather than attempted.

:func:`build_subagent_source_bundle` is where that path shape is interpreted
exactly once per discovered source: it resolves the transcript's identity,
its optional sidecar's path, and its qualified parent session in one place,
so :func:`agentlens.ingest.transcript.parse_transcript` can consume the
result without re-deriving any of it from the transcript path itself.
"""

from dataclasses import dataclass
from pathlib import Path

from agentlens.errors import MalformedSourceError
from agentlens.models.identity import SessionIdentity, SessionKind
from agentlens.utils.hashing import canonical_json_fingerprint

_PROJECTS_DIR_NAME = "projects"
_SUBAGENTS_DIR_NAME = "subagents"
_AGENT_FILENAME_PREFIX = "agent-"
_TRANSCRIPT_SUFFIX = ".jsonl"
_SIDECAR_SUFFIX = ".meta.json"


@dataclass(frozen=True, slots=True, kw_only=True)
class TranscriptLocation:
    """The identity components read off a subagent transcript's file path.

    ``raw_parent_session_id`` is the spawning main session's own raw id: the
    name of the directory that holds ``subagents``, one level above it.
    """

    source_project: str
    raw_session_id: str
    session_kind: SessionKind
    raw_parent_session_id: str


def derive_transcript_location(path: Path) -> TranscriptLocation:
    """Derive the owning project, raw session id, and session kind from ``path``.

    Raises:
        MalformedSourceError: ``path`` does not sit under
            ``.claude/projects/<project>/<uuid>/subagents/agent-<agentId>.jsonl``,
            so no project can be derived without guessing one. This covers a
            main-session path, which has no ``subagents`` segment.
    """
    name = path.name
    if not (name.startswith(_AGENT_FILENAME_PREFIX) and name.endswith(_TRANSCRIPT_SUFFIX)):
        raise MalformedSourceError(f"{path} is not a subagent transcript filename")
    raw_session_id = name[len(_AGENT_FILENAME_PREFIX) : -len(_TRANSCRIPT_SUFFIX)]
    if not raw_session_id:
        raise MalformedSourceError(f"{path} has no agent id in its filename")

    subagents_dir = path.parent
    if subagents_dir.name != _SUBAGENTS_DIR_NAME:
        raise MalformedSourceError(f"{path} does not sit under a '{_SUBAGENTS_DIR_NAME}' directory")

    project_dir = subagents_dir.parent.parent
    projects_dir = project_dir.parent
    if projects_dir.name != _PROJECTS_DIR_NAME:
        raise MalformedSourceError(f"{path} does not sit under a '{_PROJECTS_DIR_NAME}' directory")

    return TranscriptLocation(
        source_project=project_dir.name,
        raw_session_id=raw_session_id,
        session_kind=SessionKind.SUBAGENT,
        raw_parent_session_id=subagents_dir.parent.name,
    )


def derive_parent_session_id(location: TranscriptLocation) -> str:
    """Return the qualified key of the main session that spawned ``location``.

    Qualified the same way as any session identity, but pinned to
    :attr:`SessionKind.MAIN` and the same project, so a subagent's lineage can
    never resolve into a different project's session.
    """
    return canonical_json_fingerprint(
        [location.source_project, SessionKind.MAIN.value, location.raw_parent_session_id]
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class SubagentSourceBundle:
    """One discovered subagent source, with every path relationship resolved once.

    :func:`build_subagent_source_bundle` builds this by interpreting the
    transcript's path exactly once; parsing consumes its fields rather than
    re-deriving the transcript's location, its sidecar relationship, its
    source project, or its parent identity from ``transcript_path`` itself.

    ``sidecar_path`` is ``None`` when no ``.meta.json`` sits next to the
    transcript at discovery time. ``parent_session_id`` is already the
    qualified main-session key; ``raw_parent_session_id`` is retained
    alongside it because a depth-2 spawn's spawning invocation lives in a
    sibling subagent transcript rather than in that main session.
    """

    transcript_path: Path
    sidecar_path: Path | None
    source_project: str
    raw_session_id: str
    raw_parent_session_id: str
    parent_session_id: str


def build_subagent_source_bundle(transcript_path: Path) -> SubagentSourceBundle:
    """Resolve every path relationship for the transcript at ``transcript_path``, once.

    Existence-checks the sidecar candidate next to ``transcript_path`` so the
    bundle's ``sidecar_path`` is ``None`` exactly when there is nothing to
    read there.

    Raises:
        MalformedSourceError: ``transcript_path`` does not sit under
            ``.claude/projects/<project>/<uuid>/subagents/agent-<agentId>.jsonl``.
    """
    location = derive_transcript_location(transcript_path)
    sidecar_path = transcript_path.with_suffix(_SIDECAR_SUFFIX)
    return SubagentSourceBundle(
        transcript_path=transcript_path,
        sidecar_path=sidecar_path if sidecar_path.exists() else None,
        source_project=location.source_project,
        raw_session_id=location.raw_session_id,
        raw_parent_session_id=location.raw_parent_session_id,
        parent_session_id=derive_parent_session_id(location),
    )


def build_session_identity(bundle: SubagentSourceBundle) -> SessionIdentity:
    """Return the qualified identity for ``bundle``.

    ``session_id`` is the SHA-256 of the three components together, so the
    same raw id under two different projects yields two distinct keys.
    """
    session_id = canonical_json_fingerprint(
        [bundle.source_project, SessionKind.SUBAGENT.value, bundle.raw_session_id]
    )
    return SessionIdentity(
        session_id=session_id,
        source_project=bundle.source_project,
        session_kind=SessionKind.SUBAGENT,
        raw_session_id=bundle.raw_session_id,
    )


def derive_parent_evidence_path(
    bundle: SubagentSourceBundle, *, parent_agent_id: str | None
) -> Path:
    """Return the file that should hold ``bundle``'s spawning invocation.

    Subagent nesting is flat, so a depth-2 spawn still sits directly under
    ``<parent-uuid>/subagents/``, but its own spawning invocation was issued
    by the subagent that spawned it, not by the main session — that
    invocation lives only in the immediate parent subagent's own transcript, a
    sibling file in the same ``subagents`` directory (``parent_agent_id`` not
    ``None``). Every other spawn's invocation lives in the grandparent-derived
    main-session file, one level above the transcript's own parent directory.
    """
    if parent_agent_id is not None:
        return (
            bundle.transcript_path.parent
            / f"{_AGENT_FILENAME_PREFIX}{parent_agent_id}{_TRANSCRIPT_SUFFIX}"
        )
    project_dir = bundle.transcript_path.parent.parent.parent
    return project_dir / f"{bundle.raw_parent_session_id}{_TRANSCRIPT_SUFFIX}"
