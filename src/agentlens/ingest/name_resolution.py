"""Resolving an agent type through the ordered name-resolution chain.

Only the first and last links are implemented here: the ``.meta.json``
sidecar is authoritative when present, and a hash of the transcript's own raw
session id is the fallback that keeps a session from being dropped when no
sidecar exists. The links in between are a later change's job.
"""

from dataclasses import dataclass

from agentlens.models.identity import NameSource
from agentlens.utils.hashing import hash_text


@dataclass(frozen=True, slots=True, kw_only=True)
class NameResolution:
    """The agent type resolved for a session, and which link supplied it."""

    agent_type: str
    name_source: NameSource


def resolve_agent_type(*, sidecar_agent_type: str | None, raw_session_id: str) -> NameResolution:
    """Resolve the agent type, sidecar first and the raw-id hash second."""
    if sidecar_agent_type is not None:
        return NameResolution(agent_type=sidecar_agent_type, name_source=NameSource.META_JSON)
    return NameResolution(
        agent_type=hash_text(raw_session_id), name_source=NameSource.AGENT_ID_HASH
    )
