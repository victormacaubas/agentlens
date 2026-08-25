from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum

from agentlens.models.facts import FactVerdict


@dataclass(frozen=True, slots=True, kw_only=True)
class ScoringRequest:
    """Everything required to request scoring besides the injected judge backend."""

    requested_model: str
    owner: str
    claim_lease: timedelta


@dataclass(frozen=True, slots=True, kw_only=True)
class RunJudgeUsage:
    """Judge usage attributable to one scoring run."""

    cost_usd: float
    input_tokens: int
    output_tokens: int


class ScoringStatus(StrEnum):
    """How one scoring request resolved."""

    SCORED = "scored"
    REUSED = "reused"
    CLAIMED_ELSEWHERE = "claimed_elsewhere"
    FAILED = "failed"


@dataclass(frozen=True, slots=True, kw_only=True)
class ScoringOutcome:
    """The modeled result and spend attributable to one scoring request."""

    status: ScoringStatus
    verdict: FactVerdict | None
    run_judge_usage: RunJudgeUsage
    is_behind_current_input: bool
