from datetime import UTC, datetime


class SystemClock:
    """Returns the current instant, timezone-aware and in UTC."""

    def now(self) -> datetime:
        return datetime.now(UTC)
