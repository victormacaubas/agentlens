"""Resolving a caller's window request into UTC bounds.

Three selector forms exist: a relative elapsed duration (``--since 7d``), a
named local-calendar window (``--window this-week``), and an explicit range
(``--from``/``--to``). Every form resolves to the same half-open
``[current_start, current_end)`` current range plus an equal-length prior
range ending where the current one begins, so trend comparisons always
compare like-for-like durations.

The local timezone is a required parameter of :func:`resolve_window` rather
than something this module looks up for itself: calendar math needs to be
exercised against timezones other than the machine's own (a DST transition,
for instance), and a lookup wired straight to the OS would make that
untestable.
"""

import os
import re
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from agentlens.errors import ConfigError
from agentlens.models.protocols import Clock
from agentlens.models.windows import (
    DEFAULT_MIN_SESSIONS_FOR_TREND,
    NAMED_WINDOW_THIS_WEEK,
    LocalTimezone,
    ResolvedWindow,
    WindowSelector,
)

_LOCALTIME_LINK = Path("/etc/localtime")
_ZONEINFO_MARKER = "zoneinfo/"
_DURATION_PATTERN = re.compile(r"^(?P<value>\d+)(?P<unit>[dhm])$")
_DURATION_UNIT_FIELDS = {"d": "days", "h": "hours", "m": "minutes"}


def resolve_local_timezone(*, clock: Clock) -> LocalTimezone:
    """Determine the machine's local timezone without a third-party lookup.

    Tries, in order: the ``TZ`` environment variable; the IANA zone name
    embedded in the resolved target of ``/etc/localtime``; and, if neither
    yields a usable name, the machine's fixed UTC offset at ``clock``'s
    current instant. Never raises for want of a name.
    """
    tz_env = os.environ.get("TZ")
    if tz_env:
        zone = _try_zoneinfo(tz_env)
        if zone is not None:
            return LocalTimezone(zone=zone, identifier=tz_env)

    name = _read_localtime_zone_name()
    if name is not None:
        zone = _try_zoneinfo(name)
        if zone is not None:
            return LocalTimezone(zone=zone, identifier=name)

    offset = clock.now().astimezone().utcoffset() or timedelta(0)
    return LocalTimezone(zone=timezone(offset), identifier=_format_offset(offset))


def _try_zoneinfo(name: str) -> ZoneInfo | None:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, OSError, ValueError):
        return None


def _read_localtime_zone_name() -> str | None:
    if not _LOCALTIME_LINK.exists():
        return None
    try:
        resolved = str(_LOCALTIME_LINK.resolve())
    except OSError:
        return None
    marker_index = resolved.rfind(_ZONEINFO_MARKER)
    if marker_index == -1:
        return None
    return resolved[marker_index + len(_ZONEINFO_MARKER) :]


def _format_offset(offset: timedelta) -> str:
    total_minutes = int(offset.total_seconds() // 60)
    sign = "+" if total_minutes >= 0 else "-"
    hours, minutes = divmod(abs(total_minutes), 60)
    return f"UTC{sign}{hours:02d}:{minutes:02d}"


def resolve_window(
    selector: WindowSelector,
    *,
    clock: Clock,
    local_timezone: LocalTimezone,
    min_sessions_for_trend: int = DEFAULT_MIN_SESSIONS_FOR_TREND,
) -> ResolvedWindow:
    """Resolve ``selector`` into current and equal-length prior UTC bounds.

    ``local_timezone`` is used for calendar math (a named window's day
    boundaries, and a naive ``--from``/``--to`` bound) and is also what gets
    recorded on the returned window, so a saved report explains its own
    calendar resolution.

    Raises:
        ~agentlens.errors.ConfigError: ``selector`` supplies zero or more
            than one window form, only one side of an explicit range, an
            unparseable or non-positive ``--since`` duration, an unsupported
            named window, or a ``--to`` that does not follow ``--from``.
    """
    now = clock.now()
    has_range = selector.range_from is not None or selector.range_to is not None
    form_count = sum(
        (
            selector.since_duration is not None,
            selector.named_window is not None,
            has_range,
        )
    )
    if form_count != 1:
        raise ConfigError(
            "exactly one window selector is required: --since, --window, or --from with --to"
        )

    if selector.since_duration is not None:
        duration = _parse_since_duration(selector.since_duration)
        current_start, current_end = now - duration, now
    elif selector.named_window is not None:
        current_start, current_end = _resolve_named_window(
            selector.named_window, now=now, local_timezone=local_timezone
        )
        duration = current_end - current_start
    elif selector.range_from is not None and selector.range_to is not None:
        current_start = _parse_range_bound(
            selector.range_from, flag="--from", local_timezone=local_timezone
        )
        current_end = _parse_range_bound(
            selector.range_to, flag="--to", local_timezone=local_timezone
        )
        if current_end <= current_start:
            raise ConfigError("--to must be after --from")
        duration = current_end - current_start
    else:
        raise ConfigError("--from and --to must be supplied together")

    return ResolvedWindow(
        selector=selector,
        current_start=current_start,
        current_end=current_end,
        prior_start=current_start - duration,
        prior_end=current_start,
        local_timezone=local_timezone.identifier,
        min_sessions_for_trend=min_sessions_for_trend,
    )


def _parse_since_duration(raw: str) -> timedelta:
    match = _DURATION_PATTERN.fullmatch(raw.strip())
    if match is None:
        raise ConfigError(
            f"--since {raw!r} is not a supported duration; use an integer followed by "
            "d, h, or m, for example 7d"
        )
    value = int(match["value"])
    if value <= 0:
        raise ConfigError(f"--since {raw!r} must be a positive duration")
    unit_field = _DURATION_UNIT_FIELDS[match["unit"]]
    return timedelta(**{unit_field: value})


def _resolve_named_window(
    name: str, *, now: datetime, local_timezone: LocalTimezone
) -> tuple[datetime, datetime]:
    if name != NAMED_WINDOW_THIS_WEEK:
        raise ConfigError(
            f"--window {name!r} is not a supported named window; supported: "
            f"{NAMED_WINDOW_THIS_WEEK}"
        )
    local_now = now.astimezone(local_timezone.zone)
    week_start_local = (local_now - timedelta(days=local_now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return week_start_local.astimezone(UTC), now


def _parse_range_bound(raw: str, *, flag: str, local_timezone: LocalTimezone) -> datetime:
    try:
        parsed = datetime.fromisoformat(raw.strip())
    except ValueError as exc:
        raise ConfigError(f"{flag} {raw!r} is not a valid ISO-8601 date or datetime") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=local_timezone.zone)
    return parsed.astimezone(UTC)
