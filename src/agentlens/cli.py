from __future__ import annotations

import json
import shutil
from contextlib import closing
from pathlib import Path
from typing import Final

import click

from agentlens import __version__
from agentlens.discovery.filesystem import (
    discover_available_skills,
    discover_main_sessions,
    discover_subagent_runs,
)
from agentlens.errors import WindowResolutionError
from agentlens.ingest.orchestrator import (
    ingest_all,
    ingest_target,
    persist_parsed_session,
    resolve_target,
    sync_agent_definitions,
)
from agentlens.judge.claude_cli import ClaudeCliJudge
from agentlens.judge.rubric import RUBRIC_VERSION
from agentlens.judge.scoring import ProgressEvent, ScoringLoop
from agentlens.reporting.date_window import resolve_window
from agentlens.reporting.queries import (
    DEFAULT_MIN_SESSIONS_FOR_TREND,
    build_report,
)
from agentlens.reporting.rendering import render_terminal_summary
from agentlens.store.schema import create_store, resolve_store_path

CLAUDE_EXECUTABLE: Final[str] = "claude"

# Conservative, hardcoded per-session cost estimates — used only
# to show the user a ballpark before the cost confirmation gate; the actual
# cost comes from `ScoringResult.total_cost_usd` after scoring.
PER_SESSION_COST_ESTIMATE: Final[dict[str, float]] = {
    "sonnet": 0.025,
    "opus": 0.15,
}
DEFAULT_PER_SESSION_COST: Final[float] = 0.05


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
@click.option("--since", default=None, help="Window start: 7d, 30d, or an absolute date.")
@click.option("--from", "from_", default=None, help="Explicit window start date (with --to).")
@click.option("--to", default=None, help="Explicit window end date (with --from).")
@click.option("--today", is_flag=True, default=False, help="Shortcut for --since 1d.")
@click.option("--agent", "agent_type", default=None, help="Filter by agent_type.")
@click.option(
    "--judge-model",
    "judge_model",
    default="sonnet",
    help="Model the judge backend scores with (default: sonnet).",
)
@click.option(
    "--max-sessions",
    "max_sessions",
    type=int,
    default=None,
    help="Cap the number of sessions scored in this invocation.",
)
@click.option(
    "--no-confirm",
    "no_confirm",
    is_flag=True,
    default=False,
    help="Skip the cost confirmation prompt.",
)
@click.option(
    "--dry-run",
    "dry_run",
    is_flag=True,
    default=False,
    help="List unscored sessions and estimated cost without calling the judge.",
)
@click.option(
    "--claude-home",
    "claude_home_override",
    type=click.Path(path_type=Path),
    default=None,
    help="Override the .claude directory to scan for transcripts (default: ~/.claude).",
)
@click.pass_context
def score(
    ctx: click.Context,
    since: str | None,
    from_: str | None,
    to: str | None,
    today: bool,
    agent_type: str | None,
    judge_model: str,
    max_sessions: int | None,
    no_confirm: bool,
    dry_run: bool,
    claude_home_override: Path | None,
) -> None:
    """Score unscored subagent sessions in a window against the judge rubric.

    Finds subagent sessions in the window missing a verdict for the current
    `(rubric_version, judge_model)`, scores them via the judge backend, and
    persists verdicts into `fact_verdict`. Reads and writes the store only —
    never ingests: run `agentlens ingest` first.
    """
    store_path = resolve_store_path(store_override=ctx.obj.get("store"))
    claude_home = claude_home_override or Path.home() / ".claude"

    try:
        window = resolve_window(since=since, from_=from_, to=to, today=today)
    except WindowResolutionError as exc:
        raise click.ClickException(str(exc)) from exc

    with closing(create_store(store_path)) as conn:
        loop = ScoringLoop(
            judge=ClaudeCliJudge(model=judge_model),
            conn=conn,
            rubric_version=RUBRIC_VERSION,
            judge_model=judge_model,
        )
        # Fetch the full unscored set (uncapped) so the max-sessions summary
        # can report "scored/total" — `--max-sessions` capping happens below
        # in Python, not via the loop's own (also-supported) SQL LIMIT.
        all_unscored = loop.find_unscored_sessions(window=window, agent_type=agent_type)
        total_unscored = len(all_unscored)

        if total_unscored == 0:
            click.echo("all sessions already scored")
            return

        capped = max_sessions is not None and max_sessions < total_unscored
        sessions_to_score = (
            all_unscored[:max_sessions] if max_sessions is not None else all_unscored
        )
        n_to_score = len(sessions_to_score)
        estimated_cost = _estimate_judge_cost(n_to_score, judge_model)

        if dry_run:
            for record in sessions_to_score:
                click.echo(f"{record.agent_type}\t{record.task_description}")
            click.echo(f"estimated cost: ~${estimated_cost:.2f} for {n_to_score} sessions")
            return

        if not no_confirm:
            proceed = click.confirm(
                f"Will score {n_to_score} sessions with {judge_model} "
                f"(est. ~${estimated_cost:.2f}). Proceed?",
                default=True,
            )
            if not proceed:
                return

        if shutil.which(CLAUDE_EXECUTABLE) is None:
            raise click.ClickException(
                f"{CLAUDE_EXECUTABLE!r} was not found on PATH; "
                "install and authenticate it before running `agentlens score`."
            )

        def _on_progress(event: ProgressEvent) -> None:
            label = event.session.agent_type or "unknown"
            desc = (event.session.task_description or "")[:40]
            idx = f"[{event.index + 1}/{event.total}]"
            if event.verdict:
                click.echo(
                    f"  {idx} {label} \"{desc}\" ... "
                    f"scored ({event.verdict.overall_score:.1f}/5, "
                    f"${event.verdict.judge_cost_usd:.3f})",
                    err=True,
                )
            else:
                click.echo(
                    f"  {idx} {label} \"{desc}\" ... ERROR, skipped",
                    err=True,
                )

        jsonl_paths = _discover_jsonl_paths(claude_home)
        result = loop.run(
            sessions_to_score, jsonl_paths=jsonl_paths, on_progress=_on_progress
        )

        if capped:
            click.echo(
                f"{result.scored}/{total_unscored} scored (--max-sessions reached). "
                "Re-run to continue.",
                err=True,
            )
            return

        summary = (
            f"Scored {result.scored}/{n_to_score} sessions. "
            f"Total judge cost: ${result.total_cost_usd:.2f}."
        )
        if result.skipped:
            summary += f" {result.skipped} skipped (re-run to retry)."
        if result.aborted:
            summary += " Aborted early after repeated judge failures."
        click.echo(summary, err=True)


def _estimate_judge_cost(n_sessions: int, judge_model: str) -> float:
    """Ballpark cost for scoring `n_sessions` with `judge_model`.

    Uses a conservative hardcoded per-session estimate; the real cost
    reported after scoring comes from the judge backend's own usage data.
    """
    per_session = PER_SESSION_COST_ESTIMATE.get(judge_model, DEFAULT_PER_SESSION_COST)
    return n_sessions * per_session


def _discover_jsonl_paths(claude_home: Path) -> dict[str, Path]:
    """Map each session's store key to its transcript path.

    Subagent `fact_session` rows are keyed by `agent_id`; main-session
    rows are keyed by `session_id`. Both live under the same `session_id`
    column in the store, so this maps `agent_id -> jsonl_path` for subagent
    runs and `session_id -> jsonl_path` for main sessions into one dict.
    """
    projects_root = claude_home / "projects"
    jsonl_paths: dict[str, Path] = {}
    for run in discover_subagent_runs(projects_root):
        jsonl_paths[run.agent_id] = run.jsonl_path
    for msf in discover_main_sessions(projects_root):
        jsonl_paths[msf.session_id] = msf.path
    return jsonl_paths


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
