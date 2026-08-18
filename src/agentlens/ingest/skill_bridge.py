"""Deriving the session-skill bridge from declarations, availability, and firing.

The bridge is the union of three independently resolved states per skill
name: whether the spawn's bound agent definition declared it, whether the
discovered inventory can prove it was available, and whether the transcript
proves it fired. A name earns a row by satisfying any one of the three, and
each state is computed without reference to the other two, so a fire never
backfills an availability claim the files cannot support, and an available,
undeclared skill is not mistaken for a declared one.
"""

from collections.abc import Sequence
from datetime import datetime

from agentlens.ingest.records import JsonRecord
from agentlens.ingest.skill_firing import resolve_fired_skill_names
from agentlens.ingest.skill_inventory import (
    SkillInventoryEntry,
    merge_skill_inventory,
    normalize_skill_name,
    resolve_skill_availability,
)
from agentlens.models.agent_definitions import AgentDefinition
from agentlens.models.skill_signals import KnownState, SessionSkillSignal


def derive_skill_signals(
    *,
    session_id: str,
    agent_definition: AgentDefinition | None,
    skill_inventory: Sequence[SkillInventoryEntry],
    records: Sequence[JsonRecord],
    started_at: datetime,
) -> tuple[SessionSkillSignal, ...]:
    """Return one row per skill named by a declaration, the inventory, or a fire.

    ``agent_definition`` is ``None`` when history could not prove a binding,
    in which case every name's declaration state is ``unknown`` rather than
    ``false``: an unresolved binding says nothing about what was actually
    declared. When a definition is bound, its ``skills`` list is the complete
    truth for that file, so a name it omits resolves to ``false``, not
    ``unknown``.

    Results are ordered by skill name for a deterministic bridge.
    """
    fired_names = resolve_fired_skill_names(records)
    inventory = merge_skill_inventory(skill_inventory)
    declared_names = (
        frozenset(normalize_skill_name(name) for name in agent_definition.config.skills)
        if agent_definition is not None
        else frozenset()
    )
    names = declared_names | inventory.keys() | fired_names
    return tuple(
        SessionSkillSignal(
            session_id=session_id,
            skill_name=name,
            declared=_resolve_declared(
                bound=agent_definition is not None, declared_names=declared_names, name=name
            ),
            available=resolve_skill_availability(inventory, skill_name=name, started_at=started_at),
            fired=name in fired_names,
        )
        for name in sorted(names)
    )


def _resolve_declared(*, bound: bool, declared_names: frozenset[str], name: str) -> KnownState:
    if not bound:
        return KnownState.UNKNOWN
    return KnownState.TRUE if name in declared_names else KnownState.FALSE
