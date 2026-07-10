from __future__ import annotations

import json
from contextlib import closing
from pathlib import Path

import click

from agentlens import __version__
from agentlens.discovery.filesystem import discover_available_skills
from agentlens.errors import WindowResolutionError
from agentlens.ingest.orchestrator import (
    ingest_all,
    ingest_target,
    persist_parsed_session,
    resolve_target,
    sync_agent_definitions,
)
from agentlens.reporting.date_window import resolve_window
from agentlens.reporting.queries import (
    DEFAULT_MIN_SESSIONS_FOR_TREND,
    build_report,
)
from agentlens.reporting.rendering import render_terminal_summary
from agentlens.store.schema import create_store, resolve_store_path


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
@click.option(
    "--claude-home",
    "claude_home_override",
    type=click.Path(path_type=Path),
    default=None,
    help="Override the .claude directory to scan (default: ~/.claude).",
)
@click.pass_context
def session(
    ctx: click.Context,
    session_id: str | None,
    file_path: Path | None,
    claude_home_override: Path | None,
) -> None:
    """Ingest a session (by id or --file) into the store."""
    store_path = resolve_store_path(store_override=ctx.obj.get("store"))
    claude_home = claude_home_override or Path.home() / ".claude"

    with closing(create_store(store_path)) as conn:
        sync_agent_definitions(conn, claude_home=claude_home)

        if file_path is None and session_id is None:
            click.echo(f"store ready at {store_path} (no target given; nothing to ingest)")
            return

        if file_path is not None and not file_path.exists():
            raise click.ClickException(f"file not found: {file_path}")

        target = resolve_target(file_path=file_path, session_id=session_id, claude_home=claude_home)
        if target is None:
            raise click.ClickException(f"could not find a session for {file_path or session_id}")

        parsed = ingest_target(target)
        available_skills = discover_available_skills(claude_home)
        persist_parsed_session(conn, parsed, available_skills=available_skills)
        click.echo(
            f"ingested {parsed.session_kind} session {parsed.session_id} "
            f"({len(parsed.events)} tool events, name_source={parsed.name_source})"
        )


@main.command()
@click.option(
    "--claude-home",
    "claude_home_override",
    type=click.Path(path_type=Path),
    default=None,
    help="Override the .claude directory to scan (default: ~/.claude).",
)
@click.option(
    "--limit",
    type=int,
    default=None,
    help="Ingest at most N sessions in this invocation.",
)
@click.pass_context
def ingest(
    ctx: click.Context,
    claude_home_override: Path | None,
    limit: int | None,
) -> None:
    """Walk projects/** and upsert every discovered session into the store."""
    store_path = resolve_store_path(store_override=ctx.obj.get("store"))
    claude_home = claude_home_override or Path.home() / ".claude"

    with closing(create_store(store_path)) as conn:
        sync_agent_definitions(conn, claude_home=claude_home)
        summary = ingest_all(conn, claude_home=claude_home, limit=limit)
        click.echo(f"ingested {summary.n_ingested} sessions from {claude_home}")


@main.command()
@click.option("--agent", "agent_type", default=None, help="Filter by agent_type.")
@click.option("--since", default=None, help="Window start: 7d, 30d, or an absolute date.")
@click.option("--from", "from_", default=None, help="Explicit window start date (with --to).")
@click.option("--to", default=None, help="Explicit window end date (with --from).")
@click.option("--today", is_flag=True, default=False, help="Shortcut for --since 1d.")
@click.option("--json", "as_json", is_flag=True, default=False, help="Emit the verdict-JSON slice.")
@click.pass_context
def report(
    ctx: click.Context,
    agent_type: str | None,
    since: str | None,
    from_: str | None,
    to: str | None,
    today: bool,
    as_json: bool,
) -> None:
    """Aggregate deterministic counts over the store for a window.

    Reads the store only — never ingests. Defaults to the last 7 days when
    no window flag is given.
    """
    store_path = resolve_store_path(store_override=ctx.obj.get("store"))

    try:
        window = resolve_window(since=since, from_=from_, to=to, today=today)
    except WindowResolutionError as exc:
        raise click.ClickException(str(exc)) from exc

    with closing(create_store(store_path)) as conn:
        result = build_report(
            conn,
            window=window,
            agent_type=agent_type,
            min_sessions_for_trend=DEFAULT_MIN_SESSIONS_FOR_TREND,
        )
        if as_json:
            click.echo(json.dumps(result.to_verdict_slice()))
        else:
            click.echo(render_terminal_summary(result))


if __name__ == "__main__":
    main()
