from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Final

from agentlens.discovery.models import (
    AgentDefFile,
    MainSessionFile,
    SourceIdentity,
    SubagentRun,
)

AGENT_JSONL_GLOB: Final[str] = "agent-*.jsonl"
SUBAGENTS_DIRNAME: Final[str] = "subagents"
AGENTS_DIRNAME: Final[str] = "agents"
SKILLS_DIRNAME: Final[str] = "skills"
SESSION_KIND_MAIN: Final[str] = "main"
SESSION_KIND_SUBAGENT: Final[str] = "subagent"

DiscoveryErrorHandler = Callable[[Path, OSError], None]


def discover_main_sessions(
    projects_root: Path,
    *,
    on_error: DiscoveryErrorHandler | None = None,
) -> Iterator[MainSessionFile]:
    """Yield top-level project transcripts while isolating directory failures."""
    for project_dir in _directories(projects_root, on_error):
        source_project = _source_project(project_dir, projects_root)
        for entry in _entries(project_dir, on_error):
            if entry.suffix != ".jsonl" or not _is_file(entry, on_error):
                continue
            identity = SourceIdentity(source_project, SESSION_KIND_MAIN, entry.stem)
            yield MainSessionFile(
                path=entry,
                session_id=identity.session_id,
                raw_session_id=identity.raw_session_id,
                source_project=source_project,
                project_dir=project_dir,
            )


def discover_subagent_runs(
    projects_root: Path,
    *,
    on_error: DiscoveryErrorHandler | None = None,
) -> Iterator[SubagentRun]:
    """Yield subagent transcripts paired with optional spawn sidecars."""
    for project_dir in _directories(projects_root, on_error):
        source_project = _source_project(project_dir, projects_root)
        for sid_dir in _directories(project_dir, on_error):
            subagents_dir = sid_dir / SUBAGENTS_DIRNAME
            if not _is_dir(subagents_dir, on_error):
                continue
            for jsonl in _entries(subagents_dir, on_error):
                if (
                    not jsonl.name.startswith("agent-")
                    or jsonl.suffix != ".jsonl"
                    or not _is_file(jsonl, on_error)
                ):
                    continue
                agent_id = jsonl.stem.removeprefix("agent-")
                meta_path = subagents_dir / f"{jsonl.stem}.meta.json"
                session_identity = SourceIdentity(
                    source_project, SESSION_KIND_SUBAGENT, agent_id
                )
                parent_identity = SourceIdentity(
                    source_project, SESSION_KIND_MAIN, sid_dir.name
                )
                yield SubagentRun(
                    jsonl_path=jsonl,
                    meta_path=meta_path if _is_file(meta_path, on_error) else None,
                    agent_id=agent_id,
                    session_id=session_identity.session_id,
                    parent_session_id=parent_identity.session_id,
                    raw_parent_session_id=sid_dir.name,
                    source_project=source_project,
                    project_dir=project_dir,
                )


def discover_agent_defs(
    *,
    claude_home: Path,
    project_claude_dir: Path | None = None,
    source_project: str | None = None,
    include_user: bool = True,
    on_error: DiscoveryErrorHandler | None = None,
) -> list[AgentDefFile]:
    """Find agent definitions under `.claude/agents/**` at user and project level.

    Resolves both the flat layout (``agents/<name>.md``) and the nested
    layout (``agents/<name>/<name>.md``).

    Args:
        claude_home: The user's `.claude` directory (e.g. `~/.claude`).
        project_claude_dir: The current project's `.claude` directory, if
            known. Omitted when there is no project context (e.g. `--file`
            targets outside any tracked project).
        source_project: Store identity of `project_claude_dir`.
        include_user: Whether to scan the user-level definitions.
        on_error: Optional callback for filesystem discovery failures.
    """
    roots: list[tuple[Path, str, str | None]] = []
    if include_user:
        roots.append((claude_home / AGENTS_DIRNAME, "user", None))
    if project_claude_dir is not None:
        configured_project_key = (
            source_project
            if source_project is not None
            else project_claude_dir.parent.name
        )
        roots.append(
            (project_claude_dir / AGENTS_DIRNAME, "project", configured_project_key)
        )

    results: list[AgentDefFile] = []
    for agents_dir, scope, discovered_project_key in roots:
        results.extend(
            _discover_agent_defs_in(
                agents_dir,
                scope,
                source_project=discovered_project_key,
                on_error=on_error,
            )
        )
    return results


def discover_available_skills(claude_home: Path) -> set[str]:
    """Best-effort discovery of skill names under `.claude/skills/**`.

    Returns the set of skill directory names found there; empty when the
    tree is missing or unreadable. Advisory only: a skill can be declared
    or fired without appearing here, e.g. one provided by a plugin this
    scan does not resolve. Never raises.
    """
    skills_dir = claude_home / SKILLS_DIRNAME
    if not _is_dir(skills_dir, None):
        return set()
    return {
        entry.name
        for entry in _entries(skills_dir, None)
        if _is_dir(entry, None)
    }


def _discover_agent_defs_in(
    agents_dir: Path,
    scope: str,
    *,
    source_project: str | None,
    on_error: DiscoveryErrorHandler | None,
) -> list[AgentDefFile]:
    if not _is_dir(agents_dir, on_error):
        return []
    results: list[AgentDefFile] = []
    for entry in _entries(agents_dir, on_error):
        if _is_file(entry, on_error) and entry.suffix == ".md":
            results.append(
                AgentDefFile(path=entry, scope=scope, source_project=source_project)
            )
        elif _is_dir(entry, on_error):
            nested = entry / f"{entry.name}.md"
            if _is_file(nested, on_error):
                results.append(
                    AgentDefFile(path=nested, scope=scope, source_project=source_project)
                )
    return results


def _source_project(project_dir: Path, projects_root: Path) -> str:
    try:
        return project_dir.relative_to(projects_root).as_posix()
    except ValueError:
        return project_dir.name


def _directories(
    path: Path,
    on_error: DiscoveryErrorHandler | None,
) -> Iterator[Path]:
    for entry in _entries(path, on_error):
        if _is_dir(entry, on_error):
            yield entry


def _entries(
    path: Path,
    on_error: DiscoveryErrorHandler | None,
) -> list[Path]:
    try:
        return sorted(path.iterdir())
    except OSError as error:
        if on_error is not None:
            on_error(path, error)
        return []


def _is_dir(path: Path, on_error: DiscoveryErrorHandler | None) -> bool:
    try:
        return path.is_dir()
    except OSError as error:
        if on_error is not None:
            on_error(path, error)
        return False


def _is_file(path: Path, on_error: DiscoveryErrorHandler | None) -> bool:
    try:
        return path.is_file()
    except OSError as error:
        if on_error is not None:
            on_error(path, error)
        return False
