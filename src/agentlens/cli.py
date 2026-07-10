from __future__ import annotations

from pathlib import Path

import click

from agentlens import __version__
from agentlens.ingest import ingest_target, resolve_target, sync_agent_definitions
from agentlens.store import create_store, resolve_store_path, upsert_session_events


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

    conn = create_store(store_path)
    try:
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


if __name__ == "__main__":
    main()
