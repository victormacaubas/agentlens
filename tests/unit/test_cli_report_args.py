"""The ``report`` subcommand's argument parser.

Not yet reachable from ``agentlens.cli.main``; that wiring belongs to a later
change slice. These tests exercise ``parse_report_args`` directly.
"""

from pathlib import Path

import click
import pytest

from agentlens.cli import parse_report_args
from agentlens.core.session import FORMAT_JSON
from agentlens.models.windows import WindowSelector


def test_since_duration_parses_into_the_selector() -> None:
    args = parse_report_args(["report", "--since", "7d"])

    assert args.selector == WindowSelector(since_duration="7d")
    assert args.agent is None
    assert args.output_format is None
    assert args.store_path is None
    assert args.dry_run is False


def test_named_window_parses_into_the_selector() -> None:
    args = parse_report_args(["report", "--window", "this-week"])

    assert args.selector == WindowSelector(named_window="this-week")


def test_explicit_range_parses_into_the_selector() -> None:
    args = parse_report_args(["report", "--from", "2026-01-01", "--to", "2026-01-08"])

    assert args.selector == WindowSelector(range_from="2026-01-01", range_to="2026-01-08")


def test_agent_store_format_and_dryrun_flags_all_parse() -> None:
    args = parse_report_args(
        [
            "report",
            "--since",
            "7d",
            "--agent",
            "implementer",
            "--store",
            "custom-store/agentlens.db",
            "--format",
            FORMAT_JSON,
            "--dryrun",
        ]
    )

    assert args.agent == "implementer"
    assert args.store_path == Path("custom-store/agentlens.db")
    assert args.output_format == FORMAT_JSON
    assert args.dry_run is True


def test_missing_subcommand_raises() -> None:
    with pytest.raises(click.ClickException):
        parse_report_args(["--since", "7d"])


@pytest.mark.parametrize(
    "argv",
    [
        ["report"],
        ["report", "--since", "7d", "--window", "this-week"],
        ["report", "--from", "2026-01-01"],
        ["report", "--to", "2026-01-08"],
        ["report", "--since", "7d", "--from", "2026-01-01", "--to", "2026-01-08"],
        ["report", "--window", "next-week"],
        ["report", "--window", "this-week", "--from", "2026-01-01"],
    ],
)
def test_invalid_selector_combination_exits_2_and_touches_no_filesystem(
    argv: list[str], tmp_path: Path
) -> None:
    store_path = tmp_path / "store" / "agentlens.db"
    reports_dir = tmp_path / "reports"

    with pytest.raises(click.ClickException) as excinfo:
        parse_report_args(argv)

    assert excinfo.value.exit_code == 2
    assert not store_path.exists()
    assert not reports_dir.exists()
