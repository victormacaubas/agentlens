import json
import logging
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import click

from agentlens.core.report import generate_report
from agentlens.core.session import FORMAT_JSON, analyze_session
from agentlens.core.windows import resolve_local_timezone, resolve_window
from agentlens.errors import (
    AgentlensError,
    ConfigError,
    JudgeError,
    SourceError,
    StoreError,
)
from agentlens.models.windows import NAMED_WINDOW_THIS_WEEK, WindowSelector
from agentlens.utils.clock import SystemClock

logger = logging.getLogger(__name__)

_PACKAGE_LOGGER_NAME = "agentlens"
_CLI_LOG_HANDLER_ATTR = "_agentlens_cli_owned"

EXIT_OK = 0
EXIT_UNEXPECTED = 1  # escaped the taxonomy: a bug, not a handled condition
EXIT_CODES: dict[type[AgentlensError], int] = {
    ConfigError: 2,
    SourceError: 3,
    StoreError: 4,
    JudgeError: 5,
}
SESSION_SUBCOMMAND = "session"
REPORT_SUBCOMMAND = "report"
_STORE_DB_FILENAME = "agentlens.db"


@dataclass(frozen=True, slots=True, kw_only=True)
class SessionArgs:
    """The parsed arguments for the ``session`` subcommand."""

    file_path: Path
    output_format: str | None
    store_path: Path | None
    dry_run: bool


def default_store_path() -> Path:
    """Return the store's default location, under the user's cache directory."""
    return Path(click.get_app_dir("agentlens")) / _STORE_DB_FILENAME


def default_claude_root() -> Path:
    """Return the default location of the user's ``.claude`` directory."""
    return Path.home() / ".claude"


def _exit_code_for(exc: AgentlensError) -> int:
    for ancestor in type(exc).__mro__:
        code = EXIT_CODES.get(ancestor)
        if code is not None:
            return code
    return EXIT_UNEXPECTED


def _run_session(args: SessionArgs) -> int:
    """Resolve concrete collaborators for one ``session`` run and execute it.

    Logs the resolved arguments once at INFO before delegating to
    :func:`agentlens.core.session.analyze_session`, then prints that
    workflow's returned document or summary.

    Raises:
        ~agentlens.errors.AgentlensError: Propagated from the core workflow
            so ``main`` can map it to the corresponding exit code.
    """
    store_path = args.store_path if args.store_path is not None else default_store_path()
    clock = SystemClock()

    logger.info(
        "Resolved session arguments: %s",
        json.dumps(
            {
                "file": str(args.file_path),
                "format": args.output_format,
                "store": str(store_path),
                "dry_run": args.dry_run,
            }
        ),
    )

    output = analyze_session(
        transcript_path=args.file_path,
        store_path=store_path,
        clock=clock,
        output_format=args.output_format,
        dry_run=args.dry_run,
        claude_root=default_claude_root(),
    )
    print(output)
    return EXIT_OK


def _session_callback(
    *,
    file_path: Path,
    output_format: str | None,
    store_path: Path | None,
    dry_run: bool,
) -> int:
    return _run_session(
        SessionArgs(
            file_path=file_path,
            output_format=output_format,
            store_path=store_path,
            dry_run=dry_run,
        )
    )


_SESSION_COMMAND = click.Command(
    name=SESSION_SUBCOMMAND,
    callback=_session_callback,
    help="Analyze one subagent transcript and report its deterministic facts.",
    params=[
        click.Option(
            ["--file", "file_path"],
            required=True,
            type=click.Path(path_type=Path),
            help="Path to the subagent transcript to analyze.",
        ),
        click.Option(
            ["--format", "output_format"],
            type=click.Choice([FORMAT_JSON]),
            default=None,
            help="Emit the JSON document to standard output instead of an artifact file.",
        ),
        click.Option(
            ["--store", "store_path"],
            type=click.Path(path_type=Path),
            default=None,
            help="Override the default store location.",
        ),
        click.Option(
            ["--dryrun", "dry_run"],
            is_flag=True,
            default=False,
            help="Report what would be written without writing the store or the artifact.",
        ),
    ],
)

