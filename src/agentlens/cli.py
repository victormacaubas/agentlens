import json
import logging
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import click

from agentlens.core.session import FORMAT_JSON, analyze_session
from agentlens.errors import (
    AgentlensError,
    ConfigError,
    JudgeError,
    SourceError,
    StoreError,
)
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
