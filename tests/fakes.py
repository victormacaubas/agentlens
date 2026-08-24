from datetime import datetime

from agentlens.errors import JudgeError
from agentlens.models.judging import JudgeResponse


class FakeClock:
    """A fixed instant, standing in for ``agentlens.models.protocols.Clock``."""

    def __init__(self, *, instant: datetime) -> None:
        self._instant = instant

    def now(self) -> datetime:
        return self._instant


class FakeJudgeBackend:
    """A scriptable stand-in for ``agentlens.models.protocols.JudgeBackend``.

    Configured with exactly one of ``response`` or ``error``, so a test
    states what the judge should do rather than relying on an implicit
    default that could silently construct a real backend if a caller forgot
    to configure this one. Every call is recorded in ``calls`` so a test can
    assert how many calls were made and with what arguments, without a
    mock's call-tracking machinery.
    """

    def __init__(
        self,
        *,
        response: JudgeResponse | None = None,
        error: JudgeError | None = None,
    ) -> None:
        if (response is None) == (error is None):
            raise ValueError("FakeJudgeBackend needs exactly one of response or error.")
        self._response = response
        self._error = error
        self.calls: list[tuple[str, str]] = []

    def score(self, prompt: str, *, model: str) -> JudgeResponse:
        self.calls.append((prompt, model))
        if self._error is not None:
            raise self._error
        assert self._response is not None
        return self._response
