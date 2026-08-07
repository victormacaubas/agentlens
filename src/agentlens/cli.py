from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from contextlib import closing
from functools import wraps
from importlib import metadata
from pathlib import Path
from typing import Final, ParamSpec, TypeVar

import click

from agentlens.discovery.filesystem import (
    discover_available_skills,
    discover_main_sessions,
    discover_subagent_runs,
)
from agentlens.errors import (
    JudgeUnavailableError,
    SessionLookupAmbiguityError,
    StoreLocationError,
    StoreSchemaError,
    WindowResolutionError,
)
from agentlens.ingest.orchestrator import (
    ingest_all,
    ingest_target,
    persist_parsed_session,
    resolve_target,
    sync_agent_definitions,
)
from agentlens.judge.claude_cli import ClaudeCliJudge
from agentlens.judge.rubric import RUBRIC_VERSION
from agentlens.judge.scoring import ProgressEvent, ScoringLoop, is_concrete_model_id
from agentlens.reporting.date_window import resolve_window
from agentlens.reporting.queries import (
    DEFAULT_MIN_SESSIONS_FOR_TREND,
    build_report,
)
from agentlens.reporting.rendering import render_terminal_summary
from agentlens.store.schema import assert_readable_schema_version, create_store, resolve_store_path

PER_SESSION_COST_ESTIMATE: Final[dict[str, float]] = {
    "sonnet": 0.15,
    "opus": 0.15,
}
DEFAULT_PER_SESSION_COST: Final[float] = 0.15
P = ParamSpec("P")
R = TypeVar("R")


def _handle_cli_errors(func: Callable[P, R]) -> Callable[P, R]:
    @wraps(func)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return func(*args, **kwargs)
        except (StoreLocationError, StoreSchemaError) as exc:
            raise click.ClickException(str(exc)) from exc
        except JudgeUnavailableError as exc:
            raise click.ClickException(
                f"judge unavailable: {exc}. Install and authenticate the Claude CLI, then retry."
            ) from exc
        except SessionLookupAmbiguityError as exc:
            raise click.ClickException(str(exc)) from exc
        except sqlite3.Error as exc:
            raise click.ClickException(f"store operation failed: {exc}") from exc
        except OSError as exc:
            raise click.ClickException(f"filesystem operation failed: {exc}") from exc

    return wrapped


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
@click.version_option(version=metadata.version("agentlens"), prog_name="agentlens")
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
@_handle_cli_errors
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
        definition_failures = list(
            sync_agent_definitions(conn, claude_home=claude_home).failed_paths
        )

        if file_path is None and session_id is None:
            click.echo(f"store ready at {store_path} (no target given; nothing to ingest)")
            _exit_after_definition_failures(ctx, definition_failures)
            return

        if file_path is not None and not file_path.exists():
            raise click.ClickException(f"file not found: {file_path}")

        target = resolve_target(file_path=file_path, session_id=session_id, claude_home=claude_home)
        if target is None:
            raise click.ClickException(f"could not find a session for {file_path or session_id}")

        parsed = ingest_target(target)
        if parsed.project_root is not None:
            project_summary = sync_agent_definitions(
                conn,
                claude_home=claude_home,
                project_claude_dir=parsed.project_root.resolve(strict=False) / ".claude",
                source_project=parsed.source_project,
                include_user=False,
            )
            definition_failures.extend(project_summary.failed_paths)
        available_skills = discover_available_skills(claude_home)
        persisted = persist_parsed_session(conn, parsed, available_skills=available_skills)
        if not persisted:
            raise click.ClickException(
                f"session {parsed.raw_session_id!r} was not persisted because its source changed "
                "or was incomplete; retry after the transcript is stable"
            )
        click.echo(
            f"ingested {parsed.session_kind} session {parsed.raw_session_id} "
            f"({len(parsed.events)} tool events, name_source={parsed.name_source})"
        )
        _exit_after_definition_failures(ctx, definition_failures)


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
    type=click.IntRange(min=1),
    default=None,
    help="Ingest at most N sessions in this invocation.",
)
@click.pass_context
@_handle_cli_errors
def ingest(
    ctx: click.Context,
    claude_home_override: Path | None,
    limit: int | None,
) -> None:
    """Walk projects/** and upsert every discovered session into the store."""
    store_path = resolve_store_path(store_override=ctx.obj.get("store"))
    claude_home = claude_home_override or Path.home() / ".claude"

    with closing(create_store(store_path)) as conn:
        user_definition_summary = sync_agent_definitions(conn, claude_home=claude_home)
        summary = ingest_all(conn, claude_home=claude_home, limit=limit)
        discovery_failed_paths = _unique_paths(
            (*user_definition_summary.failed_paths, *summary.discovery_failed_paths)
        )
        failed_paths = _unique_paths(
            (*user_definition_summary.failed_paths, *summary.failed_paths)
        )
        click.echo(
            f"Ingested: {summary.n_ingested}. Skipped: {summary.n_skipped}. "
            f"Degraded: {summary.n_degraded}. "
            f"Discovery failures: {len(discovery_failed_paths)}."
        )
        for failed_path in failed_paths:
            click.echo(f"  failed: {failed_path}")
        if summary.n_skipped or discovery_failed_paths:
            ctx.exit(1)


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
    type=click.IntRange(min=1),
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
@_handle_cli_errors
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
            max_sessions=max_sessions,
        )
        count_is_upper_bound = not is_concrete_model_id(judge_model)
        all_unscored = loop.find_unscored_sessions(window=window, agent_type=agent_type)
        total_unscored = len(all_unscored)

        if total_unscored == 0:
            summary = (
                "all sessions already scored. Attempts: 0. Scored: 0. "
                "Skipped: 0. Remaining: 0. Total judge cost: $0.00. Aborted: no."
            )
            summary += _resolved_model_note(judge_model, judge_model)
            click.echo(summary)
            return

        preview_sessions = (
            all_unscored[:max_sessions] if max_sessions is not None else all_unscored
        )
        n_to_score = len(preview_sessions)
        estimated_cost = _estimate_judge_cost(n_to_score, judge_model)
        count_display = f"up to {n_to_score}" if count_is_upper_bound else str(n_to_score)

        if dry_run:
            for record in preview_sessions:
                click.echo(f"{record.agent_type}\t{record.task_description}")
            click.echo(f"estimated cost: ~${estimated_cost:.2f} for {count_display} sessions")
            return

        if not no_confirm:
            proceed = click.confirm(
                f"Will score {count_display} sessions with {judge_model} "
                f"(est. ~${estimated_cost:.2f}). Proceed?",
                default=True,
            )
            if not proceed:
                return

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
        result = loop.score_window(
            window=window,
            agent_type=agent_type,
            jsonl_paths=jsonl_paths,
            on_progress=_on_progress,
        )
        resolved_note = _resolved_model_note(judge_model, result.resolved_model)
        summary = (
            f"Attempts: {result.attempts}. Scored: {result.scored}. "
            f"Skipped: {result.skipped}. Remaining: {result.remaining}. "
            f"Total judge cost: ${result.total_cost_usd:.2f}. "
            f"Aborted: {'yes' if result.aborted else 'no'}."
        )
        summary += resolved_note
        if max_sessions is not None and result.attempts >= max_sessions and result.remaining:
            summary += " --max-sessions reached; re-run to continue."
        click.echo(summary, err=True)
        if result.skipped or result.aborted:
            ctx.exit(1)


