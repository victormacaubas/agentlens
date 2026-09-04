import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import click

from agentlens.cli.exit_codes import EXIT_OK
from agentlens.cli.paths import default_claude_root, default_store_path
from agentlens.cli.windows import build_window_selector, build_window_selector_options
from agentlens.core.report import generate_report
from agentlens.core.session import FORMAT_JSON
from agentlens.core.windows import resolve_local_timezone, resolve_window
from agentlens.models.windows import WindowSelector
from agentlens.utils.clock import SystemClock

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True, kw_only=True)
class ReportArgs:
    selector: WindowSelector
    agent: str | None
    output_format: str | None
    store_path: Path | None
    dry_run: bool


def build_report_command() -> click.Command:
    """Build the command that reports deterministic facts over a window."""
    return click.Command(
        name="report",
        callback=_report_callback,
        help="Report deterministic subagent-spawn facts over a resolved window.",
        params=[
            *build_window_selector_options(action="Report"),
            click.Option(
                ["--agent", "agent"],
                type=str,
                default=None,
                help="Restrict the report to spawns of one agent type.",
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
                help="Emit the JSON document to standard output instead of an artifact file.",
            ),
            click.Option(
                ["--dryrun", "dry_run"],
                is_flag=True,
                default=False,
                help="Compute the report without writing the store or a report artifact.",
            ),
        ],
    )


def parse_report_args(argv: Sequence[str]) -> ReportArgs:
    """Parse the ``report`` subcommand's arguments."""
    if not argv or argv[0] != "report":
        raise click.UsageError("expected the 'report' subcommand")
    command = build_report_command()
    context = command.make_context("agentlens report", list(argv[1:]))
    return _report_args_from_params(context.params)


def _report_callback(
    *,
    since_duration: str | None,
    named_window: str | None,
    range_from: str | None,
    range_to: str | None,
    agent: str | None,
    store_path: Path | None,
    output_format: str | None,
    dry_run: bool,
) -> int:
    return _run_report(
        _report_args_from_params(
            {
                "since_duration": since_duration,
                "named_window": named_window,
                "range_from": range_from,
                "range_to": range_to,
                "agent": agent,
                "store_path": store_path,
                "output_format": output_format,
                "dry_run": dry_run,
            }
        )
    )


def _report_args_from_params(params: Mapping[str, object]) -> ReportArgs:
    return ReportArgs(
        selector=build_window_selector(params),
        agent=cast("str | None", params["agent"]),
        output_format=cast("str | None", params["output_format"]),
        store_path=cast("Path | None", params["store_path"]),
        dry_run=cast(bool, params["dry_run"]),
    )


def _run_report(args: ReportArgs) -> int:
    store_path = args.store_path if args.store_path is not None else default_store_path()
    claude_root = default_claude_root()
    clock = SystemClock()
    local_timezone = resolve_local_timezone(clock=clock)
    window = resolve_window(args.selector, clock=clock, local_timezone=local_timezone)
    logger.info(
        "Resolved report arguments: %s",
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
                "prior_start": window.prior_start.isoformat(),
                "prior_end": window.prior_end.isoformat(),
                "agent": args.agent,
                "store": str(store_path),
                "format": args.output_format,
                "dry_run": args.dry_run,
            }
        ),
    )
    output = generate_report(
        window=window,
        agent_filter=args.agent,
        store_path=store_path,
        claude_root=claude_root,
        output_format=args.output_format,
        clock=clock,
        dry_run=args.dry_run,
    )
    print(output)
    return EXIT_OK
