"""Resolving an agent type through the ordered name-resolution chain.

Four links are consulted in priority order: the ``.meta.json`` sidecar, the
subagent's own distinct assistant-record attribution, the parent transcript's
spawning invocation, and a hash of the transcript's own raw session id as the
fallback that keeps a session from being dropped when nothing else supplies a
value. Two or more distinct values across the links that actually supplied a
candidate resolve to an explicit ambiguous outcome rather than silently
picking one, so a report never treats a disputed agent type as certain.
"""

from dataclasses import dataclass

from agentlens.models.identity import NameSource
from agentlens.utils.hashing import hash_text


@dataclass(frozen=True, slots=True, kw_only=True)
class NameResolution:
    """The agent type resolved for a session, and which link supplied it."""

    agent_type: str
    name_source: NameSource


def resolve_agent_type(
    *,
    sidecar_agent_type: str | None,
    attribution_agent_types: frozenset[str],
    parent_subagent_type: str | None,
    raw_session_id: str,
) -> NameResolution:
    """Resolve the agent type through the ordered sidecar/attribution/parent/hash chain.

    ``parent_subagent_type`` is the caller's already-resolved evidence: this
    function never opens a file itself, so a caller that already knows the
    sidecar and attribution links are not silent can skip reading the parent
    transcript entirely and pass ``None`` without this function noticing the
    difference from a parent transcript that was read and had nothing to say.

    Exactly one distinct value across every link that supplied a candidate is
    used outright, credited to the highest-priority link that supplied it —
    sidecar over attribution over parent — so a sidecar and an agreeing
    attribution value together still credit the sidecar. Two or more distinct
    values, including two distinct attribution values with no other link to
    break the tie, resolve to :attr:`NameSource.AMBIGUOUS` with a deterministic
    retained value: the highest-priority link's, and within a link that itself
    supplied more than one value, its lexicographically smallest — so the
    result never depends on record order. No candidate at all resolves to a
    hash of ``raw_session_id`` under :attr:`NameSource.AGENT_ID_HASH`, and the
    session is never dropped.
    """
    candidates: list[tuple[NameSource, str]] = []
    if sidecar_agent_type is not None:
        candidates.append((NameSource.META_JSON, sidecar_agent_type))
    candidates.extend(
        (NameSource.ATTRIBUTION_AGENT, value) for value in sorted(attribution_agent_types)
    )
    if parent_subagent_type is not None:
        candidates.append((NameSource.PARENT_TASK, parent_subagent_type))

    distinct_values = {value for _, value in candidates}
    if not distinct_values:
        return NameResolution(
            agent_type=hash_text(raw_session_id), name_source=NameSource.AGENT_ID_HASH
        )
    if len(distinct_values) == 1:
        source, value = candidates[0]
        return NameResolution(agent_type=value, name_source=source)
    _, retained_value = candidates[0]
    return NameResolution(agent_type=retained_value, name_source=NameSource.AMBIGUOUS)
