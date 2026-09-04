from collections.abc import Callable, Sequence
from datetime import datetime, timedelta

from agentlens.errors import JudgeError
from agentlens.models.judging import JudgeResponse


class FakeClock:
    """A fixed instant, standing in for ``agentlens.models.protocols.Clock``."""

    def __init__(self, *, instant: datetime) -> None:
        self._instant = instant

    def now(self) -> datetime:
        return self._instant

    def advance(self, delta: timedelta) -> None:
        self._instant += delta


class FakeJudgeBackend:
    """A scriptable stand-in for ``agentlens.models.protocols.JudgeBackend``.

    Configured with exactly one of ``response``, ``error``, or ``responses``,
    so a test states what the judge should do rather than relying on an
    implicit default that could silently construct a real backend if a
    caller forgot to configure this one. ``response``/``error`` fix a single
    outcome returned or raised on every call. ``responses`` scripts a
    sequence of outcomes consumed in call order; once exhausted, its last
    entry repeats for every further call, the same way a single fixed
    ``response`` or ``error`` already does. Every call is recorded in
    ``calls`` so a test can assert how many calls were made and with what
    arguments, without a mock's call-tracking machinery.
    """

    def __init__(
        self,
        *,
        response: JudgeResponse | None = None,
        error: JudgeError | None = None,
        responses: Sequence[JudgeResponse | JudgeError] | None = None,
        on_score: Callable[[], None] | None = None,
    ) -> None:
        if responses is not None:
            if response is not None or error is not None:
                raise ValueError(
                    "FakeJudgeBackend takes responses on its own, not alongside response or error."
                )
            if len(responses) == 0:
                raise ValueError("FakeJudgeBackend needs at least one scripted outcome.")
            outcomes: list[JudgeResponse | JudgeError] = list(responses)
        elif response is not None and error is None:
            outcomes = [response]
        elif error is not None and response is None:
            outcomes = [error]
        else:
            raise ValueError("FakeJudgeBackend needs exactly one of response, error, or responses.")
        self._outcomes = outcomes
        self._on_score = on_score
        self.calls: list[tuple[str, str]] = []

    def score(self, prompt: str, *, model: str) -> JudgeResponse:
        self.calls.append((prompt, model))
        if self._on_score is not None:
            self._on_score()
        outcome = self._outcomes[min(len(self.calls) - 1, len(self._outcomes) - 1)]
        if isinstance(outcome, JudgeError):
            raise outcome
        return outcome
