"""Resolving and memoizing a spawn's project-scoped agent-definition and skill context."""

from pathlib import Path

from agentlens.ingest.context import SubagentContextCache
from tests.factories import (
    build_agent_definition_text,
    build_plugin_cache_skill_path,
    build_skill_md_path,
    build_skill_md_text,
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_no_cwd_resolves_user_scope_only(tmp_path: Path) -> None:
    claude_root = tmp_path / ".claude"
    _write(
        claude_root / "agents" / "implementer.md",
        build_agent_definition_text(name="implementer"),
    )

    context = SubagentContextCache(claude_root).resolve(None)

    assert set(context.effective_definitions) == {"implementer"}
    assert context.effective_definitions["implementer"].source_project is None


def test_project_definition_overrides_user_definition_of_the_same_name(tmp_path: Path) -> None:
    claude_root = tmp_path / ".claude"
    project_root = tmp_path / "project"
    _write(
        claude_root / "agents" / "implementer.md",
        build_agent_definition_text(name="implementer", effort="high"),
    )
    _write(
        project_root / ".claude" / "agents" / "implementer.md",
        build_agent_definition_text(name="implementer", effort="medium"),
    )

    context = SubagentContextCache(claude_root).resolve(str(project_root))

    assert context.effective_definitions["implementer"].config.effort == "medium"
    assert context.effective_definitions["implementer"].source_project == str(project_root)


def test_skill_inventory_merges_user_project_and_plugin_scopes(tmp_path: Path) -> None:
    claude_root = tmp_path / ".claude"
    project_root = tmp_path / "project"
    _write(
        build_skill_md_path(tmp_path, skill_name="user-skill"),
        build_skill_md_text(name="user-skill"),
    )
    _write(
        project_root / ".claude" / "skills" / "project-skill" / "SKILL.md",
        build_skill_md_text(name="project-skill"),
    )
    _write(
        build_plugin_cache_skill_path(tmp_path, skill_name="plugin-skill"),
        build_skill_md_text(name="plugin-skill"),
    )

    context = SubagentContextCache(claude_root).resolve(str(project_root))

    assert {entry.skill_name for entry in context.skill_inventory} == {
        "user-skill",
        "project-skill",
        "plugin-skill",
    }


def test_resolution_is_memoized_per_cwd(tmp_path: Path) -> None:
    """A second resolve of the same ``cwd`` returns the cached context, not a rescan."""
    claude_root = tmp_path / ".claude"
    project_root = tmp_path / "project"
    definition_path = project_root / ".claude" / "agents" / "implementer.md"
    _write(definition_path, build_agent_definition_text(name="implementer", effort="high"))

    cache = SubagentContextCache(claude_root)
    first = cache.resolve(str(project_root))
    assert first.effective_definitions["implementer"].config.effort == "high"

    _write(definition_path, build_agent_definition_text(name="implementer", effort="medium"))
    second = cache.resolve(str(project_root))

    assert second is first
    assert second.effective_definitions["implementer"].config.effort == "high"


def test_discovered_definitions_collects_every_distinct_effective_definition_seen(
    tmp_path: Path,
) -> None:
    claude_root = tmp_path / ".claude"
    project_a = tmp_path / "project-a"
    project_b = tmp_path / "project-b"
    _write(
        claude_root / "agents" / "implementer.md",
        build_agent_definition_text(name="implementer"),
    )
    _write(
        project_a / ".claude" / "agents" / "pathfinder.md",
        build_agent_definition_text(name="pathfinder"),
    )
    _write(
        project_b / ".claude" / "agents" / "researcher.md",
        build_agent_definition_text(name="researcher"),
    )

    cache = SubagentContextCache(claude_root)
    cache.resolve(str(project_a))
    cache.resolve(str(project_b))

    names = {definition.config.name for definition in cache.discovered_definitions()}
    assert names == {"implementer", "pathfinder", "researcher"}


def test_discovered_definitions_is_empty_before_any_resolve(tmp_path: Path) -> None:
    cache = SubagentContextCache(tmp_path / ".claude")
    assert cache.discovered_definitions() == ()


def test_shadowed_user_definition_and_winning_project_definition_are_both_cataloged(
    tmp_path: Path,
) -> None:
    """A project override wins the effective binding but must not erase the
    user-scoped identity it shadows from the reproducible catalog.
    """
    claude_root = tmp_path / ".claude"
    project_root = tmp_path / "project"
    _write(
        claude_root / "agents" / "implementer.md",
        build_agent_definition_text(name="implementer", effort="high"),
    )
    _write(
        project_root / ".claude" / "agents" / "implementer.md",
        build_agent_definition_text(name="implementer", effort="medium"),
    )

    cache = SubagentContextCache(claude_root)
    context = cache.resolve(str(project_root))

    assert context.effective_definitions["implementer"].config.effort == "medium"
    cataloged_efforts = {definition.config.effort for definition in cache.discovered_definitions()}
    assert cataloged_efforts == {"high", "medium"}
    assert len(cache.discovered_definitions()) == 2
