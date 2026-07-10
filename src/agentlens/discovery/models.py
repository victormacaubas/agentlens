from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MainSessionFile:
    """A top-level session transcript: `projects/<project>/<sid>.jsonl`."""

    path: Path
    session_id: str
    project_dir: Path


@dataclass(frozen=True)
class SubagentRun:
    """A subagent transcript paired with its (optional) `.meta.json` sidecar."""

    jsonl_path: Path
    meta_path: Path | None
    agent_id: str
    parent_session_id: str
    project_dir: Path


@dataclass(frozen=True)
class AgentDefFile:
    """A discovered agent definition file, flat or nested, project or user scoped."""

    path: Path
    scope: str  # "project" | "user"