def _estimate_judge_cost(n_sessions: int, judge_model: str) -> float:
    """Ballpark cost for scoring `n_sessions` with `judge_model`.

    Uses a conservative hardcoded per-session estimate; the real cost
    reported after scoring comes from the judge backend's own usage data.
    """
    per_session = PER_SESSION_COST_ESTIMATE.get(judge_model, DEFAULT_PER_SESSION_COST)
    return n_sessions * per_session


def _resolved_model_note(judge_model: str, resolved_model: str | None) -> str:
    """Build the summary clause describing configured and concrete model state."""
    if is_concrete_model_id(judge_model):
        return f" Judge model: {judge_model}."
    if resolved_model is None:
        return f" Configured model: {judge_model!r}. Resolved model: unresolved."
    return f" Resolved {judge_model!r} to {resolved_model}."


def _discover_jsonl_paths(claude_home: Path) -> dict[str, Path]:
    """Map each session's store key to its transcript path.

    Both main and subagent records use the qualified source identity stored
    in `fact_session.session_id`; raw IDs are intentionally excluded because
    they can collide across projects and session kinds.
    """
    projects_root = claude_home / "projects"
    jsonl_paths: dict[str, Path] = {}
    for run in discover_subagent_runs(projects_root):
        jsonl_paths[run.session_id] = run.jsonl_path
    for msf in discover_main_sessions(projects_root):
        jsonl_paths[msf.session_id] = msf.path
    return jsonl_paths


@main.command()
@click.option("--agent", "agent_type", default=None, help="Filter by agent_type.")
@click.option("--since", default=None, help="Window start: 7d, 30d, or an absolute date.")
@click.option("--from", "from_", default=None, help="Explicit window start date (with --to).")
@click.option("--to", default=None, help="Explicit window end date (with --from).")
@click.option("--today", is_flag=True, default=False, help="Shortcut for --since 1d.")
@click.option(
    "--rubric-version",
    default=RUBRIC_VERSION,
    show_default=True,
    help="Rubric version for the comparable verdict cohort.",
)
@click.option(
    "--judge-model",
    default=None,
    help="Concrete judge model for the verdict cohort; required when stored models are ambiguous.",
)
@click.option("--json", "as_json", is_flag=True, default=False, help="Emit the verdict-JSON slice.")
@click.pass_context
@_handle_cli_errors
def report(
    ctx: click.Context,
    agent_type: str | None,
    since: str | None,
    from_: str | None,
    to: str | None,
    today: bool,
    rubric_version: str,
    judge_model: str | None,
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

    with closing(_open_report_store_read_only(store_path)) as conn:
        try:
            result = build_report(
                conn,
                window=window,
                agent_type=agent_type,
                min_sessions_for_trend=DEFAULT_MIN_SESSIONS_FOR_TREND,
                rubric_version=rubric_version,
                judge_model=judge_model,
            )
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc
        if as_json:
            click.echo(json.dumps(result.to_verdict_slice()))
        else:
            click.echo(render_terminal_summary(result))


def _open_report_store_read_only(path: Path) -> sqlite3.Connection:
    """Open an existing report store without creating or modifying it."""
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        conn.execute("PRAGMA query_only = ON")
        assert_readable_schema_version(conn, path)
    except BaseException:
        conn.close()
        raise
    return conn


def _unique_paths(paths: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(paths))


def _exit_after_definition_failures(
    ctx: click.Context,
    failed_paths: list[str],
) -> None:
    unique_paths = _unique_paths(tuple(failed_paths))
    if not unique_paths:
        return
    for failed_path in unique_paths:
        click.echo(f"  definition discovery failed: {failed_path}", err=True)
    ctx.exit(1)


if __name__ == "__main__":
    main()
