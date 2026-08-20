"""Reading, cataloging, and binding agent definitions.

An agent definition is a Markdown file with a bounded frontmatter block
(:mod:`agentlens.ingest.frontmatter`) declaring an agent's ``name``, ``model``,
``effort``, ``tools``, and ``skills``. This module reads one such file into a
content-addressed :class:`~agentlens.models.agent_definitions.AgentDefinition`,
discovers every definition under a scope's directory, resolves which
definition is effective for a given agent type once project and user scope
are both considered, and decides whether a spawn may be bound to the
currently observed effective definition at all.

Mapping a spawn's qualified ``source_project`` to the real filesystem
directory a project-scoped ``.claude/agents/`` would live under is out of
scope here: the project-directory encoding under ``~/.claude/projects/`` is
lossy, and resolving it is bundled with the rest of source-tree discovery.
Callers that already know a project's real root pass it in explicitly.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path

from agentlens.errors import MalformedSourceError, SourceChangedError
from agentlens.ingest.frontmatter import list_field, parse_frontmatter, scalar_field
from agentlens.models.agent_definitions import (
    AgentDefinition,
    AgentDefinitionConfig,
    DefinitionScope,
)
from agentlens.models.identity import SourceRevision
from agentlens.utils.hashing import canonical_json_fingerprint, hash_text

_DEFINITION_GLOB = "*.md"
_NANOSECONDS_PER_SECOND = 1_000_000_000


def content_addressed_definition_id(
    *, scope: DefinitionScope, source_project: str | None, content_hash: str
) -> str:
    """Return the stable identity for a definition of ``scope`` with ``content_hash``.

    Two definitions with the same scope, source project, and file content
    always resolve to the same identity; changing any of the three resolves
    to a different one.
    """
    return canonical_json_fingerprint(
        ["agent_definition", scope.value, source_project, content_hash]
    )


def read_agent_definition(
    path: Path, *, scope: DefinitionScope, source_project: str | None
) -> AgentDefinition | None:
    """Read one candidate agent-definition file with a sound stat-read-stat revision.

    ``path.stat()`` follows symlinks, so a symlinked definition's revision
    reflects its target's content, and editing the target is what changes
    the definition's identity.

    Returns ``None`` when ``path`` does not resolve to a file, including a
    dangling symlink: a definition whose target has been deleted is treated
    as absent rather than an error.

    Raises:
        MalformedSourceError: The path could not be statted or read for a
            reason other than absence (including a symlink loop), the file
            has no parseable frontmatter, or a known field has an unsupported
            shape.
        SourceChangedError: The file changed between the stat taken before
            the read and the one taken immediately after it.
    """
    try:
        stat_before = path.stat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise MalformedSourceError(f"could not stat {path}") from exc

    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise MalformedSourceError(f"could not read {path}") from exc

    try:
        stat_after = path.stat()
    except OSError as exc:
        raise MalformedSourceError(f"could not stat {path}") from exc
    if (stat_before.st_mtime_ns, stat_before.st_size) != (
        stat_after.st_mtime_ns,
        stat_after.st_size,
    ):
        raise SourceChangedError(f"{path} changed while being read")

    revision = SourceRevision(
        mtime_ns=stat_after.st_mtime_ns, size=stat_after.st_size, content_hash=hash_text(text)
    )
    config = _extract_config(text, source_path_label=str(path))
    agent_definition_id = content_addressed_definition_id(
        scope=scope, source_project=source_project, content_hash=revision.content_hash
    )
    return AgentDefinition(
        agent_definition_id=agent_definition_id,
        scope=scope,
        source_project=source_project,
        config=config,
        revision=revision,
    )


def _extract_config(text: str, *, source_path_label: str) -> AgentDefinitionConfig:
    frontmatter = parse_frontmatter(text, source_path_label=source_path_label)
    name = scalar_field(frontmatter, "name", source_path_label=source_path_label)
    if name is None:
        raise MalformedSourceError(f"{source_path_label} has no 'name' field")
    return AgentDefinitionConfig(
        name=name,
        model=scalar_field(frontmatter, "model", source_path_label=source_path_label),
        effort=scalar_field(frontmatter, "effort", source_path_label=source_path_label),
        tools=list_field(frontmatter, "tools", source_path_label=source_path_label),
        skills=list_field(frontmatter, "skills", source_path_label=source_path_label),
    )


def discover_agent_definitions(
    directory: Path, *, scope: DefinitionScope, source_project: str | None
) -> tuple[AgentDefinition, ...]:
    """Scan ``directory`` non-recursively for ``*.md`` agent definitions.

    Returns an empty tuple when ``directory`` does not exist. Each candidate
    is read through :func:`read_agent_definition`, so a dangling symlink
    inside the directory is skipped rather than raising. Results are ordered
    by file name for a deterministic catalog.
    """
    if not directory.is_dir():
        return ()
    definitions = []
    for candidate in sorted(directory.glob(_DEFINITION_GLOB)):
        definition = read_agent_definition(candidate, scope=scope, source_project=source_project)
        if definition is not None:
            definitions.append(definition)
    return tuple(definitions)


def resolve_effective_definitions(
    *,
    user_definitions: Sequence[AgentDefinition],
    project_definitions: Sequence[AgentDefinition],
) -> Mapping[str, AgentDefinition]:
    """Return the effective definition per agent name for one project.

    A project-scoped definition overrides a user-scoped definition of the
    same name; an agent named only in user scope keeps its user-scoped
    definition.
    """
    effective: dict[str, AgentDefinition] = {
        definition.config.name: definition for definition in user_definitions
    }
    for definition in project_definitions:
        effective[definition.config.name] = definition
    return effective


def resolve_agent_definition_binding(
    *,
    effective_definitions: Mapping[str, AgentDefinition],
    agent_type: str,
    started_at: datetime,
) -> AgentDefinition | None:
    """Return the definition a spawn may bind to, or ``None`` when history is unprovable.

    A spawn binds to the effective definition for ``agent_type`` only when
    that definition's observed modification time is no later than
    ``started_at``. No matching definition, and a matching definition
    observed newer than the spawn, both resolve to ``None``: current files
    cannot prove what an old spawn actually saw.
    """
    definition = effective_definitions.get(agent_type)
    if definition is None:
        return None
    spawn_epoch_ns = int(started_at.timestamp() * _NANOSECONDS_PER_SECOND)
    if definition.revision.mtime_ns > spawn_epoch_ns:
        return None
    return definition
