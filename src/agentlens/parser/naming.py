from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

NAME_SOURCE_META = "meta_agent_type"
NAME_SOURCE_ATTRIBUTION = "attribution_agent"
NAME_SOURCE_PARENT_TASK = "parent_task_subagent_type"
NAME_SOURCE_AGENT_ID_HASH = "agent_id_hash"


@dataclass(frozen=True)
class NameResolution:
    name: str
    name_source: str
    ambiguous: bool


def resolve_name(
    *,
    meta_agent_type: str | None,
    attribution_agents: Iterable[str] = (),
    parent_task_subagent_type: str | None,
    agent_id: str,
) -> NameResolution:
    """Resolve a subagent session's name via the guarded fallback chain.

    Order, authoritative first: (1) `.meta.json` `agentType`, (2) distinct
    `attributionAgent` values from the session's own assistant records,
    (3) the parent's `Task` `subagent_type`, (4) the `agent_id` hash — never
    dropping a session. Conflicting distinct signals across the whole chain
    are flagged `ambiguous`, even though one source still wins per priority.
    """
    distinct_attribution = sorted({a for a in attribution_agents if a})
    all_signals = (meta_agent_type, *distinct_attribution, parent_task_subagent_type)
    candidates = [c for c in all_signals if c]
    ambiguous = len(set(candidates)) > 1

    if meta_agent_type:
        return NameResolution(
            name=meta_agent_type, name_source=NAME_SOURCE_META, ambiguous=ambiguous
        )
    if distinct_attribution:
        return NameResolution(
            name=distinct_attribution[0], name_source=NAME_SOURCE_ATTRIBUTION, ambiguous=ambiguous
        )
    if parent_task_subagent_type:
        return NameResolution(
            name=parent_task_subagent_type,
            name_source=NAME_SOURCE_PARENT_TASK,
            ambiguous=ambiguous,
        )
    return NameResolution(name=agent_id, name_source=NAME_SOURCE_AGENT_ID_HASH, ambiguous=False)
