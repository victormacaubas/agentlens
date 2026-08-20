"""Domain types for one session's skill-usage signals.

Both ``ingest`` (which derives these from declarations, inventories, and
transcript evidence) and ``store`` (which persists them at
``bridge_session_skill`` grain) depend on these types rather than on each
other, the same pattern :mod:`agentlens.models.agent_definitions` uses.
"""

from dataclasses import dataclass
from enum import StrEnum


class KnownState(StrEnum):
    """Whether a historical fact could be proven true, proven false, or neither.

    ``UNKNOWN`` is a real, storable value rather than an absence: it is how
    this product distinguishes "ingest looked and could not tell" from
    "ingest looked and it was false". Collapsing the two into a nullable
    boolean would lose exactly the distinction this type exists to keep.
    """

    TRUE = "true"
    FALSE = "false"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True, kw_only=True)
class SessionSkillSignal:
    """One row of the session-skill bridge: one qualified session, one skill name.

    ``declared``, ``available``, and ``fired`` are resolved independently.
    Transcript evidence proving a fire never backfills ``available``, and an
    agent definition declaring a skill never backfills whether it actually
    ran: each state answers a different question, and a reader must not
    collapse them into a single verdict about whether the spawn "should have"
    fired a skill it did not.
    """

    session_id: str
    skill_name: str
    declared: KnownState
    available: KnownState
    fired: bool
