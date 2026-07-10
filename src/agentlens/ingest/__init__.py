from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from agentlens.discovery import discover_agent_defs, discover_main_sessions, discover_subagent_runs
from agentlens.discovery.models import AgentDefFile, MainSessionFile, SubagentRun
from agentlens.parser.extraction import read_jsonl_records
from agentlens.parser.session import (
    ParsedSession,
    parse_agent_definition,
    parse_main_session,
    parse_subagent_run,
)
from agentlens.store import upsert_agent_definition

IngestTarget = MainSessionFile | SubagentRun


def resolve_target(
    *,
    file_path: Path | None,
    session_id: str | None,
    claude_home: Path,
) -> IngestTarget | None:
    """Resolve a CLI-supplied `--file`/session_id pair to a concrete ingest target."""
    if file_path is not None:
        return _target_from_file(file_path)
    if session_id is not None:
        return _find_target_by_session_id(session_id, claude_home)
    return None


def _target_from_file(path: Path) -> IngestTarget:
    if path.parent.name == "subagents" and path.name.startswith("agent-"):
        agent_id = path.stem.removeprefix("agent-")
        meta_path = path.with_name(f"{path.stem}.meta.json")
        return SubagentRun(
            jsonl_path=path,
            meta_path=meta_path if meta_path.is_file() else None,
            agent_id=agent_id,
            parent_session_id=path.parent.parent.name,
            project_dir=path.parent.parent.parent,
        )
    return MainSessionFile(path=path, session_id=path.stem, project_dir=path.parent)


def _find_target_by_session_id(session_id: str, claude_home: Path) -> IngestTarget | None:
    projects_root = claude_home / "projects"
    for main_session in discover_main_sessions(projects_root):
        if main_session.session_id == session_id:
            return main_session
    for run in discover_subagent_runs(projects_root):
        if run.agent_id == session_id:
            return run
    return None


def ingest_target(target: IngestTarget) -> ParsedSession:
    """Parse a resolved target into a `ParsedSession` (no store writes)."""
    if isinstance(target, MainSessionFile):
        return parse_main_session(target.path, session_id=target.session_id)

    meta = _read_meta(target.meta_path) if target.meta_path is not None else None
    parent_path = target.project_dir / f"{target.parent_session_id}.jsonl"
    parent_records = read_jsonl_records(parent_path) if parent_path.is_file() else []
    return parse_subagent_run(
        target.jsonl_path,
        agent_id=target.agent_id,
        parent_session_id=target.parent_session_id,
        meta=meta,
        parent_records=parent_records,
    )


def _read_meta(meta_path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def sync_agent_definitions(
    conn: sqlite3.Connection,
    *,
    claude_home: Path,
    project_claude_dir: Path | None = None,
) -> None:
    """Scan `.claude/agents/**` and upsert every parseable definition into dim_agent."""
    for agent_def_file in discover_agent_defs(
        claude_home=claude_home, project_claude_dir=project_claude_dir
    ):
        _upsert_one_agent_def(conn, agent_def_file)


def _upsert_one_agent_def(conn: sqlite3.Connection, agent_def_file: AgentDefFile) -> None:
    record = parse_agent_definition(agent_def_file.path)
    if record is not None:
        upsert_agent_definition(conn, record)
