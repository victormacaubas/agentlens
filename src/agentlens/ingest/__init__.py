from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from agentlens.aggregation import derive_fact_session, derive_skill_bridge
from agentlens.discovery import (
    discover_agent_defs,
    discover_available_skills,
    discover_main_sessions,
    discover_subagent_runs,
)
from agentlens.discovery.models import AgentDefFile, MainSessionFile, SubagentRun
from agentlens.parser.extraction import extract_task_subagent_types, read_jsonl_records
from agentlens.parser.session import (
    ParsedSession,
    parse_agent_definition,
    parse_main_session,
    parse_subagent_run,
)
from agentlens.store import (
    fetch_declared_skills,
    upsert_agent_definition,
    upsert_dim_date,
    upsert_dim_tool,
    upsert_session,
    upsert_session_events,
    upsert_session_skills,
)

logger = logging.getLogger(__name__)

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


def persist_parsed_session(
    conn: sqlite3.Connection,
    parsed: ParsedSession,
    *,
    available_skills: Iterable[str] = (),
) -> None:
    """Derive and upsert the full session grain for one `ParsedSession`.

    Covers `fact_session`, `fact_tool_event`, and `bridge_session_skill`
    (the session-parser spec's "Idempotent ingest" requirement: a
    re-ingested session replaces its rows in every table, not only
    `fact_tool_event`), plus `dim_date` / `dim_tool` backfill. Shared by
    both the single-session `session` CLI command and `ingest_all`.
    """
    declared_skills = fetch_declared_skills(conn, parsed.name) if parsed.name else []
    session_record = derive_fact_session(parsed)
    skill_records = derive_skill_bridge(
        parsed, declared_skills=declared_skills, available_skills=available_skills
    )

    upsert_session_events(conn, parsed.session_id, parsed.events)
    upsert_session(conn, session_record)
    upsert_session_skills(conn, parsed.session_id, skill_records)

    for tool_name in {event.tool_name for event in parsed.events}:
        upsert_dim_tool(conn, tool_name)
    if session_record.session_date is not None:
        _backfill_dim_date(conn, session_record.session_date)


def _backfill_dim_date(conn: sqlite3.Connection, date_str: str) -> None:
    try:
        parsed_date = date.fromisoformat(date_str)
    except ValueError:
        return
    _, iso_week, _ = parsed_date.isocalendar()
    upsert_dim_date(
        conn,
        date_str,
        year=parsed_date.year,
        month=parsed_date.month,
        day=parsed_date.day,
        iso_week=iso_week,
    )


@dataclass(frozen=True)
class IngestSummary:
    """Result of one `ingest_all` run."""

    n_ingested: int


def ingest_all(
    conn: sqlite3.Connection,
    *,
    claude_home: Path,
    limit: int | None = None,
) -> IngestSummary:
    """Walk `projects/**`, parse every discovered session, and upsert the
    full grain for each (D4). Idempotent by `session_id` (the per-spawn
    `agent_id` for subagents) via the underlying upserts.

    Each parent transcript is read and run through
    `extract_task_subagent_types` at most once per `ingest_all` call, and
    that map is reused across every sibling spawn under the same parent —
    the cheap path bulk ingest would otherwise re-pay per sibling.
    """
    projects_root = claude_home / "projects"
    targets: list[IngestTarget] = [
        *discover_main_sessions(projects_root),
        *discover_subagent_runs(projects_root),
    ]
    if limit is not None:
        targets = targets[:limit]

    available_skills = discover_available_skills(claude_home)
    parent_task_maps: dict[Path, dict[str, str]] = {}

    n_ingested = 0
    n_skipped = 0
    for target in targets:
        try:
            parsed = _parse_ingest_target(target, parent_task_maps)
            persist_parsed_session(conn, parsed, available_skills=available_skills)
        except Exception:
            # Isolation boundary (ERR-01): one unreadable/malformed target
            # (e.g. a filesystem error surfacing after discovery) must not
            # abort the rest of the batch.
            n_skipped += 1
            logger.warning(
                "Skipping ingest target %s due to an error", _target_path(target), exc_info=True
            )
            continue
        n_ingested += 1

    if n_skipped:
        logger.warning(
            "ingest_all completed with %d of %d targets skipped due to errors",
            n_skipped,
            len(targets),
        )

    return IngestSummary(n_ingested=n_ingested)


def _target_path(target: IngestTarget) -> Path:
    return target.path if isinstance(target, MainSessionFile) else target.jsonl_path


def _parse_ingest_target(
    target: IngestTarget,
    parent_task_maps: dict[Path, dict[str, str]],
) -> ParsedSession:
    if isinstance(target, MainSessionFile):
        return parse_main_session(target.path, session_id=target.session_id)

    meta = _read_meta(target.meta_path) if target.meta_path is not None else None
    parent_path = target.project_dir / f"{target.parent_session_id}.jsonl"
    if parent_path not in parent_task_maps:
        parent_records = read_jsonl_records(parent_path) if parent_path.is_file() else []
        parent_task_maps[parent_path] = extract_task_subagent_types(parent_records)

    return parse_subagent_run(
        target.jsonl_path,
        agent_id=target.agent_id,
        parent_session_id=target.parent_session_id,
        meta=meta,
        parent_task_subagent_types=parent_task_maps[parent_path],
    )
