"""Resolving a spawn's project-scoped agent-definition catalog and skill inventory.

A subagent transcript's own ``cwd`` field is the only sound way to locate a
project's ``.claude/agents`` and ``.claude/skills`` directories: the encoded
project directory name under ``~/.claude/projects/`` replaces both ``/`` and
``_`` with ``-``, so it cannot be decoded back into a real path. ``cwd`` sits
inside the transcript itself, so resolving a spawn's context always happens
after the transcript has been read, never before.

:class:`SubagentContextCache` memoizes that resolution across one ingest
batch, keyed on the resolved ``cwd`` value, so many spawns from the same
project scan the filesystem once rather than once per spawn.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from agentlens.ingest.agent_definitions import (
    discover_agent_definitions,
    resolve_effective_definitions,
)
from agentlens.ingest.skill_inventory import SkillInventoryEntry, discover_skill_inventory
from agentlens.models.agent_definitions import AgentDefinition, DefinitionScope

_CLAUDE_DIR_NAME = ".claude"
_AGENTS_DIR_NAME = "agents"
_SKILLS_DIR_NAME = "skills"
_PLUGINS_CACHE_RELATIVE = ("plugins", "cache")


@dataclass(frozen=True, slots=True, kw_only=True)
class SubagentContext:
    """The effective agent-definition bindings and skill inventory one spawn's project sees.

    ``effective_definitions`` already resolves project-over-user precedence,
    keyed by agent name — it is a binding map, not the full catalog. The
    complete set of observed definitions, including any a project definition
    shadows, is retained separately by :meth:`SubagentContextCache.discovered_definitions`.
    ``skill_inventory`` merges user, project, and plugin-cache entries.
    """

    effective_definitions: Mapping[str, AgentDefinition]
    skill_inventory: tuple[SkillInventoryEntry, ...]


class SubagentContextCache:
    """Resolves and memoizes :class:`SubagentContext` for one ingest batch.

    Keyed on the transcript's own ``cwd`` value, with ``None`` a distinct key
    for a spawn whose transcript names no project at all — that spawn falls
    back to user scope only, never to decoding the encoded project directory.

    Every observed user- and project-scoped definition is retained in the
    catalog, keyed by its content identity, independently of
    :attr:`SubagentContext.effective_definitions`: a project-scoped
    definition that wins precedence for binding does not remove the
    user-scoped definition it shadows from the reproducible catalog.
    """

    def __init__(self, claude_root: Path) -> None:
        self._claude_root = claude_root
        self._resolved: dict[str | None, SubagentContext] = {}
        self._definitions: dict[str, AgentDefinition] = {}

    def resolve(self, cwd: str | None) -> SubagentContext:
        """Return the context for ``cwd``, resolving and caching it on first use."""
        context = self._resolved.get(cwd)
        if context is None:
            context = self._build(cwd)
            self._resolved[cwd] = context
        return context

    def discovered_definitions(self) -> tuple[AgentDefinition, ...]:
        """Return every distinct definition observed so far, for cataloging.

        Includes every user- and project-scoped definition seen, whether or
        not it won precedence to become effective for any spawn. Ordered by
        identity for a deterministic catalog write.
        """
        return tuple(
            sorted(
                self._definitions.values(), key=lambda definition: definition.agent_definition_id
            )
        )

    def _build(self, cwd: str | None) -> SubagentContext:
        user_definitions = discover_agent_definitions(
            self._claude_root / _AGENTS_DIR_NAME, scope=DefinitionScope.USER, source_project=None
        )
        project_definitions = (
            discover_agent_definitions(
                Path(cwd) / _CLAUDE_DIR_NAME / _AGENTS_DIR_NAME,
                scope=DefinitionScope.PROJECT,
                source_project=cwd,
            )
            if cwd is not None
            else ()
        )
        for definition in (*user_definitions, *project_definitions):
            self._definitions[definition.agent_definition_id] = definition
        effective_definitions = resolve_effective_definitions(
            user_definitions=user_definitions, project_definitions=project_definitions
        )

        user_skills = discover_skill_inventory(
            self._claude_root / _SKILLS_DIR_NAME, recursive=False
        )
        project_skills = (
            discover_skill_inventory(
                Path(cwd) / _CLAUDE_DIR_NAME / _SKILLS_DIR_NAME, recursive=False
            )
            if cwd is not None
            else ()
        )
        plugin_skills = discover_skill_inventory(
            self._claude_root.joinpath(*_PLUGINS_CACHE_RELATIVE), recursive=True
        )

        return SubagentContext(
            effective_definitions=effective_definitions,
            skill_inventory=user_skills + project_skills + plugin_skills,
        )
