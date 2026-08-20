from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SourceIdentity:
    """The stable source tuple behind an internal session key."""

    source_project: str
    session_kind: str
    raw_session_id: str

    @property
    def session_id(self) -> str:
        return qualify_session_id(
            source_project=self.source_project,
            session_kind=self.session_kind,
            raw_session_id=self.raw_session_id,
        )


def qualify_session_id(
    *,
    source_project: str,
    session_kind: str,
    raw_session_id: str,
) -> str:
    """Hash a canonical source tuple into an opaque session key."""
    payload = json.dumps(
        [source_project, session_kind, raw_session_id],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class MainSessionFile:
    """A top-level session transcript: `projects/<project>/<sid>.jsonl`."""

    path: Path
    session_id: str
    raw_session_id: str
    source_project: str
    project_dir: Path


@dataclass(frozen=True)
class SubagentRun:
    """A subagent transcript paired with its (optional) `.meta.json` sidecar."""

    jsonl_path: Path
    meta_path: Path | None
    agent_id: str
    session_id: str
    parent_session_id: str
    raw_parent_session_id: str
    source_project: str
    project_dir: Path


@dataclass(frozen=True)
class AgentDefFile:
    """A discovered agent definition file, flat or nested, project or user scoped."""

    path: Path
    scope: str  # "project" | "user"
    source_project: str | None = None
