from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentlens.aggregation.derivation import derive_fact_session, derive_skill_bridge
from agentlens.discovery.models import MainSessionFile, SubagentRun
from agentlens.discovery.walker import (
    discover_agent_defs,
    discover_available_skills,
    discover_main_sessions,
    discover_subagent_runs,
)
from agentlens.parser.extraction import extract_task_subagent_types, read_jsonl_records
from agentlens.parser.session import (
    ParsedSession,
    parse_agent_definition,
    parse_main_session,
    parse_subagent_run,
)
from agentlens.store.operations import (
    fetch_declared_skills,
    upsert_agent_definition,
    upsert_session_grain,
)

logger = logging.getLogger(__name__)

IngestTarget = MainSessionFile | SubagentRun

@dataclass(frozen=True)
class IngestSummary:
    """Result of one `ingest_all` run."""

    n_ingested: int
    n_skipped: int = 0

class IngestRunner:
    """Owns the connection, caches, and iteration state for a bulk ingest run.

    Accepts dependencies in `__init__` and holds them across the batch so
    per-target methods don't thread `conn`/`claude_home` through every call.
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        claude_home: Path,
    ) -> None:
        self.conn = conn
        self.claude_home = claude_home
        self.available_skills: set[str] = discover_available_skills(claude_home)
        self._parent_task_maps: dict[Path, dict[str, str]] = {}

    def run(self, *, limit: int | None = None) -> IngestSummary:
        """Walk `projects/**`, parse every discovered session, and upsert the
        full grain for each. Idempotent by `session_id`.

        Each parent transcript is read and run through
        `extract_task_subagent_types` at most once per run, and that map is
        reused across every sibling spawn under the same parent.
        """
        projects_root = self.claude_home / "projects"
        targets: list[IngestTarget] = [
            *discover_main_sessions(projects_root),
            *discover_subagent_runs(projects_root),
        ]
        if limit is not None:
            targets = targets[:limit]

        n_ingested = 0
        n_skipped = 0
        for target in targets:
            try:
                parsed = self._parse_target(target)
                persist_parsed_session(
                    self.conn, parsed, available_skills=self.available_skills
                )
            except Exception:
                n_skipped += 1
                logger.warning(
                    "Skipping ingest target %s due to an error",
                    _target_path(target),
                    exc_info=True,
                )
                continue
            n_ingested += 1

        if n_skipped:
            logger.warning(
                "ingest_all completed with %d of %d targets skipped due to errors",
                n_skipped,
                len(targets),
            )

        return IngestSummary(n_ingested=n_ingested, n_skipped=n_skipped)

    def _parse_target(self, target: IngestTarget) -> ParsedSession:
        if isinstance(target, MainSessionFile):
            return parse_main_session(target.path, session_id=target.session_id)

        meta = _read_meta(target.meta_path) if target.meta_path is not None else None
        parent_path = target.project_dir / f"{target.parent_session_id}.jsonl"
        if parent_path not in self._parent_task_maps:
            parent_records = read_jsonl_records(parent_path) if parent_path.is_file() else []
            self._parent_task_maps[parent_path] = extract_task_subagent_types(parent_records)

        return parse_subagent_run(
            target.jsonl_path,
            agent_id=target.agent_id,
            parent_session_id=target.parent_session_id,
            meta=meta,
            parent_task_subagent_types=self._parent_task_maps[parent_path],
        )

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
        record = parse_agent_definition(agent_def_file.path)
        if record is not None:
            upsert_agent_definition(conn, record)


def persist_parsed_session(
    conn: sqlite3.Connection,
    parsed: ParsedSession,
    *,
    available_skills: Iterable[str] = (),
) -> None:
    """Derive and upsert the full session grain for one `ParsedSession`.

    Covers `fact_session`, `fact_tool_event`, and `bridge_session_skill`,
    plus `dim_date` / `dim_tool` backfill.
    """
    declared_skills = fetch_declared_skills(conn, parsed.name) if parsed.name else []
    session_record = derive_fact_session(parsed)
    skill_records = derive_skill_bridge(
        parsed, declared_skills=declared_skills, available_skills=available_skills
    )

    upsert_session_grain(
        conn,
        record=session_record,
        events=parsed.events,
        skills=skill_records,
    )


def ingest_all(
    conn: sqlite3.Connection,
    *,
    claude_home: Path,
    limit: int | None = None,
) -> IngestSummary:
    """Convenience wrapper that constructs an `IngestRunner` and runs."""
    runner = IngestRunner(conn, claude_home=claude_home)
    return runner.run(limit=limit)


def _target_path(target: IngestTarget) -> Path:
    return target.path if isinstance(target, MainSessionFile) else target.jsonl_path
