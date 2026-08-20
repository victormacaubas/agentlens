"""Resolving a report window: relative durations, named calendars, explicit ranges."""

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from agentlens.core import windows as windows_module
from agentlens.core.windows import resolve_local_timezone, resolve_window
from agentlens.errors import ConfigError
from agentlens.models.windows import (
    DEFAULT_MIN_SESSIONS_FOR_TREND,
    LocalTimezone,
    WindowSelector,
)
from tests.fakes import FakeClock

_UTC_TIMEZONE = LocalTimezone(zone=UTC, identifier="UTC")


def test_since_duration_resolves_a_window_ending_at_now() -> None:
    now = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
    clock = FakeClock(instant=now)

    resolved = resolve_window(
        WindowSelector(since_duration="7d"), clock=clock, local_timezone=_UTC_TIMEZONE
    )

    assert resolved.current_start == now - timedelta(days=7)
    assert resolved.current_end == now
    assert resolved.prior_start == now - timedelta(days=14)
    assert resolved.prior_end == now - timedelta(days=7)
    assert resolved.local_timezone == "UTC"
    assert resolved.min_sessions_for_trend == DEFAULT_MIN_SESSIONS_FOR_TREND
    assert resolved.selector == WindowSelector(since_duration="7d")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("24h", timedelta(hours=24)), ("30m", timedelta(minutes=30))],
)
def test_since_duration_supports_hour_and_minute_units(raw: str, expected: timedelta) -> None:
    now = datetime(2026, 1, 15, tzinfo=UTC)
    clock = FakeClock(instant=now)

    resolved = resolve_window(
        WindowSelector(since_duration=raw), clock=clock, local_timezone=_UTC_TIMEZONE
    )

    assert resolved.current_start == now - expected


@pytest.mark.parametrize("raw", ["0d", "-7d", "7", "7x", "sevendays", ""])
def test_zero_negative_malformed_or_unsupported_duration_raises_config_error(raw: str) -> None:
    clock = FakeClock(instant=datetime(2026, 1, 15, tzinfo=UTC))

    with pytest.raises(ConfigError):
        resolve_window(
            WindowSelector(since_duration=raw), clock=clock, local_timezone=_UTC_TIMEZONE
        )


def test_explicit_range_resolves_to_the_supplied_half_open_bounds() -> None:
    clock = FakeClock(instant=datetime(2026, 6, 1, tzinfo=UTC))
    selector = WindowSelector(
        range_from="2026-01-01T00:00:00+00:00", range_to="2026-01-08T00:00:00+00:00"
    )

    resolved = resolve_window(selector, clock=clock, local_timezone=_UTC_TIMEZONE)

    assert resolved.current_start == datetime(2026, 1, 1, tzinfo=UTC)
    assert resolved.current_end == datetime(2026, 1, 8, tzinfo=UTC)
    assert resolved.prior_start == datetime(2025, 12, 25, tzinfo=UTC)
    assert resolved.prior_end == datetime(2026, 1, 1, tzinfo=UTC)


def test_prior_window_ends_exactly_where_current_window_begins() -> None:
    clock = FakeClock(instant=datetime(2026, 1, 15, tzinfo=UTC))

    resolved = resolve_window(
        WindowSelector(since_duration="7d"), clock=clock, local_timezone=_UTC_TIMEZONE
    )

    assert resolved.prior_end == resolved.current_start


def test_naive_range_bound_is_interpreted_in_the_injected_local_timezone() -> None:
    local_timezone = LocalTimezone(zone=timezone(timedelta(hours=-3)), identifier="UTC-03:00")
    clock = FakeClock(instant=datetime(2026, 6, 1, tzinfo=UTC))
    selector = WindowSelector(range_from="2026-01-01T00:00:00", range_to="2026-01-02T00:00:00")

    resolved = resolve_window(selector, clock=clock, local_timezone=local_timezone)

    assert resolved.current_start == datetime(2026, 1, 1, 3, 0, tzinfo=UTC)
    assert resolved.current_end == datetime(2026, 1, 2, 3, 0, tzinfo=UTC)
    assert resolved.local_timezone == "UTC-03:00"


def test_malformed_range_bound_raises_config_error() -> None:
    clock = FakeClock(instant=datetime(2026, 1, 15, tzinfo=UTC))
    selector = WindowSelector(range_from="not-a-date", range_to="2026-01-01T00:00:00+00:00")

    with pytest.raises(ConfigError):
        resolve_window(selector, clock=clock, local_timezone=_UTC_TIMEZONE)


def test_to_not_after_from_raises_config_error() -> None:
    clock = FakeClock(instant=datetime(2026, 1, 15, tzinfo=UTC))
    selector = WindowSelector(
        range_from="2026-01-08T00:00:00+00:00", range_to="2026-01-01T00:00:00+00:00"
    )

    with pytest.raises(ConfigError):
        resolve_window(selector, clock=clock, local_timezone=_UTC_TIMEZONE)


def test_named_window_this_week_resolves_to_the_local_monday_midnight() -> None:
    now = datetime(2026, 8, 19, 15, 30, tzinfo=UTC)
    expected_monday = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    clock = FakeClock(instant=now)

    resolved = resolve_window(
        WindowSelector(named_window="this-week"), clock=clock, local_timezone=_UTC_TIMEZONE
    )

    assert resolved.current_start == expected_monday
    assert resolved.current_end == now


def test_unsupported_named_window_raises_config_error() -> None:
    clock = FakeClock(instant=datetime(2026, 8, 19, tzinfo=UTC))

    with pytest.raises(ConfigError):
        resolve_window(
            WindowSelector(named_window="last-month"), clock=clock, local_timezone=_UTC_TIMEZONE
        )


