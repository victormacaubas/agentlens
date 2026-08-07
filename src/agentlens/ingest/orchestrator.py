from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, replace
from itertools import chain, islice
from pathlib import Path
from typing import Any

from agentlens.aggregation.derivation import derive_fact_session, derive_skill_bridge
from agentlens.discovery.filesystem import (
    discover_agent_defs,
    discover_available_skills,
    discover_main_sessions,
    discover_subagent_runs,
)
from agentlens.discovery.models import (
    MainSessionFile,
    SourceIdentity,
    SubagentRun,
)
from agentlens.errors import SessionLookupAmbiguityError
from agentlens.parser.extraction import consume_jsonl_records, extract_task_subagent_types
from agentlens.parser.session import (
    ParsedSession,
    parse_agent_definition,
    parse_main_session,
    parse_subagent_run,
)
from agentlens.store.operations import (
    resolve_session_agent_definition,
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
    n_degraded: int = 0
    failed_paths: tuple[str, ...] = ()
    discovery_failed_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class DefinitionSyncSummary:
    """Result of scanning one user or project definition context."""

    n_synced: int
    failed_paths: tuple[str, ...] = ()


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
        self._synced_project_definition_contexts: set[tuple[str, Path]] = set()

    def run(self, *, limit: int | None = None) -> IngestSummary:
        """Walk `projects/**`, parse every discovered session, and upsert the
        full grain for each. Idempotent by `session_id`.

        Each parent transcript is read and run through
        `extract_task_subagent_types` at most once per run, and that map is
        reused across every sibling spawn under the same parent.
        """
        projects_root = self.claude_home / "projects"
        failed_paths: list[str] = []
        discovery_failed_paths: list[str] = []

        def record_discovery_error(path: Path, error: OSError) -> None:
            failed_path = str(path)
            failed_paths.append(failed_path)
            discovery_failed_paths.append(failed_path)
            logger.warning("Discovery failed for %s: %s", path, error)

        targets: Iterable[IngestTarget] = chain(
            discover_main_sessions(projects_root, on_error=record_discovery_error),
            discover_subagent_runs(projects_root, on_error=record_discovery_error),
        )
        if limit is not None:
            targets = islice(targets, limit)

        n_ingested = 0
        n_skipped = 0
        n_degraded = 0
        n_targets = 0
        for target in targets:
            n_targets += 1
            try:
                parsed = self._parse_target(target)
                if not parsed.parse_health.complete:
                    n_skipped += 1
                    n_degraded += 1
                    failed_paths.append(str(_target_path(target)))
                    logger.warning(
                        "Skipping degraded ingest target %s",
                        _target_path(target),
                    )
                    continue
                definition_summary = self._sync_project_definitions(parsed)
                for failed_path in definition_summary.failed_paths:
                    if failed_path not in failed_paths:
                        failed_paths.append(failed_path)
                    if failed_path not in discovery_failed_paths:
                        discovery_failed_paths.append(failed_path)
                persisted = persist_parsed_session(
                    self.conn, parsed, available_skills=self.available_skills
                )
            except Exception:
                n_skipped += 1
                failed_paths.append(str(_target_path(target)))
                logger.warning(
                    "Skipping ingest target %s due to an error",
                    _target_path(target),
                    exc_info=True,
                )
                continue
            if persisted:
                n_ingested += 1
            else:
                n_skipped += 1
                failed_paths.append(str(_target_path(target)))

        if n_skipped:
            logger.warning(
                "ingest_all completed with %d of %d targets skipped due to errors",
                n_skipped,
                n_targets,
            )

        return IngestSummary(
            n_ingested=n_ingested,
            n_skipped=n_skipped,
            n_degraded=n_degraded,
            failed_paths=tuple(failed_paths),
            discovery_failed_paths=tuple(discovery_failed_paths),
        )

    def _parse_target(self, target: IngestTarget) -> ParsedSession:
        if isinstance(target, MainSessionFile):
            return parse_main_session(
                target.path,
                session_id=target.raw_session_id,
                source_project=target.source_project,
            )

        parent_path = target.project_dir / f"{target.raw_parent_session_id}.jsonl"
        if parent_path not in self._parent_task_maps:
            self._parent_task_maps[parent_path] = _read_parent_task_map(parent_path)
        return _parse_subagent_run(
            target, parent_task_subagent_types=self._parent_task_maps[parent_path]
        )

    def _sync_project_definitions(self, parsed: ParsedSession) -> DefinitionSyncSummary:
        if parsed.project_root is None:
            return DefinitionSyncSummary(n_synced=0)

        project_root = parsed.project_root.resolve(strict=False)
        context = (parsed.source_project, project_root)
        if context in self._synced_project_definition_contexts:
            return DefinitionSyncSummary(n_synced=0)
        self._synced_project_definition_contexts.add(context)

        return sync_agent_definitions(
            self.conn,
            claude_home=self.claude_home,
            project_claude_dir=project_root / ".claude",
            source_project=parsed.source_project,
            include_user=False,
        )


def resolve_target(
    *,
    file_path: Path | None,
    session_id: str | None,
    claude_home: Path,
) -> IngestTarget | None:
    """Resolve a CLI-supplied `--file`/session_id pair to a concrete ingest target."""
    if file_path is not None:
        return _target_from_file(
            file_path,
            projects_root=claude_home / "projects",
        )
    if session_id is not None:
        return _find_target_by_session_id(session_id, claude_home)
    return None


def _target_from_file(path: Path, *, projects_root: Path) -> IngestTarget:
    if path.parent.name == "subagents" and path.name.startswith("agent-"):
        agent_id = path.stem.removeprefix("agent-")
        meta_path = path.with_name(f"{path.stem}.meta.json")
        project_dir = path.parent.parent.parent
        source_project = _source_project_for_path(project_dir, projects_root)
        raw_parent_session_id = path.parent.parent.name
        return SubagentRun(
            jsonl_path=path,
            meta_path=meta_path if meta_path.is_file() else None,
            agent_id=agent_id,
            session_id=SourceIdentity(
                source_project, "subagent", agent_id
            ).session_id,
            parent_session_id=SourceIdentity(
                source_project, "main", raw_parent_session_id
            ).session_id,
            raw_parent_session_id=raw_parent_session_id,
            source_project=source_project,
            project_dir=project_dir,
        )
    source_project = _source_project_for_path(path.parent, projects_root)
    identity = SourceIdentity(source_project, "main", path.stem)
    return MainSessionFile(
        path=path,
        session_id=identity.session_id,
        raw_session_id=path.stem,
        source_project=source_project,
        project_dir=path.parent,
    )


def _find_target_by_session_id(session_id: str, claude_home: Path) -> IngestTarget | None:
    projects_root = claude_home / "projects"
    matches: list[IngestTarget] = []
    for main_session in discover_main_sessions(projects_root):
        if main_session.raw_session_id == session_id:
            matches.append(main_session)
    for subagent_run in discover_subagent_runs(projects_root):
        if subagent_run.agent_id == session_id:
            matches.append(subagent_run)
    if len(matches) > 1:
        qualifiers = ", ".join(
            sorted(
                f"{target.source_project}/{_target_kind(target)}"
                for target in matches
            )
        )
        raise SessionLookupAmbiguityError(
            f"raw session ID {session_id!r} is ambiguous; matches: {qualifiers}"
        )
    return matches[0] if matches else None


def ingest_target(target: IngestTarget) -> ParsedSession:
    """Parse a resolved target into a `ParsedSession` (no store writes)."""
    if isinstance(target, MainSessionFile):
        return parse_main_session(
            target.path,
            session_id=target.raw_session_id,
            source_project=target.source_project,
        )

    parent_path = target.project_dir / f"{target.raw_parent_session_id}.jsonl"
    return _parse_subagent_run(
        target, parent_task_subagent_types=_read_parent_task_map(parent_path)
    )


def _read_parent_task_map(parent_path: Path) -> dict[str, str]:
    """Read the parent transcript's `{tool_use_id: subagent_type}` map.

    Empty when the parent transcript is missing — name resolution still
    proceeds via the remaining fallbacks and never drops a session.
    """
    if not parent_path.is_file():
        return {}
    return consume_jsonl_records(parent_path, extract_task_subagent_types).value


def _parse_subagent_run(
    target: SubagentRun,
    *,
    parent_task_subagent_types: dict[str, str],
) -> ParsedSession:
    """Parse one `SubagentRun` into a `ParsedSession`, reading its sidecar.

    Shared by the single-target (`ingest_target`) and bulk (`IngestRunner`)
    paths; they differ only in how the parent task map is sourced (fresh
    read vs. per-parent cache).
    """
    meta = _read_meta(target.meta_path) if target.meta_path is not None else None
    return parse_subagent_run(
        target.jsonl_path,
        agent_id=target.agent_id,
        parent_session_id=target.raw_parent_session_id,
        source_project=target.source_project,
        meta=meta,
        parent_task_subagent_types=parent_task_subagent_types,
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
    source_project: str | None = None,
    include_user: bool = True,
) -> DefinitionSyncSummary:
    """Scan one definition context and report filesystem failures."""
    failed_paths: list[str] = []

    def record_error(path: Path, error: OSError) -> None:
        failed_path = str(path)
        if failed_path not in failed_paths:
            failed_paths.append(failed_path)
        logger.warning("Definition discovery failed for %s: %s", path, error)

    n_synced = 0
    for agent_def_file in discover_agent_defs(
        claude_home=claude_home,
        project_claude_dir=project_claude_dir,
        source_project=source_project,
        include_user=include_user,
        on_error=record_error,
    ):
        record = parse_agent_definition(
            agent_def_file.path,
            scope=agent_def_file.scope,
            source_project=agent_def_file.source_project,
            on_error=record_error,
        )
        if record is not None:
            upsert_agent_definition(conn, record)
            n_synced += 1
    return DefinitionSyncSummary(
        n_synced=n_synced,
        failed_paths=tuple(failed_paths),
    )


def persist_parsed_session(
    conn: sqlite3.Connection,
    parsed: ParsedSession,
    *,
    available_skills: Iterable[str] = (),
) -> bool:
    """Derive and upsert the full session grain for one `ParsedSession`.

    Covers `fact_session`, `fact_tool_event`, and `bridge_session_skill`,
    plus `dim_date` / `dim_tool` backfill.
    """
    if not parsed.parse_health.complete or not _source_is_current(parsed):
        return False

    effective_definition = (
        resolve_session_agent_definition(
            conn,
            session_id=parsed.session_id,
            source_revision=parsed.source_revision.identity,
            agent_type=parsed.name,
            source_project=parsed.source_project,
        )
        if parsed.name
        else None
    )
    bound = replace(
        parsed,
        agent_definition_id=(
            effective_definition.effective_definition_id
            if effective_definition is not None
            else None
        ),
    )
    declared_skills = (
        list(effective_definition.declared_skills)
        if effective_definition is not None
        else []
    )
    session_record = derive_fact_session(bound)
    skill_records = derive_skill_bridge(
        bound, declared_skills=declared_skills, available_skills=available_skills
    )

    return upsert_session_grain(
        conn,
        record=session_record,
        events=bound.events,
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


def _target_kind(target: IngestTarget) -> str:
    return "main" if isinstance(target, MainSessionFile) else "subagent"


def _source_project_for_path(project_dir: Path, projects_root: Path) -> str:
    try:
        return project_dir.relative_to(projects_root).as_posix()
    except ValueError:
        canonical = project_dir.resolve(strict=False).as_posix()
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return f"external:{digest}"


def _source_is_current(parsed: ParsedSession) -> bool:
    if parsed.source_path is None:
        return True
    try:
        current = parsed.source_path.stat()
    except OSError:
        return False
    return (
        current.st_mtime_ns == parsed.source_revision.mtime_ns
        and current.st_size == parsed.source_revision.size
    )
