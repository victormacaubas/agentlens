import json
import logging
import secrets
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import cast

import click

from agentlens.cli.exit_codes import EXIT_OK, exit_code_for_type
from agentlens.cli.paths import default_claude_root, default_store_path
from agentlens.cli.windows import build_window_selector, build_window_selector_options
from agentlens.core.session import FORMAT_JSON
from agentlens.core.window_scoring import (
    DEFAULT_MAX_RUN_COST_USD,
    WindowScoringContext,
    WindowScoringPreviewRun,
    WindowScoringRun,
)
from agentlens.core.windows import resolve_local_timezone, resolve_window
from agentlens.errors import JudgeError
from agentlens.judge.cli_backend import DEFAULT_SPEND_CEILING_USD, DEFAULT_TIMEOUT_S, ClaudeCliJudge
from agentlens.models.claims import CLAIM_LEASE_MARGIN_S
from agentlens.models.scoring import ScoringRequest, WindowStopReason
from agentlens.models.windows import WindowSelector
from agentlens.render.document import (
    build_window_scoring_document_json,
    build_window_scoring_preview_document_json,
    render_document_json,
)
from agentlens.render.summary import (
    build_window_scoring_preview_summary,
    build_window_scoring_summary,
)
from agentlens.utils.clock import SystemClock

logger = logging.getLogger(__name__)

_SCORING_OWNER_TOKEN_BYTES = 16


@dataclass(frozen=True, slots=True, kw_only=True)
class ScoreArgs:
    selector: WindowSelector
    agent: str | None
    requested_model: str
    max_run_cost_usd: float
    store_path: Path | None
    output_format: str | None
    dry_run: bool


def build_score_command() -> click.Command:
    """Build the command that scores every qualifying spawn in one window."""
    return click.Command(
        name="score",
        callback=_score_callback,
        help="Score every subagent spawn in a resolved window through the judge.",
        params=[
            *build_window_selector_options(action="Score"),
            click.Option(
                ["--agent", "agent"],
                type=str,
                default=None,
                help="Restrict scoring to spawns of one agent type.",
            ),
            click.Option(
                ["--judge-model", "requested_model"],
                type=str,
                default="sonnet",
                help="The model alias or id to request each verdict from.",
            ),
            click.Option(
                ["--max-run-cost-usd", "max_run_cost_usd"],
                type=float,
                default=DEFAULT_MAX_RUN_COST_USD,
                help="Stop starting further judge calls once the run's accrued spend "
                "reaches this ceiling.",
            ),
            click.Option(
                ["--store", "store_path"],
                type=click.Path(path_type=Path),
                default=None,
                help="Override the default store location.",
            ),
            click.Option(
                ["--format", "output_format"],
                type=click.Choice([FORMAT_JSON]),
                default=None,
                help="Emit the run's outcome as JSON instead of a terminal summary.",
            ),
            click.Option(
                ["--dryrun", "dry_run"],
                is_flag=True,
                default=False,
                help="Preview the run's scope and an upper bound on its cost "
                "without calling the judge or writing a verdict or claim.",
            ),
        ],
    )


def parse_score_args(argv: Sequence[str]) -> ScoreArgs:
    """Parse the ``score`` subcommand's arguments."""
    if not argv or argv[0] != "score":
        raise click.UsageError("expected the 'score' subcommand")
    command = build_score_command()
    context = command.make_context("agentlens score", list(argv[1:]))
    return _score_args_from_params(context.params)


def _score_callback(
    *,
    since_duration: str | None,
    named_window: str | None,
    range_from: str | None,
    range_to: str | None,
    agent: str | None,
    requested_model: str,
    max_run_cost_usd: float,
    store_path: Path | None,
    output_format: str | None,
    dry_run: bool,
) -> int:
    return _run_score(
        _score_args_from_params(
            {
                "since_duration": since_duration,
                "named_window": named_window,
                "range_from": range_from,
                "range_to": range_to,
                "agent": agent,
                "requested_model": requested_model,
                "max_run_cost_usd": max_run_cost_usd,
                "store_path": store_path,
                "output_format": output_format,
                "dry_run": dry_run,
            }
        )
    )


def _score_args_from_params(params: Mapping[str, object]) -> ScoreArgs:
    return ScoreArgs(
        selector=build_window_selector(params),
        agent=cast("str | None", params["agent"]),
        requested_model=cast(str, params["requested_model"]),
        max_run_cost_usd=cast(float, params["max_run_cost_usd"]),
        store_path=cast("Path | None", params["store_path"]),
        output_format=cast("str | None", params["output_format"]),
        dry_run=cast(bool, params["dry_run"]),
    )


def _run_score(args: ScoreArgs) -> int:
    store_path = args.store_path if args.store_path is not None else default_store_path()
    claude_root = default_claude_root()
    clock = SystemClock()
    local_timezone = resolve_local_timezone(clock=clock)
    window = resolve_window(args.selector, clock=clock, local_timezone=local_timezone)
    scoring_owner = secrets.token_urlsafe(_SCORING_OWNER_TOKEN_BYTES)
    request = ScoringRequest(
        requested_model=args.requested_model,
        owner=scoring_owner,
        claim_lease=timedelta(seconds=DEFAULT_TIMEOUT_S + CLAIM_LEASE_MARGIN_S),
    )
    context = WindowScoringContext(
        projects_root=claude_root / "projects",
        claude_root=claude_root,
        store_path=store_path,
        clock=clock,
        request=request,
        agent_type=args.agent,
        window=window,
    )
    logger.info(
        "Resolved score arguments: %s",
        json.dumps(
            {
                "selector": {
                    "since_duration": args.selector.since_duration,
                    "named_window": args.selector.named_window,
                    "range_from": args.selector.range_from,
                    "range_to": args.selector.range_to,
                },
                "current_start": window.current_start.isoformat(),
                "current_end": window.current_end.isoformat(),
                "agent": args.agent,
                "requested_model": args.requested_model,
                "max_run_cost_usd": args.max_run_cost_usd,
                "store": str(store_path),
                "format": args.output_format,
                "dry_run": args.dry_run,
                "owner": scoring_owner,
            }
        ),
    )

    if args.dry_run:
        preview = WindowScoringPreviewRun(
            context=context,
            per_call_cost_usd_bound=DEFAULT_SPEND_CEILING_USD,
            max_run_cost_usd=args.max_run_cost_usd,
        ).preview()
        if args.output_format == FORMAT_JSON:
            print(render_document_json(build_window_scoring_preview_document_json(preview)))
        else:
            print(build_window_scoring_preview_summary(preview))
        return EXIT_OK

    outcome = WindowScoringRun(
        context=context,
        judge=ClaudeCliJudge(timeout_s=DEFAULT_TIMEOUT_S),
        max_run_cost_usd=args.max_run_cost_usd,
    ).score()
    if args.output_format == FORMAT_JSON:
        print(render_document_json(build_window_scoring_document_json(outcome)))
    else:
        print(build_window_scoring_summary(outcome))
    if outcome.stop_reason is WindowStopReason.JUDGE_UNUSABLE:
        return exit_code_for_type(JudgeError)
    return EXIT_OK
