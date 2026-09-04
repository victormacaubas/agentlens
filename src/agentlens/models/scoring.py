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


class WindowStopReason(StrEnum):
    """Why a window run stopped before attempting every spawn in its window.

    ``None`` on :class:`WindowScoringOutcome` means the run attempted every
    spawn it covered; one of these members means it stopped early instead.
    """

    JUDGE_UNUSABLE = "judge_unusable"
    COST_CEILING_REACHED = "cost_ceiling_reached"


@dataclass(frozen=True, slots=True, kw_only=True)
class ScoringOutcome:
    """The modeled result and spend attributable to one scoring request."""

    status: ScoringStatus
    verdict: FactVerdict | None
    run_judge_usage: RunJudgeUsage
    is_behind_current_input: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class WindowJudgeUsage:
    """Judge usage aggregated across every spawn one window run attempted.

    Distinct from :class:`RunJudgeUsage`, which despite its name is the
    usage attributable to a single spawn's own scoring request. A reused or
    claimed-elsewhere spawn adds nothing, since neither one calls the judge.
    A failed spawn adds whatever its own call had already spent before its
    verdict was rejected or its attempt could not reach the judge; that
    figure is zero unless the judge actually answered.
    """

    cost_usd: float
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True, slots=True, kw_only=True)
class WindowScoringOutcome:
    """What happened to every spawn one window run attempted.

    ``scored``, ``reused``, ``skipped``, and ``failed`` are counts of
    spawns, never sessions or agent types, and sum to the number of spawns
    the run attempted. ``skipped`` counts ``ScoringStatus.CLAIMED_ELSEWHERE``.
    ``stop_reason`` is ``None`` when the run attempted every spawn in its
    window; otherwise it names why the run stopped early, and
    ``unattempted`` counts the spawns the run never reached.
    """

    scored: int
    reused: int
    skipped: int
    failed: int
    judge_usage: WindowJudgeUsage
    stop_reason: WindowStopReason | None
    unattempted: int
