"""Discovering skill directories and resolving provable availability.

Fixtures cover the user- and project-scope shape (a skill one level down),
all three observed plugin-cache depth shapes, a dangling symlink, a
directory symlink with a relative target (the shape every real user skill on
this machine actually takes), a symlink loop, and a file that changes mid-read.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentlens.errors import MalformedSourceError, SourceChangedError
from agentlens.ingest.skill_inventory import (
    SkillInventoryEntry,
    discover_skill_inventory,
    merge_skill_inventory,
    normalize_skill_name,
    resolve_skill_availability,
)
from agentlens.models.identity import SourceRevision
from agentlens.models.skill_signals import KnownState
from tests.factories import (
    build_plugin_cache_skill_path,
    build_skill_md_path,
    build_skill_md_text,
    build_source_revision,
)

_STARTED_AT = datetime(2026, 1, 10, tzinfo=UTC)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_discover_returns_an_empty_tuple_for_a_missing_directory(tmp_path: Path) -> None:
    inventory = discover_skill_inventory(tmp_path / "does-not-exist", recursive=False)
    assert inventory == ()


def test_discover_finds_a_scoped_skill_one_level_down(tmp_path: Path) -> None:
    skills_dir = tmp_path / ".claude" / "skills"
    _write(
        build_skill_md_path(tmp_path, skill_name="tdd"),
        build_skill_md_text(name="tdd"),
    )

    inventory = discover_skill_inventory(skills_dir, recursive=False)

    assert [entry.skill_name for entry in inventory] == ["tdd"]


def test_discover_skips_a_dangling_symlink_among_sound_skills(tmp_path: Path) -> None:
    skills_dir = tmp_path / ".claude" / "skills"
    _write(build_skill_md_path(tmp_path, skill_name="tdd"), build_skill_md_text(name="tdd"))
    (skills_dir / "ghost").mkdir(parents=True)
    (skills_dir / "ghost" / "SKILL.md").symlink_to(tmp_path / "nowhere.md")

    inventory = discover_skill_inventory(skills_dir, recursive=False)

    assert [entry.skill_name for entry in inventory] == ["tdd"]


def test_discover_follows_a_directory_symlink_with_a_relative_target(tmp_path: Path) -> None:
    """Every real user skill on this machine is a directory symlink with a
    relative target; ``*/SKILL.md`` must resolve it at the final component.
    """
    target_dir = tmp_path / "real-skills" / "tdd"
    _write(target_dir / "SKILL.md", build_skill_md_text(name="tdd"))
    skills_dir = tmp_path / ".claude" / "skills"
    skills_dir.mkdir(parents=True)
    (skills_dir / "tdd").symlink_to(target_dir, target_is_directory=True)

    inventory = discover_skill_inventory(skills_dir, recursive=False)

    assert [entry.skill_name for entry in inventory] == ["tdd"]
    assert inventory[0].revision.mtime_ns == (target_dir / "SKILL.md").stat().st_mtime_ns


def test_discover_symlink_loop_is_translated_to_a_malformed_source_error(tmp_path: Path) -> None:
    skill_dir = tmp_path / ".claude" / "skills" / "loop"
    skill_dir.mkdir(parents=True)
    first = skill_dir / "SKILL.md"
    second = skill_dir / "SKILL-alias.md"
    first.symlink_to(second)
    second.symlink_to(first)

    with pytest.raises(MalformedSourceError):
        discover_skill_inventory(tmp_path / ".claude" / "skills", recursive=False)


def test_discover_recursive_finds_all_three_plugin_cache_depth_shapes(tmp_path: Path) -> None:
    cache_dir = tmp_path / ".claude" / "plugins" / "cache"
    _write(
        build_plugin_cache_skill_path(
            tmp_path, skill_name="skill-creator", shape="marketplace_hash"
        ),
        build_skill_md_text(name="skill-creator"),
    )
    _write(
        build_plugin_cache_skill_path(
            tmp_path, skill_name="orchestrate", shape="plugin_hash_skill"
        ),
        build_skill_md_text(name="orchestrate"),
    )
    _write(
        build_plugin_cache_skill_path(tmp_path, skill_name="scout", shape="plugin_version_skills"),
        build_skill_md_text(name="scout"),
    )

    inventory = discover_skill_inventory(cache_dir, recursive=True)

    assert {entry.skill_name for entry in inventory} == {"skill-creator", "orchestrate", "scout"}


def test_file_changed_mid_read_raises_source_changed_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skills_dir = tmp_path / ".claude" / "skills"
    _write(build_skill_md_path(tmp_path, skill_name="tdd"), build_skill_md_text(name="tdd"))

    real_stat = Path.stat
    call_count = 0

    def fake_stat(self: Path) -> object:
        nonlocal call_count
        call_count += 1
        result = real_stat(self)
        if call_count == 1:
            return result
        return SimpleNamespace(st_mtime_ns=result.st_mtime_ns + 1, st_size=result.st_size)

    monkeypatch.setattr(Path, "stat", fake_stat)

    with pytest.raises(SourceChangedError):
        discover_skill_inventory(skills_dir, recursive=False)


def test_normalize_skill_name_strips_the_owner_namespace_prefix() -> None:
    assert normalize_skill_name("craft:python-engineering-standards") == (
        "python-engineering-standards"
    )


def test_normalize_skill_name_leaves_a_bare_name_unchanged() -> None:
    assert normalize_skill_name("skill-creator") == "skill-creator"


def test_normalize_skill_name_splits_on_the_last_colon_only() -> None:
    assert normalize_skill_name("a:b:c") == "c"


def test_merge_skill_inventory_keeps_the_oldest_revision_on_a_name_collision() -> None:
    older = build_source_revision(mtime_ns=100, content_hash="older")
    newer = build_source_revision(mtime_ns=200, content_hash="newer")
    entries = (
        _entry("tdd", newer),
        _entry("tdd", older),
    )

    merged = merge_skill_inventory(entries)

    assert merged["tdd"] == older


def test_availability_is_true_when_revision_predates_the_spawn() -> None:
    inventory = {"tdd": build_source_revision(mtime_ns=_epoch_ns(_STARTED_AT - timedelta(days=1)))}

    state = resolve_skill_availability(inventory, skill_name="tdd", started_at=_STARTED_AT)

    assert state == KnownState.TRUE


def test_availability_is_unknown_when_revision_postdates_the_spawn() -> None:
    inventory = {"tdd": build_source_revision(mtime_ns=_epoch_ns(_STARTED_AT + timedelta(days=1)))}

    state = resolve_skill_availability(inventory, skill_name="tdd", started_at=_STARTED_AT)

    assert state == KnownState.UNKNOWN


def test_availability_is_unknown_when_the_skill_is_absent_from_the_inventory() -> None:
    state = resolve_skill_availability({}, skill_name="tdd", started_at=_STARTED_AT)

    assert state == KnownState.UNKNOWN


def _entry(skill_name: str, revision: SourceRevision) -> SkillInventoryEntry:
    return SkillInventoryEntry(skill_name=skill_name, revision=revision)


def _epoch_ns(moment: datetime) -> int:
    return int(moment.timestamp() * 1_000_000_000)
