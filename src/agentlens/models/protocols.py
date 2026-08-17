"""Protocols for the injected seams.

Neither Protocol is implemented inside this package. The implementations are a
real client and a fake in ``tests/fakes.py``, which is what makes the indirection
a seam rather than speculation.

Callers accept these as required constructor arguments, never as defaulted ones.
See ``docs/adr/0004`` for the rule that decides what becomes a seam and which
candidates were rejected.
"""

from datetime import datetime
from typing import Protocol

from agentlens.models.judging import JudgeResponse


class Clock(Protocol):
    """Supplies the current instant, so time-dependent logic stays testable."""

    def now(self) -> datetime:
        """Return the current instant, timezone-aware and in UTC."""
        ...


class JudgeBackend(Protocol):
    """Sends a prepared prompt to an LLM and returns its raw envelope.

    An implementation owns its transport and translates that transport's failures
    into :class:`~agentlens.errors.JudgeError`, so callers depend on agentlens's
    vocabulary rather than on ``subprocess`` or an HTTP library.

    An implementation does not parse or validate the verdict. Keeping that out
    here is what lets provenance stay explicit downstream: scores are locally
    derived and validated, while evidence and fix text stay untrusted output.
    """

    def score(self, prompt: str, *, model: str) -> JudgeResponse:
        """Score one prepared transcript.

        Args:
            prompt: The prepared transcript view, already reduced to what the
                rubric needs. Its hash is the verdict cache key, so the caller
                controls it exactly.
            model: The model to request. May be a floating alias; the concrete
                identifier that answered comes back on the response.

        Raises:
            ~agentlens.errors.JudgeUnavailableError: The backend cannot be
                reached or is not authenticated.
            ~agentlens.errors.JudgeResponseError: The backend answered but the
                envelope was unusable.
        """
        ...
