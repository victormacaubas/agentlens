from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import click

from agentlens import __version__
from agentlens.discovery import (
    AgentDefFile,
    MainSessionFile,
    SubagentRun,
    discover_agent_defs,
    discover_main_sessions,
    discover_subagent_runs,
)
from agentlens.parser import (
    ParsedSession,
    parse_agent_definition,
    parse_main_session,
    parse_subagent_run,
    read_jsonl_records,
)
from agentlens.store import (
    create_store,
    resolve_store_path,
    upsert_agent_definition,
    upsert_session_events,
)

IngestTarget = MainSessionFile | SubagentRun


@click.group()
@click.option(
    "--store",
    "store_path",
    type=click.Path(path_type=Path),
    default=None,
    help=(
        "Override the store location "
        "(default: ~/.cache/agentlens/agentlens.db, or $AGENTLENS_STORE)."
    ),
)
@click.version_option(__version__, prog_name="agentlens")
@click.pass_context
def main(ctx: click.Context, store_path: Path | None) -> None:
    """Analyze, score, and improve Claude Code subagents from session logs."""
    ctx.ensure_object(dict)
    ctx.obj["store"] = store_path


@main.command()
@click.argument("session_id", required=False, default=None)
@click.option(
    "--file",
    "file_path",
    type=click.Path(path_type=Path),
    default=None,
    help="Path to a session or subagent JSONL transcript.",
)
@click.pass_context
def session(ctx: click.Context, session_id: str | None, file_path: Path | None) -> None:
    """Ingest a session (by id or --file) into the store."""
    store_path = resolve_store_path(store_override=ctx.obj.get("store"))
    claude_home = Path.home() / ".claude"

    conn = create_store(store_path)
    try:
        _sync_agent_definitions(conn, claude_home=claude_home)

        if file_path is None and session_id is None:
            click.echo(f"store ready at {store_path} (no target given; nothing to ingest)")
            return

        if file_path is not None and not file_path.exists():
            raise click.ClickException(f"file not found: {file_path}")

        target = _resolve_target(
            file_path=file_path, session_id=session_id, claude_home=claude_home
        )
        if target is None:
            raise click.ClickException(f"could not find a session for {file_path or session_id}")

        parsed = _ingest(conn, target)
        upsert_session_events(conn, parsed.session_id, parsed.events)
        click.echo(
            f"ingested {parsed.session_kind} session {parsed.session_id} "
            f"({len(parsed.events)} tool events, name_source={parsed.name_source})"
        )
    finally:
        conn.close()


@main.command()
@click.option("--agent", default=None, help="Filter by agent_type (aggregation lands in Phase 2).")
@click.option("--since", default=None, help="Window start, e.g. 7d (aggregation lands in Phase 2).")
def report(agent: str | None, since: str | None) -> None:
    """Aggregate rollup across sessions (stub — full aggregation is Phase 2)."""
    click.echo("report: aggregation not yet implemented (Phase 2)")


def _resolve_target(
    *,
    file_path: Path | None,
    session_id: str | None,
    claude_home: Path,
) -> IngestTarget | None:
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


def _ingest(conn: sqlite3.Connection, target: IngestTarget) -> ParsedSession:
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


def _sync_agent_definitions(
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


if __name__ == "__main__":
    main()