def test_this_week_crosses_a_daylight_saving_transition() -> None:
    """The ambient local zone (America/Recife, UTC-03 since 2019) has no DST.

    A DST-observing zone is constructed explicitly, per the pinned rule, so
    the test actually exercises a spring-forward transition instead of
    passing vacuously against a zone that never has one.
    """
    new_york = LocalTimezone(zone=ZoneInfo("America/New_York"), identifier="America/New_York")
    now = datetime(2026, 3, 8, 14, 0, tzinfo=UTC)  # 10:00 EDT, the Sunday DST began
    clock = FakeClock(instant=now)

    resolved = resolve_window(
        WindowSelector(named_window="this-week"), clock=clock, local_timezone=new_york
    )

    assert resolved.current_start == datetime(2026, 3, 2, 5, 0, tzinfo=UTC)  # Monday, still EST
    assert resolved.current_end == now
    assert resolved.current_end - resolved.current_start == timedelta(days=6, hours=9)


def test_range_bound_in_a_nonexistent_spring_forward_local_time_does_not_raise() -> None:
    new_york = LocalTimezone(zone=ZoneInfo("America/New_York"), identifier="America/New_York")
    clock = FakeClock(instant=datetime(2026, 3, 9, tzinfo=UTC))
    selector = WindowSelector(range_from="2026-03-08T02:30:00", range_to="2026-03-08T04:30:00")

    resolved = resolve_window(selector, clock=clock, local_timezone=new_york)

    assert resolved.current_start == datetime(2026, 3, 8, 7, 30, tzinfo=UTC)
    assert resolved.current_end == datetime(2026, 3, 8, 8, 30, tzinfo=UTC)


def test_range_bound_in_an_ambiguous_fall_back_local_time_does_not_raise() -> None:
    new_york = LocalTimezone(zone=ZoneInfo("America/New_York"), identifier="America/New_York")
    clock = FakeClock(instant=datetime(2026, 11, 2, tzinfo=UTC))
    selector = WindowSelector(range_from="2026-11-01T01:30:00", range_to="2026-11-01T02:30:00")

    resolved = resolve_window(selector, clock=clock, local_timezone=new_york)

    assert resolved.current_start.tzinfo == UTC
    assert resolved.current_end.tzinfo == UTC
    assert resolved.current_start < resolved.current_end


@pytest.mark.parametrize(
    "selector",
    [
        WindowSelector(),
        WindowSelector(since_duration="7d", named_window="this-week"),
        WindowSelector(range_from="2026-01-01T00:00:00+00:00"),
        WindowSelector(range_to="2026-01-08T00:00:00+00:00"),
        WindowSelector(since_duration="7d", range_from="2026-01-01T00:00:00+00:00"),
    ],
)
def test_zero_multiple_or_incomplete_selector_forms_raise_config_error(
    selector: WindowSelector,
) -> None:
    clock = FakeClock(instant=datetime(2026, 1, 15, tzinfo=UTC))

    with pytest.raises(ConfigError):
        resolve_window(selector, clock=clock, local_timezone=_UTC_TIMEZONE)


def test_min_sessions_for_trend_defaults_to_five_and_can_be_overridden() -> None:
    clock = FakeClock(instant=datetime(2026, 1, 15, tzinfo=UTC))
    selector = WindowSelector(since_duration="7d")

    default_resolved = resolve_window(selector, clock=clock, local_timezone=_UTC_TIMEZONE)
    custom_resolved = resolve_window(
        selector, clock=clock, local_timezone=_UTC_TIMEZONE, min_sessions_for_trend=10
    )

    assert default_resolved.min_sessions_for_trend == DEFAULT_MIN_SESSIONS_FOR_TREND
    assert custom_resolved.min_sessions_for_trend == 10


def test_resolve_local_timezone_uses_the_tz_environment_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TZ", "America/New_York")
    clock = FakeClock(instant=datetime(2026, 1, 15, tzinfo=UTC))

    resolved = resolve_local_timezone(clock=clock)

    assert resolved.identifier == "America/New_York"
    assert isinstance(resolved.zone, ZoneInfo)


def test_resolve_local_timezone_extracts_the_name_after_the_last_zoneinfo_segment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("TZ", raising=False)
    zone_file = (
        tmp_path
        / "private"
        / "var"
        / "db"
        / "timezone"
        / "tz"
        / "2026c.1.0"
        / "zoneinfo"
        / "America"
        / "Recife"
    )
    zone_file.parent.mkdir(parents=True)
    zone_file.touch()
    localtime_link = tmp_path / "etc" / "localtime"
    localtime_link.parent.mkdir(parents=True)
    localtime_link.symlink_to(zone_file)
    monkeypatch.setattr(windows_module, "_LOCALTIME_LINK", localtime_link)
    clock = FakeClock(instant=datetime(2026, 1, 15, tzinfo=UTC))

    resolved = resolve_local_timezone(clock=clock)

    assert resolved.identifier == "America/Recife"


def test_resolve_local_timezone_falls_back_to_the_offset_at_the_injected_instant(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Neither ``TZ`` nor ``/etc/localtime`` yields a name in this setup.

    The fallback then computes a fixed offset from the clock's instant
    rather than reading the wall clock; the expected offset is derived from
    that same instant here, independent of when the test itself runs.
    """
    monkeypatch.delenv("TZ", raising=False)
    monkeypatch.setattr(windows_module, "_LOCALTIME_LINK", tmp_path / "does-not-exist")
    instant = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)
    clock = FakeClock(instant=instant)

    resolved = resolve_local_timezone(clock=clock)

    expected_offset = instant.astimezone().utcoffset() or timedelta(0)
    assert resolved.zone == timezone(expected_offset)
    assert resolved.identifier.startswith("UTC")
