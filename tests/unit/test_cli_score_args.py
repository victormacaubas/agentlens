"""The ``score`` subcommand's argument parser, independent of its execution."""

from datetime import UTC, datetime
from pathlib import Path

import click
import pytest

from agentlens.cli import parse_score_args
from agentlens.core.session import FORMAT_JSON
from agentlens.core.window_scoring import DEFAULT_MAX_RUN_COST_USD
from agentlens.core.windows import resolve_local_timezone, resolve_window
from agentlens.models.windows import WindowSelector
from tests.fakes import FakeClock


def test_since_duration_parses_into_the_selector() -> None:
    args = parse_score_args(["score", "--since", "7d"])

    assert args.selector == WindowSelector(since_duration="7d")
    assert args.agent is None
    assert args.requested_model == "sonnet"
    assert args.max_run_cost_usd == DEFAULT_MAX_RUN_COST_USD
    assert args.store_path is None
    assert args.output_format is None
    assert args.dry_run is False


def test_named_window_parses_into_the_selector() -> None:
    args = parse_score_args(["score", "--window", "this-week"])

    assert args.selector == WindowSelector(named_window="this-week")


def test_explicit_range_parses_into_the_selector() -> None:
    args = parse_score_args(["score", "--from", "2026-01-01", "--to", "2026-01-08"])

    assert args.selector == WindowSelector(range_from="2026-01-01", range_to="2026-01-08")


def test_a_selector_resolves_the_same_window_bounds_as_report_resolves() -> None:
    clock = FakeClock(instant=datetime(2026, 1, 15, tzinfo=UTC))
    local_timezone = resolve_local_timezone(clock=clock)
    score_args = parse_score_args(["score", "--since", "7d"])

    score_window = resolve_window(score_args.selector, clock=clock, local_timezone=local_timezone)
    report_window = resolve_window(
        WindowSelector(since_duration="7d"), clock=clock, local_timezone=local_timezone
    )

    assert score_window.current_start == report_window.current_start
    assert score_window.current_end == report_window.current_end
    assert score_window.prior_start == report_window.prior_start
    assert score_window.prior_end == report_window.prior_end


def test_agent_model_ceiling_store_format_and_dryrun_flags_all_parse() -> None:
    args = parse_score_args(
        [
            "score",
            "--since",
            "7d",
            "--agent",
            "implementer",
            "--judge-model",
            "opus",
            "--max-run-cost-usd",
            "5.0",
            "--store",
            "custom-store/agentlens.db",
            "--format",
            FORMAT_JSON,
            "--dryrun",
        ]
    )

    assert args.agent == "implementer"
    assert args.requested_model == "opus"
    assert args.max_run_cost_usd == 5.0
    assert args.store_path == Path("custom-store/agentlens.db")
    assert args.output_format == FORMAT_JSON
    assert args.dry_run is True


def test_missing_subcommand_raises() -> None:
    with pytest.raises(click.ClickException):
        parse_score_args(["--since", "7d"])


@pytest.mark.parametrize(
    "argv",
    [
        ["score"],
        ["score", "--since", "7d", "--window", "this-week"],
        ["score", "--from", "2026-01-01"],
        ["score", "--to", "2026-01-08"],
        ["score", "--since", "7d", "--from", "2026-01-01", "--to", "2026-01-08"],
        ["score", "--window", "next-week"],
        ["score", "--window", "this-week", "--from", "2026-01-01"],
    ],
)
def test_invalid_selector_combination_exits_2_and_touches_no_filesystem(
    argv: list[str], tmp_path: Path
) -> None:
    store_path = tmp_path / "store" / "agentlens.db"

    with pytest.raises(click.ClickException) as excinfo:
        parse_score_args(argv)

    assert excinfo.value.exit_code == 2
    assert not store_path.exists()
