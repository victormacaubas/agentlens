"""Deriving a subagent session's qualified identity from its transcript path.

Layout is fixed by Claude Code, not chosen here: a subagent transcript lives at
``.claude/projects/<project>/<parent-session-uuid>/subagents/agent-<agentId>.jsonl``.
A path that does not sit in that shape does not identify an owning project, so
guessing one is refused rather than attempted.
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


@dataclass(frozen=True, slots=True, kw_only=True)
class TranscriptLocation:
    """The identity components read off a subagent transcript's file path."""

    source_project: str
    raw_session_id: str
    session_kind: SessionKind


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
    )


def build_session_identity(location: TranscriptLocation) -> SessionIdentity:
    """Return the qualified identity for ``location``.

    ``session_id`` is the SHA-256 of the three components together, so the
    same raw id under two different projects yields two distinct keys.
    """
    session_id = canonical_json_fingerprint(
        [location.source_project, location.session_kind.value, location.raw_session_id]
    )
    return SessionIdentity(
        session_id=session_id,
        source_project=location.source_project,
        session_kind=location.session_kind,
        raw_session_id=location.raw_session_id,
    )
