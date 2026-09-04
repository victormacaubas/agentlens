import logging
import sys
from typing import Final

import click

from agentlens.cli.exit_codes import EXIT_UNEXPECTED, exit_code_for
from agentlens.cli.report import build_report_command
from agentlens.cli.score import build_score_command
from agentlens.cli.session import build_session_command
from agentlens.errors import AgentlensError

logger = logging.getLogger(__name__)

_PACKAGE_LOGGER_NAME = "agentlens"
_CLI_LOG_HANDLER_ATTR: Final = "_agentlens_cli_owned"


def build_root_command() -> click.Group:
    """Build the root command and register each command-specific callback."""
    root = click.Group(
        name="agentlens",
        help="Read-only analysis of Claude Code session and subagent transcripts.",
    )
    root.add_command(build_session_command())
    root.add_command(build_report_command())
    root.add_command(build_score_command())
    return root


def main(argv: list[str] | None = None) -> int:
    """Configure diagnostics, dispatch a command, and return its public exit code."""
    _configure_logging()
    args = list(argv) if argv is not None else sys.argv[1:]
    if not args:
        args = ["--help"]
    try:
        result: int = build_root_command().main(
            args=args,
            prog_name="agentlens",
            standalone_mode=False,
        )
    except click.ClickException as exc:
        exc.show()
        return exc.exit_code
    except AgentlensError as exc:
        logger.error("Command failed: %s", exc)
        logger.debug("Command failure detail", exc_info=True)
        return exit_code_for(exc)
    except Exception:
        logger.exception("Unexpected failure running agentlens %s", args)
        return EXIT_UNEXPECTED
    return result


def _configure_logging() -> None:
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


if __name__ == "__main__":
    sys.exit(main())
