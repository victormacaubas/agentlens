from datetime import datetime


class FakeClock:
    """A fixed instant, standing in for ``agentlens.models.protocols.Clock``."""

    def __init__(self, *, instant: datetime) -> None:
        self._instant = instant

    def now(self) -> datetime:
        return self._instant
