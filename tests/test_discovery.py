"""Tests for `agentlens.discovery`.

Unit tests build synthetic `.claude`-shaped trees under `tmp_path` — no
dependency on real logs or the host machine's `~/.claude`.
"""

from __future__ import annotations

from pathlib import Path

from agentlens.discovery import (
    discover_agent_defs,
    discover_main_sessions,
    discover_subagent_runs,
)


def test_discover_main_sessions_finds_top_level_jsonl(tmp_path: Path) -> None:
    projects_root = tmp_path / "projects"
    project_dir = projects_root / "-Users-x-project"
    project_dir.mkdir(parents=True)
    (project_dir / "sid-1.jsonl").write_text("{}\n")
    (project_dir / "sid-2.jsonl").write_text("{}\n")

    sessions = discover_main_sessions(projects_root)

    assert {s.session_id for s in sessions} == {"sid-1", "sid-2"}
    assert all(s.project_dir == project_dir for s in sessions)


def test_discover_main_sessions_missing_root_returns_empty(tmp_path: Path) -> None:
    assert discover_main_sessions(tmp_path / "does-not-exist") == []


def test_discover_subagent_runs_pairs_meta_sidecar(tmp_path: Path) -> None:
    projects_root = tmp_path / "projects"
    subagents_dir = projects_root / "-Users-x-project" / "sid-1" / "subagents"
    subagents_dir.mkdir(parents=True)
    (subagents_dir / "agent-a1.jsonl").write_text("{}\n")
    (subagents_dir / "agent-a1.meta.json").write_text('{"agentType": "implementer"}')
    (subagents_dir / "agent-a2.jsonl").write_text("{}\n")  # no sidecar

    runs = {run.agent_id: run for run in discover_subagent_runs(projects_root)}

    assert runs["a1"].meta_path is not None
    assert runs["a1"].parent_session_id == "sid-1"
    assert runs["a2"].meta_path is None


def test_discover_subagent_runs_missing_root_returns_empty(tmp_path: Path) -> None:
    assert discover_subagent_runs(tmp_path / "does-not-exist") == []


def test_discover_agent_defs_flat_and_nested_user_and_project(tmp_path: Path) -> None:
    claude_home = tmp_path / "user-home" / ".claude"
    (claude_home / "agents").mkdir(parents=True)
    (claude_home / "agents" / "implementer.md").write_text("---\nname: implementer\n---\n")
    nested_dir = claude_home / "agents" / "researcher"
    nested_dir.mkdir()
    (nested_dir / "researcher.md").write_text("---\nname: researcher\n---\n")

    project_claude_dir = tmp_path / "project" / ".claude"
    (project_claude_dir / "agents").mkdir(parents=True)
    (project_claude_dir / "agents" / "custom.md").write_text("---\nname: custom\n---\n")

    defs = discover_agent_defs(claude_home=claude_home, project_claude_dir=project_claude_dir)
    by_scope = {(d.path.stem, d.scope) for d in defs}

    assert by_scope == {("implementer", "user"), ("researcher", "user"), ("custom", "project")}


def test_discover_agent_defs_without_project_dir_only_scans_user(tmp_path: Path) -> None:
    claude_home = tmp_path / ".claude"
    (claude_home / "agents").mkdir(parents=True)
    (claude_home / "agents" / "implementer.md").write_text("---\nname: implementer\n---\n")

    defs = discover_agent_defs(claude_home=claude_home)

    assert len(defs) == 1
    assert defs[0].scope == "user"


def test_discover_agent_defs_missing_dirs_returns_empty(tmp_path: Path) -> None:
    assert discover_agent_defs(claude_home=tmp_path / "does-not-exist") == []
