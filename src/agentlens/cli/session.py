import json
import logging
import secrets
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import click

from agentlens.cli.exit_codes import EXIT_OK
from agentlens.cli.paths import default_claude_root, default_store_path
from agentlens.core.session import FORMAT_JSON, analyze_session
from agentlens.judge.cli_backend import DEFAULT_TIMEOUT_S, ClaudeCliJudge
from agentlens.models.claims import CLAIM_LEASE_MARGIN_S
from agentlens.models.protocols import JudgeBackend
from agentlens.models.scoring import ScoringRequest
from agentlens.utils.clock import SystemClock

logger = logging.getLogger(__name__)

_SCORING_OWNER_TOKEN_BYTES = 16


@dataclass(frozen=True, slots=True, kw_only=True)
class SessionArgs:
    file_path: Path
    output_format: str | None
    store_path: Path | None
    dry_run: bool
    score: bool
    requested_model: str


def build_session_command() -> click.Command:
    """Build the command that analyzes one subagent transcript."""
    return click.Command(
        name="session",
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
            click.Option(
                ["--score", "score"],
                is_flag=True,
                default=False,
                help="Request a modeled verdict for this spawn from an LLM judge. "
                "Costs money; never on by default.",
            ),
            click.Option(
                ["--judge-model", "requested_model"],
                type=str,
                default="sonnet",
                help="The model alias or id to request the verdict from. "
                "Meaningful only when --score is also given.",
            ),
        ],
    )


def _session_callback(
    *,
    file_path: Path,
    output_format: str | None,
    store_path: Path | None,
    dry_run: bool,
    score: bool,
    requested_model: str,
) -> int:
    return _run_session(
        SessionArgs(
            file_path=file_path,
            output_format=output_format,
            store_path=store_path,
            dry_run=dry_run,
            score=score,
            requested_model=requested_model,
        )
    )


def _run_session(args: SessionArgs) -> int:
    store_path = args.store_path if args.store_path is not None else default_store_path()
    clock = SystemClock()
    judge_timeout_s = DEFAULT_TIMEOUT_S
    scoring_owner = secrets.token_urlsafe(_SCORING_OWNER_TOKEN_BYTES)
    scoring = (
        ScoringRequest(
            requested_model=args.requested_model,
            owner=scoring_owner,
            claim_lease=timedelta(seconds=judge_timeout_s + CLAIM_LEASE_MARGIN_S),
        )
        if args.score
        else None
    )
    judge: JudgeBackend | None = (
        ClaudeCliJudge(timeout_s=judge_timeout_s) if scoring is not None else None
    )

    logger.info(
        "Resolved session arguments: %s",
        json.dumps(
            {
                "file": str(args.file_path),
                "format": args.output_format,
                "store": str(store_path),
                "dry_run": args.dry_run,
                "score": args.score,
                "requested_model": args.requested_model,
                "owner": scoring_owner,
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
        scoring=scoring,
        judge=judge,
    )
    print(output)
    return EXIT_OK