_ROOT = click.Group(
    name="agentlens",
    help="Read-only analysis of Claude Code session and subagent transcripts.",
)
_ROOT.add_command(_SESSION_COMMAND)


@dataclass(frozen=True, slots=True, kw_only=True)
class ReportArgs:
    """The parsed arguments for the ``report`` subcommand."""

    selector: WindowSelector
    agent: str | None
    output_format: str | None
    store_path: Path | None
    dry_run: bool


class _MutuallyExclusiveOption(click.Option):
    """A ``click.Option`` that cannot be supplied alongside a named sibling set.

    Declares the exclusion on the option itself, via ``mutually_exclusive``,
    so the report command's window-selector group is a declared group rather
    than an ad hoc check written into the command body.
    """

    def __init__(
        self,
        param_decls: Sequence[str] | None = None,
        *,
        mutually_exclusive: frozenset[str] = frozenset(),
        **attrs: Any,
    ) -> None:
        self._mutually_exclusive = mutually_exclusive
        super().__init__(param_decls, **attrs)

    def handle_parse_result(
        self, ctx: click.Context, opts: Mapping[str, object], args: list[str]
    ) -> tuple[object, list[str]]:
        value, remaining = super().handle_parse_result(ctx, opts, args)
        if ctx.get_parameter_source(self.name or "") == click.ParameterSource.COMMANDLINE:
            supplied = {
                name
                for name in self._mutually_exclusive
                if ctx.get_parameter_source(name) == click.ParameterSource.COMMANDLINE
            }
            if supplied:
                names = ", ".join(sorted({self.name or "", *supplied}))
                raise click.UsageError(f"only one window selector may be supplied at once: {names}")
        return value, remaining


def _run_report(args: ReportArgs) -> int:
    """Resolve concrete collaborators for one ``report`` run and execute it.

    Resolves the local timezone and the requested window once at this
    CLI/core boundary, logs the resolved arguments once at INFO before
    delegating to :func:`agentlens.core.report.generate_report`, then prints
    that workflow's returned document or summary.

    Raises:
        ~agentlens.errors.AgentlensError: Propagated from the core workflow
            so ``main`` can map it to the corresponding exit code.
    """
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


def _require_single_window_form(selector: WindowSelector) -> None:
    has_range = selector.range_from is not None or selector.range_to is not None
    form_count = sum(
        (
            selector.since_duration is not None,
            selector.named_window is not None,
            has_range,
        )
    )
    if form_count != 1:
        raise click.UsageError(
            "exactly one window selector is required: --since, --window, or --from with --to"
        )
    if has_range and (selector.range_from is None or selector.range_to is None):
        raise click.UsageError("--from and --to must be supplied together")


