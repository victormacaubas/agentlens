import json
import logging
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click

from agentlens.core.session import FORMAT_JSON, analyze_session
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


# Parses `session` flags only; never invoked directly, so it carries no callback.
_SESSION_COMMAND = click.Command(
    name=SESSION_SUBCOMMAND,
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


def parse_session_args(argv: Sequence[str]) -> SessionArgs:
    """Parse the ``session`` subcommand's arguments.

    Testable directly against a plain argument list, independent of
    ``sys.argv`` and of any CLI test runner.

    Raises:
        click.ClickException: ``argv`` does not start with the ``session``
            subcommand, or the remaining flags are malformed.
    """
    if not argv or argv[0] != SESSION_SUBCOMMAND:
        raise click.UsageError(f"expected the {SESSION_SUBCOMMAND!r} subcommand")
    context = _SESSION_COMMAND.make_context("agentlens session", list(argv[1:]))
    return SessionArgs(
        file_path=context.params["file_path"],
        output_format=context.params["output_format"],
        store_path=context.params["store_path"],
        dry_run=context.params["dry_run"],
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class ReportArgs:
    """The parsed arguments for the ``report`` subcommand.

    Not yet wired into :func:`main`: composing this with window resolution,
    ingest, and rendering belongs to a later change slice.
    """

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


# Parses `report` flags only; never invoked directly, so it carries no callback.
_REPORT_COMMAND = click.Command(
    name=REPORT_SUBCOMMAND,
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


def parse_report_args(argv: Sequence[str]) -> ReportArgs:
    """Parse the ``report`` subcommand's arguments.

    Testable directly against a plain argument list, independent of
    ``sys.argv`` and of any CLI test runner. Not yet reachable from
    :func:`main`.

    Raises:
        click.ClickException: ``argv`` does not start with the ``report``
            subcommand, the window selectors are missing, conflicting, or an
            incomplete range, or another flag is malformed.
    """
    if not argv or argv[0] != REPORT_SUBCOMMAND:
        raise click.UsageError(f"expected the {REPORT_SUBCOMMAND!r} subcommand")
    context = _REPORT_COMMAND.make_context("agentlens report", list(argv[1:]))
    selector = WindowSelector(
        since_duration=context.params["since_duration"],
        named_window=context.params["named_window"],
        range_from=context.params["range_from"],
        range_to=context.params["range_to"],
    )
    _require_single_window_form(selector)
    return ReportArgs(
        selector=selector,
        agent=context.params["agent"],
        output_format=context.params["output_format"],
        store_path=context.params["store_path"],
        dry_run=context.params["dry_run"],
    )


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


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, resolve the composition root, and run the ``session`` command.

    Returns a process exit code rather than calling ``sys.exit`` itself, which
    keeps this function directly testable.
    """
    args = list(argv) if argv is not None else sys.argv[1:]

    try:
        parsed = parse_session_args(args)
    except click.ClickException as exc:
        exc.show()
        return exc.exit_code

    store_path = parsed.store_path if parsed.store_path is not None else default_store_path()
    clock = SystemClock()

    logger.info(
        "Resolved arguments: %s",
        json.dumps(
            {
                "file": str(parsed.file_path),
                "format": parsed.output_format,
                "store": str(store_path),
                "dry_run": parsed.dry_run,
            }
        ),
    )

    try:
        output = analyze_session(
            transcript_path=parsed.file_path,
            store_path=store_path,
            clock=clock,
            output_format=parsed.output_format,
            dry_run=parsed.dry_run,
            claude_root=default_claude_root(),
        )
    except AgentlensError as exc:
        logger.error("Session analysis failed: %s", exc)
        logger.debug("Session analysis failure detail", exc_info=True)
        return _exit_code_for(exc)
    except Exception:
        logger.exception("Unexpected failure analyzing %s", parsed.file_path)
        raise

    print(output)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
