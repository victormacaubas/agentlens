"""``FakeJudgeBackend``, the double that earns the ``JudgeBackend`` Protocol.

A structural Protocol with only a real implementation and no fake is
unearned; this exercises the fake directly, and later phases exercise it in
place of a real backend.
"""

import pytest

from agentlens.errors import JudgeUnavailableError
from tests.factories import build_judge_response
from tests.fakes import FakeJudgeBackend


def test_configured_response_is_returned_and_the_call_is_recorded() -> None:
    response = build_judge_response()
    fake = FakeJudgeBackend(response=response)

    result = fake.score("Score this run.", model="sonnet")

    assert result is response
    assert fake.calls == [("Score this run.", "sonnet")]


def test_configured_error_is_raised_instead_of_returning() -> None:
    fake = FakeJudgeBackend(error=JudgeUnavailableError("unavailable"))

    with pytest.raises(JudgeUnavailableError, match="unavailable"):
        fake.score("Score this run.", model="sonnet")


def test_repeated_calls_are_all_recorded_in_order() -> None:
    fake = FakeJudgeBackend(response=build_judge_response())

    fake.score("first", model="sonnet")
    fake.score("second", model="opus")

    assert fake.calls == [("first", "sonnet"), ("second", "opus")]


def test_constructing_with_neither_response_nor_error_raises() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        FakeJudgeBackend()


def test_constructing_with_both_response_and_error_raises() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        FakeJudgeBackend(response=build_judge_response(), error=JudgeUnavailableError("x"))
