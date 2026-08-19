"""Domain types for a report window: the caller's request and its resolved bounds.

Both ``core`` (which resolves a selector into UTC bounds) and ``render``
(which persists the resolved window in the report document) depend on these
types rather than on each other, the same pattern
:mod:`agentlens.models.agent_definitions` uses.
"""

from dataclasses import dataclass
from datetime import datetime, tzinfo

DEFAULT_MIN_SESSIONS_FOR_TREND = 5
NAMED_WINDOW_THIS_WEEK = "this-week"


@dataclass(frozen=True, slots=True, kw_only=True)
class WindowSelector:
    """The caller's original window request, exactly as supplied.

    Exactly one of ``since_duration``, ``named_window``, or the
    ``range_from``/``range_to`` pair is populated; resolving a selector is
    what enforces that invariant, not this type. Retained verbatim, rather
    than only the resolved bounds, so a saved report can explain how it was
    requested without needing the original command line.
    """

    since_duration: str | None = None
    named_window: str | None = None
    range_from: str | None = None
    range_to: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class LocalTimezone:
    """The machine's local timezone, resolved once outside window resolution.

    ``zone`` is the ``tzinfo`` used to compute calendar boundaries.
    ``identifier`` is what a report persists: an IANA zone name when one
    could be determined, otherwise a fixed UTC offset such as
    ``"UTC-03:00"``.
    """

    zone: tzinfo
    identifier: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ResolvedWindow:
    """The current and prior UTC bounds a report actually queries.

    Both ranges are half-open ``[start, end)``: a spawn starting exactly at
    ``current_end`` belongs to the following window, never this one. The
    prior range ends where the current range begins and spans the same
    elapsed duration, so trend comparisons never compare unequal durations.
    """

    selector: WindowSelector
    current_start: datetime
    current_end: datetime
    prior_start: datetime
    prior_end: datetime
    local_timezone: str
    min_sessions_for_trend: int
