"""The typed report document: scope metadata, current-window spawn rows, and agent rollups.

Distinct from :mod:`agentlens.models.session_facts`, which pairs one spawn
with its tool events for the single-session surface. A report document
covers every qualifying spawn across a resolved window, carries its own
schema version, and never contains a modeled field. Building one from
stored facts is ``core``'s job; this module only defines its shape.
"""

from dataclasses import dataclass
from datetime import datetime

from agentlens.models.facts import FactSession
from agentlens.models.report_aggregates import AgentRollup
from agentlens.models.skill_signals import SessionSkillSignal
from agentlens.models.windows import ResolvedWindow

REPORT_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True, kw_only=True)
class ReportSpawn:
    """One current-window spawn's deterministic facts, plus its resolved skill states.

    ``skill_signals`` is the session's ordered skill-bridge rows. Empty when
    the spawn has no applicable declaration and no firing evidence, never
    omitted, so a qualifying spawn with no skill context stays present and
    explicit rather than dropped.
    """

    session: FactSession
    skill_signals: tuple[SessionSkillSignal, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class ReportDocument:
    """The versioned deterministic window document one report run produces.

    ``window`` carries the original selector, the resolved current and
    prior UTC bounds, the local timezone identifier, and the trend
    threshold in one value, so a saved document can reproduce its own
    scope without the command line that requested it. ``agent_filter`` is
    ``None`` when the report covers every agent type. ``spawns`` holds
    every qualifying current-window spawn, including one with no
    effective definition and no skill evidence. The document never
    carries a score, verdict, or fix field: Phase 2 never runs a judge.
    """

    schema_version: int
    generated_at: datetime
    window: ResolvedWindow
    agent_filter: str | None
    spawns: tuple[ReportSpawn, ...]
    agent_rollups: tuple[AgentRollup, ...]
