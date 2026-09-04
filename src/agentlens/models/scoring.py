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


@dataclass(frozen=True, slots=True, kw_only=True)
class WindowJudgeUsage:
    """Judge usage aggregated across every spawn one window run scored.

    Distinct from :class:`RunJudgeUsage`, which despite its name is the
    usage attributable to a single spawn's own scoring request. Only spawns
    with status ``SCORED`` contribute; a reused, claimed-elsewhere, or
    failed spawn adds nothing.
    """

    cost_usd: float
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True, slots=True, kw_only=True)
class WindowScoringOutcome:
    """What happened to every spawn one window run covered.

    ``scored``, ``reused``, ``skipped``, and ``failed`` are counts of
    spawns, never sessions or agent types, and sum to the number of spawns
    the run covered. ``skipped`` counts ``ScoringStatus.CLAIMED_ELSEWHERE``.
    Nothing stops a run before it covers its whole window yet, so
    ``stop_reason`` is always ``None`` and ``unattempted`` is always ``0``.
    """

    scored: int
    reused: int
    skipped: int
    failed: int
    judge_usage: WindowJudgeUsage
    stop_reason: None
    unattempted: int
