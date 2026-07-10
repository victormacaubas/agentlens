from __future__ import annotations

from pathlib import Path

from agentlens.discovery.models import AgentDefFile, MainSessionFile, SubagentRun

AGENT_JSONL_GLOB = "agent-*.jsonl"
SUBAGENTS_DIRNAME = "subagents"
AGENTS_DIRNAME = "agents"
SKILLS_DIRNAME = "skills"


def discover_main_sessions(projects_root: Path) -> list[MainSessionFile]:
    """Find `projects/**/*.jsonl` at the top level of each project folder."""
    if not projects_root.is_dir():
        return []
    results: list[MainSessionFile] = []
    for project_dir in sorted(p for p in projects_root.iterdir() if p.is_dir()):
        for jsonl in sorted(project_dir.glob("*.jsonl")):
            results.append(
                MainSessionFile(path=jsonl, session_id=jsonl.stem, project_dir=project_dir)
            )
    return results


def discover_subagent_runs(projects_root: Path) -> list[SubagentRun]:
    """Find `projects/**/<sid>/subagents/agent-*.jsonl`, paired with sidecars."""
    if not projects_root.is_dir():
        return []
    results: list[SubagentRun] = []
    for project_dir in sorted(p for p in projects_root.iterdir() if p.is_dir()):
        for sid_dir in sorted(p for p in project_dir.iterdir() if p.is_dir()):
            subagents_dir = sid_dir / SUBAGENTS_DIRNAME
            if not subagents_dir.is_dir():
                continue
            for jsonl in sorted(subagents_dir.glob(AGENT_JSONL_GLOB)):
                agent_id = jsonl.stem.removeprefix("agent-")
                meta_path = subagents_dir / f"{jsonl.stem}.meta.json"
                results.append(
                    SubagentRun(
                        jsonl_path=jsonl,
                        meta_path=meta_path if meta_path.is_file() else None,
                        agent_id=agent_id,
                        parent_session_id=sid_dir.name,
                        project_dir=project_dir,
                    )
                )
    return results


def discover_agent_defs(
    *,
    claude_home: Path,
    project_claude_dir: Path | None = None,
) -> list[AgentDefFile]:
    """Find agent definitions under `.claude/agents/**` at user and project level.

    Resolves both the flat layout (``agents/<name>.md``) and the nested
    layout (``agents/<name>/<name>.md``).

    Args:
        claude_home: The user's `.claude` directory (e.g. `~/.claude`).
        project_claude_dir: The current project's `.claude` directory, if
            known. Omitted when there is no project context (e.g. `--file`
            targets outside any tracked project).
    """
    roots: list[tuple[Path, str]] = [(claude_home / AGENTS_DIRNAME, "user")]
    if project_claude_dir is not None:
        roots.append((project_claude_dir / AGENTS_DIRNAME, "project"))

    results: list[AgentDefFile] = []
    for agents_dir, scope in roots:
        results.extend(_discover_agent_defs_in(agents_dir, scope))
    return results


def discover_available_skills(claude_home: Path) -> set[str]:
    """Best-effort discovery of skill names under `.claude/skills/**`.

    Returns the set of skill directory names found there; empty when the
    tree is missing or unreadable. Advisory only (D3/`available`) — a
    skill can be declared or fired without appearing here, e.g. one
    provided by a plugin this scan does not resolve. Never raises.
    """
    skills_dir = claude_home / SKILLS_DIRNAME
    if not skills_dir.is_dir():
        return set()
    return {entry.name for entry in skills_dir.iterdir() if entry.is_dir()}


def _discover_agent_defs_in(agents_dir: Path, scope: str) -> list[AgentDefFile]:
    if not agents_dir.is_dir():
        return []
    results: list[AgentDefFile] = []
    for entry in sorted(agents_dir.iterdir()):
        if entry.is_file() and entry.suffix == ".md":
            results.append(AgentDefFile(path=entry, scope=scope))
        elif entry.is_dir():
            nested = entry / f"{entry.name}.md"
            if nested.is_file():
                results.append(AgentDefFile(path=nested, scope=scope))
    return results
