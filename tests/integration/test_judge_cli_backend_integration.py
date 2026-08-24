"""The one integration canary: the real hardened invocation against the installed CLI.

Excluded by default (``pytest.ini_options.addopts`` filters out
``-m integration``) and only run through ``make integration``, since it needs
a real authenticated ``claude`` CLI and costs money. Kept deliberately
trivial so a run of this canary costs a fraction of a cent.
"""

import pytest

from agentlens.judge.cli_backend import ClaudeCliJudge
from agentlens.judge.prompt import render_prompt
from tests.factories import build_spawn_narrative


@pytest.mark.integration
def test_hardened_invocation_authenticates_and_returns_a_parseable_envelope() -> None:
    narrative = build_spawn_narrative(
        task_prompt="Say hello.", messages=("Hello.",), tool_events=()
    )
    prompt = render_prompt(narrative)
    judge = ClaudeCliJudge()

    response = judge.score(prompt, model="sonnet")

    assert response.is_error is False
    assert response.resolved_model
    assert response.cost_usd is not None
