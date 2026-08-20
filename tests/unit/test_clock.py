"""SystemClock and FakeClock both satisfy Clock structurally, without inheritance."""

from datetime import UTC, datetime, timedelta

from agentlens.models.protocols import Clock
from agentlens.utils.clock import SystemClock
from tests.fakes import FakeClock


def _read_instant(clock: Clock) -> datetime:
    return clock.now()


def test_system_clock_returns_timezone_aware_utc_instant() -> None:
    instant = _read_instant(SystemClock())
    assert instant.tzinfo is not None
    assert instant.utcoffset() == timedelta(0)


def test_fake_clock_returns_its_fixed_instant() -> None:
    fixed = datetime(2026, 1, 1, tzinfo=UTC)
    assert _read_instant(FakeClock(instant=fixed)) == fixed