def _report_args_from_params(params: Mapping[str, object]) -> ReportArgs:
    """Build a validated ``ReportArgs`` from a ``report`` command's parsed parameters.

    Shared by :func:`parse_report_args`, which reads parameters off a
    manually built context, and :func:`_report_callback`, which receives
    them as keyword arguments from Click's own dispatch, so the single-
    window-form validation is enforced identically on both paths.
    """
    selector = WindowSelector(
        since_duration=cast("str | None", params["since_duration"]),
        named_window=cast("str | None", params["named_window"]),
        range_from=cast("str | None", params["range_from"]),
        range_to=cast("str | None", params["range_to"]),
    )
    _require_single_window_form(selector)
    return ReportArgs(
        selector=selector,
        agent=cast("str | None", params["agent"]),
        output_format=cast("str | None", params["output_format"]),
        store_path=cast("Path | None", params["store_path"]),
        dry_run=cast(bool, params["dry_run"]),
    )


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
    args = _report_args_from_params(
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
    return _run_report(args)


_REPORT_COMMAND = click.Command(
    name=REPORT_SUBCOMMAND,
    callback=_report_callback,
    help="Report deterministic subagent-spawn facts over a resolved window.",
    params=[
        _MutuallyExclusiveOption(
            ["--since", "since_duration"],
            mutually_exclusive=frozenset({"named_window", "range_from", "range_to"}),
            type=str,
            default=None,
            help="Report a relative duration ending now, for example 7d.",
        ),
        _MutuallyExclusiveOption(
            ["--window", "named_window"],
            mutually_exclusive=frozenset({"since_duration", "range_from", "range_to"}),
            type=click.Choice([NAMED_WINDOW_THIS_WEEK]),
            default=None,
            help="Report a named local-calendar window.",
        ),
        _MutuallyExclusiveOption(
            ["--from", "range_from"],
            mutually_exclusive=frozenset({"since_duration", "named_window"}),
            type=str,
            default=None,
            help="Explicit range lower bound (ISO-8601), paired with --to.",
        ),
        _MutuallyExclusiveOption(
            ["--to", "range_to"],
            mutually_exclusive=frozenset({"since_duration", "named_window"}),
            type=str,
            default=None,
            help="Explicit range upper bound (ISO-8601), paired with --from.",
        ),
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
_ROOT.add_command(_REPORT_COMMAND)


def parse_report_args(argv: Sequence[str]) -> ReportArgs:
    """Parse the ``report`` subcommand's arguments.

    Testable directly against a plain argument list, independent of
    ``sys.argv`` and of the CLI's own dispatch, by building a context off
    ``_REPORT_COMMAND`` without invoking it.

    Raises:
        click.ClickException: ``argv`` does not start with the ``report``
            subcommand, the window selectors are missing, conflicting, or an
            incomplete range, or another flag is malformed.
    """
    if not argv or argv[0] != REPORT_SUBCOMMAND:
        raise click.UsageError(f"expected the {REPORT_SUBCOMMAND!r} subcommand")
    context = _REPORT_COMMAND.make_context("agentlens report", list(argv[1:]))
    return _report_args_from_params(context.params)


def _configure_logging() -> None:
    """Attach one INFO stderr handler to the ``agentlens`` package logger.

    Called from the composition root on every ``main`` invocation rather
    than at import time. Removes only the handler a prior call attached
    (identified by a marker attribute) before adding a fresh one bound to
    the current ``sys.stderr``, so repeated ``main`` calls neither duplicate
    log lines nor keep writing to a stream a caller has since replaced or
    closed. Handlers an embedding application attached itself are left
    untouched, and disabling propagation stops those external handlers from
    printing the same line a second time.
    """
    package_logger = logging.getLogger(_PACKAGE_LOGGER_NAME)
    for handler in list(package_logger.handlers):
        if getattr(handler, _CLI_LOG_HANDLER_ATTR, False):
            package_logger.removeHandler(handler)

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    setattr(handler, _CLI_LOG_HANDLER_ATTR, True)
    package_logger.addHandler(handler)
    package_logger.setLevel(logging.INFO)
    package_logger.propagate = False


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, resolve the composition root, and dispatch to a subcommand.

    Returns a process exit code rather than calling ``sys.exit`` itself, which
    keeps this function directly testable and lets it honor its own contract
    even when a subcommand raises something unexpected. Root and subcommand
    ``--help`` (and no arguments at all) are successful control flow: Click
    resolves them to exit code 0 before any command callback runs.
    """
    _configure_logging()
    args = list(argv) if argv is not None else sys.argv[1:]
    if not args:
        args = ["--help"]

    try:
        result: int = _ROOT.main(args=args, prog_name="agentlens", standalone_mode=False)
    except click.ClickException as exc:
        exc.show()
        return exc.exit_code
    except AgentlensError as exc:
        logger.error("Command failed: %s", exc)
        logger.debug("Command failure detail", exc_info=True)
        return _exit_code_for(exc)
    except Exception:
        logger.exception("Unexpected failure running agentlens %s", args)
        return EXIT_UNEXPECTED

    return result


if __name__ == "__main__":
    sys.exit(main())
