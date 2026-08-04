"""Window resolution for the `report` command's `--since`/`--from`/`--to`/
`--today` flags — resolves them into a `[start, end)` date range.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Final

from agentlens.errors import WindowResolutionError

DEFAULT_WINDOW_DAYS: Final[int] = 7

_SINCE_RELATIVE_RE: Final[re.Pattern[str]] = re.compile(r"^(\d+)d$")


@dataclass(frozen=True)
class WindowRange:
    """A `[start, end)` date range (`end` exclusive)."""

    start: date
    end: date

    def __post_init__(self) -> None:
        if self.end <= self.start:
            raise WindowResolutionError("window end must be after start")

    @property
    def n_days(self) -> int:
        return (self.end - self.start).days

    def prior(self) -> WindowRange:
        """The immediately preceding equal-length span."""
        span = timedelta(days=self.n_days)
        return WindowRange(start=self.start - span, end=self.start)


def resolve_window(
    *,
    since: str | None = None,
    from_: str | None = None,
    to: str | None = None,
    today: bool = False,
    now: date | None = None,
) -> WindowRange:
    """Resolve report window flags to a `[start, end)` range.

    Precedence: `--today` (equivalent to `--since 1d`), then `--from`/`--to`
    (both required together), then `--since` (`<N>d` or an absolute
    `YYYY-MM-DD` start date), then the 7-day default when nothing is given.
    """
    today_date = now if now is not None else datetime.now(UTC).date()

    if today:
        return WindowRange(start=today_date, end=today_date + timedelta(days=1))

    if from_ is not None or to is not None:
        if from_ is None or to is None:
            raise WindowResolutionError("--from and --to must be given together")
        start = _parse_date(from_)
        end = _parse_date(to) + timedelta(days=1)
        return WindowRange(start=start, end=end)

    if since is not None:
        relative = _SINCE_RELATIVE_RE.match(since)
        if relative:
            days = int(relative.group(1))
            end = today_date + timedelta(days=1)
            return WindowRange(start=end - timedelta(days=days), end=end)
        start = _parse_date(since)
        return WindowRange(start=start, end=today_date + timedelta(days=1))

    end = today_date + timedelta(days=1)
    return WindowRange(start=end - timedelta(days=DEFAULT_WINDOW_DAYS), end=end)


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise WindowResolutionError(f"not a valid date: {value!r}") from exc
