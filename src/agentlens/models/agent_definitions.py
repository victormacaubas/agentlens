"""Domain types for one cataloged agent definition.

Both ``ingest`` (which discovers and parses definitions) and ``store`` (which
persists them) depend on these types rather than on each other, the same
pattern :mod:`agentlens.models.facts` uses for session rows.
"""

from dataclasses import dataclass
from enum import StrEnum

from agentlens.models.identity import SourceRevision


class DefinitionScope(StrEnum):
    """Where an agent definition file was found.

    Project scope overrides user scope for spawns inside that project; a
    definition's scope alone does not say whether it is the effective one for
    any given spawn.
    """

    USER = "user"
    PROJECT = "project"


@dataclass(frozen=True, slots=True, kw_only=True)
class AgentDefinitionConfig:
    """The deterministic configuration one agent definition declares.

    ``model`` and ``effort`` are ``None`` when the frontmatter omits the key.
    ``tools`` and ``skills`` are empty tuples when the frontmatter omits the
    key entirely, which is a proven-empty declaration rather than an unknown
    one; unknown declaration is a property of the *binding*, not of a
    definition that was itself read successfully.
    """

    name: str
    model: str | None
    effort: str | None
    tools: tuple[str, ...]
    skills: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class AgentDefinition:
    """One cataloged, content-addressed agent definition.

    ``agent_definition_id`` is derived from ``scope``, ``source_project``, and
    the file's own content hash, so the same file scanned twice resolves to
    the same identity and an edited file resolves to a new one.
    ``source_project`` is ``None`` for a user-scoped definition.
    """

    agent_definition_id: str
    scope: DefinitionScope
    source_project: str | None
    config: AgentDefinitionConfig
    revision: SourceRevision
