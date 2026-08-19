"""Deterministic report-aggregate types: window totals, per-spawn averages,
weighted proportions, and the agent rollups the store builds from them.

Every value here is measured from ``fact_session`` rows the store already
persists. None of it is model output, and none of it is ever joined against
verdict data.
"""

from dataclasses import dataclass
from enum import StrEnum


class TrendStatus(StrEnum):
    """Whether an agent rollup's current and prior windows both meet the trend threshold.

    Governs only whether a metric's prior-window comparison (its prior value
    and signed delta) is disclosed. It never governs whether the metric's own
    current value, or an available-but-below-threshold prior value, is shown.
    """

    COMPARABLE = "comparable"
    INSUFFICIENT_DATA = "insufficient_data"


@dataclass(frozen=True, slots=True, kw_only=True)
class MetricTotals:
    """The additive metrics for one agent type, summed over one window's spawns.

    Totals are never compared across windows: a total moving because the
    spawn population grew, rather than because per-spawn behavior changed, is
    not a trend. No window's totals ever get a "prior" or "delta" sibling —
    that comparison only exists for :class:`PerSpawnAverages` and the
    cache-read proportion.
    """

    n_turns: int
    n_invocations: int
    n_reads: int
    n_edits: int
    n_writes: int
    n_bash: int
    n_distinct_files: int
    n_errors: int
    n_denials: int
    n_repeated_invocations: int
    n_skills_fired: int
    duration_ms: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    unreadable_line_count: int


@dataclass(frozen=True, slots=True, kw_only=True)
class PerSpawnAverages:
    """The same additive metrics as :class:`MetricTotals`, divided by spawn count.

    This shape is reused verbatim for a signed delta between two windows'
    averages: a delta is shaped exactly like the values it is the difference
    of, so ``AgentRollup.average_deltas`` holds one of these too, with its
    fields simply allowed to be negative.
    """

    n_turns: float
    n_invocations: float
    n_reads: float
    n_edits: float
    n_writes: float
    n_bash: float
    n_distinct_files: float
    n_errors: float
    n_denials: float
    n_repeated_invocations: float
    n_skills_fired: float
    duration_ms: float
    input_tokens: float
    output_tokens: float
    cache_read_tokens: float
    cache_creation_tokens: float
    unreadable_line_count: float


@dataclass(frozen=True, slots=True, kw_only=True)
class WeightedProportion:
    """A ratio computed from summed window totals, plus its prior-window comparison.

    ``current`` is ``None`` when the current window's denominator is zero —
    an unmeasurable proportion must never collapse to ``0.0``, which would
    read as "no cache reads" rather than "not computable". ``prior`` is
    ``None`` when no prior-window value could be computed, independent of the
    trend threshold. ``delta`` populates only when both ``current`` and
    ``prior`` are available and the owning rollup's trend status is
    :attr:`TrendStatus.COMPARABLE`.
    """

    current: float | None
    prior: float | None
    delta: float | None


@dataclass(frozen=True, slots=True, kw_only=True)
class AgentRollup:
    """One agent type's current-window population, plus its prior-window comparison.

    ``n_spawns`` counts spawns, never sessions or agents: four
    ``implementer`` spawns in one parent session contribute 4, not 1.

    ``prior_averages`` and ``average_deltas`` are independent absences.
    ``prior_averages`` is ``None`` only when the agent type has zero
    prior-window spawns at all; it is still populated when the prior
    population is nonzero but below the trend threshold, so a low-volume
    prior value stays visible. ``average_deltas`` is ``None`` whenever
    ``trend_status`` is ``INSUFFICIENT_DATA``, regardless of whether a prior
    value happens to be available.
    """

    agent_type: str
    n_spawns: int
    n_spawns_prior: int
    trend_status: TrendStatus
    totals: MetricTotals
    averages: PerSpawnAverages
    prior_averages: PerSpawnAverages | None
    average_deltas: PerSpawnAverages | None
    cache_read_proportion: WeightedProportion
