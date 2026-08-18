"""Reading, cataloging, and binding agent definitions.

Fixtures here cover user scope, project scope, the scalar and list
frontmatter forms every real definition on this machine actually uses,
unknown keys, a malformed known field, changed content, and a symlinked
definition, since a file symlink with an absolute target is the shape every
real agent definition takes.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentlens.errors import MalformedSourceError, SourceChangedError
from agentlens.ingest.agent_definitions import (
    content_addressed_definition_id,
    discover_agent_definitions,
    read_agent_definition,
    resolve_agent_definition_binding,
    resolve_effective_definitions,
)
from agentlens.models.agent_definitions import DefinitionScope
from tests.factories import (
    build_agent_definition,
    build_agent_definition_config,
    build_agent_definition_text,
    build_source_revision,
)

_STARTED_AT = datetime(2026, 1, 10, tzinfo=UTC)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _epoch_ns(moment: datetime) -> int:
    return int(moment.timestamp() * 1_000_000_000)


def test_scalar_tools_and_list_skills_are_parsed_from_the_real_observed_shape(
    tmp_path: Path,
) -> None:
    path = tmp_path / "agents" / "implementer.md"
    _write(
        path,
        build_agent_definition_text(
            name="implementer",
            model="claude-sonnet-5[1m]",
            effort="high",
            tools="Read, Write, Edit, Bash, Grep, Glob",
            skills=("craft:python-engineering-standards",),
        ),
    )

    definition = read_agent_definition(path, scope=DefinitionScope.USER, source_project=None)

    assert definition is not None
    assert definition.config.name == "implementer"
    assert definition.config.model == "claude-sonnet-5[1m]"
    assert definition.config.effort == "high"
    assert definition.config.tools == ("Read", "Write", "Edit", "Bash", "Grep", "Glob")
    assert definition.config.skills == ("craft:python-engineering-standards",)


def test_list_shaped_tools_are_accepted_alongside_the_scalar_form(tmp_path: Path) -> None:
    path = tmp_path / "agents" / "researcher.md"
    _write(
        path,
        build_agent_definition_text(name="researcher", tools=("WebFetch", "Read"), skills=None),
    )

    definition = read_agent_definition(path, scope=DefinitionScope.USER, source_project=None)

    assert definition is not None
    assert definition.config.tools == ("WebFetch", "Read")
    assert definition.config.skills == ()


def test_absent_skills_key_is_a_proven_empty_declaration(tmp_path: Path) -> None:
    path = tmp_path / "agents" / "pathfinder.md"
    _write(path, build_agent_definition_text(name="pathfinder", skills=None))

    definition = read_agent_definition(path, scope=DefinitionScope.USER, source_project=None)

    assert definition is not None
    assert definition.config.skills == ()


def test_unknown_frontmatter_keys_are_ignored(tmp_path: Path) -> None:
    path = tmp_path / "agents" / "code-auditor.md"
    _write(
        path,
        build_agent_definition_text(
            name="code-auditor",
            unknown_fields={"permissionMode": "acceptEdits", "color": "cyan"},
        ),
    )

    definition = read_agent_definition(path, scope=DefinitionScope.USER, source_project=None)

    assert definition is not None
    assert definition.config.name == "code-auditor"


def test_a_bracketed_tools_string_is_rejected_with_the_path_and_field_name(
    tmp_path: Path,
) -> None:
    """The one real plugin shape this product must reject rather than guess at."""
    path = tmp_path / "agents" / "skill-reviewer.md"
    _write(path, build_agent_definition_text(name="skill-reviewer", tools='["Read", "Grep"]'))

    with pytest.raises(MalformedSourceError) as excinfo:
        read_agent_definition(path, scope=DefinitionScope.USER, source_project=None)

    assert str(path) in str(excinfo.value)
    assert "tools" in str(excinfo.value)


def test_dangling_symlink_is_treated_as_absent(tmp_path: Path) -> None:
    missing_target = tmp_path / "missing-target.md"
    link = tmp_path / "agents" / "implementer.md"
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(missing_target)

    assert read_agent_definition(link, scope=DefinitionScope.USER, source_project=None) is None


def test_symlink_loop_is_translated_to_a_malformed_source_error(tmp_path: Path) -> None:
    first = tmp_path / "agents" / "a.md"
    second = tmp_path / "agents" / "b.md"
    first.parent.mkdir(parents=True, exist_ok=True)
    first.symlink_to(second)
    second.symlink_to(first)

    with pytest.raises(MalformedSourceError):
        read_agent_definition(first, scope=DefinitionScope.USER, source_project=None)


def test_a_symlinked_definition_reads_the_targets_content_and_revision(tmp_path: Path) -> None:
    """Every real agent definition on this machine is a file symlink with an
    absolute target; ``stat()`` must follow it and report the target's
    revision, not the link's own.
    """
    target = tmp_path / "real-source" / "implementer.md"
    _write(target, build_agent_definition_text(name="implementer", effort="high"))
    link = tmp_path / "agents" / "implementer.md"
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target.absolute())

    definition = read_agent_definition(link, scope=DefinitionScope.USER, source_project=None)

    assert definition is not None
    assert definition.config.name == "implementer"
    assert definition.revision.mtime_ns == target.stat().st_mtime_ns
    assert definition.revision.size == target.stat().st_size


def test_editing_a_symlinked_definitions_target_changes_the_content_identity(
    tmp_path: Path,
) -> None:
    target = tmp_path / "real-source" / "implementer.md"
    _write(target, build_agent_definition_text(name="implementer", effort="high"))
    link = tmp_path / "agents" / "implementer.md"
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target.absolute())

    before = read_agent_definition(link, scope=DefinitionScope.USER, source_project=None)
    _write(target, build_agent_definition_text(name="implementer", effort="medium"))
    after = read_agent_definition(link, scope=DefinitionScope.USER, source_project=None)

    assert before is not None
    assert after is not None
    assert before.agent_definition_id != after.agent_definition_id


def test_repeated_scans_of_unchanged_content_resolve_to_the_same_identity(
    tmp_path: Path,
) -> None:
    path = tmp_path / "agents" / "implementer.md"
    _write(path, build_agent_definition_text(name="implementer"))

    first = read_agent_definition(path, scope=DefinitionScope.USER, source_project=None)
    second = read_agent_definition(path, scope=DefinitionScope.USER, source_project=None)

    assert first is not None
    assert second is not None
    assert first.agent_definition_id == second.agent_definition_id


def test_the_same_content_under_two_scopes_catalogs_as_two_distinct_identities() -> None:
    same_content_hash = "shared-content-hash"

    user_scoped_id = content_addressed_definition_id(
        scope=DefinitionScope.USER, source_project=None, content_hash=same_content_hash
    )
    project_scoped_id = content_addressed_definition_id(
        scope=DefinitionScope.PROJECT,
        source_project="project-one",
        content_hash=same_content_hash,
    )

    assert user_scoped_id != project_scoped_id


def test_file_changed_mid_read_raises_source_changed_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "agents" / "implementer.md"
    _write(path, build_agent_definition_text(name="implementer"))

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
        read_agent_definition(path, scope=DefinitionScope.USER, source_project=None)


def test_discover_returns_an_empty_tuple_for_a_missing_directory(tmp_path: Path) -> None:
    definitions = discover_agent_definitions(
        tmp_path / "does-not-exist", scope=DefinitionScope.USER, source_project=None
    )
    assert definitions == ()


def test_discover_skips_a_dangling_symlink_among_sound_definitions(tmp_path: Path) -> None:
    agents_dir = tmp_path / "agents"
    _write(agents_dir / "implementer.md", build_agent_definition_text(name="implementer"))
    (agents_dir / "ghost.md").symlink_to(tmp_path / "nowhere.md")

    definitions = discover_agent_definitions(
        agents_dir, scope=DefinitionScope.USER, source_project=None
    )

    assert [definition.config.name for definition in definitions] == ["implementer"]


def test_project_scoped_definition_overrides_user_scoped_definition_of_the_same_name() -> None:
    user_definition = build_agent_definition(scope=DefinitionScope.USER)
    project_definition = build_agent_definition(
        scope=DefinitionScope.PROJECT, source_project="project-one"
    )

    effective = resolve_effective_definitions(
        user_definitions=(user_definition,), project_definitions=(project_definition,)
    )

    assert effective["implementer"] == project_definition


def test_an_agent_named_only_in_user_scope_keeps_its_user_scoped_definition() -> None:
    user_definition = build_agent_definition(scope=DefinitionScope.USER)

    effective = resolve_effective_definitions(
        user_definitions=(user_definition,), project_definitions=()
    )

    assert effective["implementer"] == user_definition


def test_binding_succeeds_when_the_definition_predates_the_spawn() -> None:
    definition = build_agent_definition()
    effective = resolve_effective_definitions(
        user_definitions=(definition,), project_definitions=()
    )

    bound = resolve_agent_definition_binding(
        effective_definitions=effective,
        agent_type="implementer",
        started_at=_STARTED_AT + timedelta(days=1),
    )

    assert bound == definition


def test_binding_is_unknown_when_the_only_definition_is_newer_than_the_spawn() -> None:
    newer_revision = build_source_revision(mtime_ns=_epoch_ns(_STARTED_AT + timedelta(days=1)))
    definition = build_agent_definition(revision=newer_revision)
    effective = resolve_effective_definitions(
        user_definitions=(definition,), project_definitions=()
    )

    bound = resolve_agent_definition_binding(
        effective_definitions=effective, agent_type="implementer", started_at=_STARTED_AT
    )

    assert bound is None


def test_binding_is_unknown_when_no_definition_matches_the_agent_type() -> None:
    definition = build_agent_definition()
    effective = resolve_effective_definitions(
        user_definitions=(definition,), project_definitions=()
    )

    bound = resolve_agent_definition_binding(
        effective_definitions=effective, agent_type="researcher", started_at=_STARTED_AT
    )

    assert bound is None


def test_editing_a_definition_re_evaluates_a_previously_bound_spawn_to_unknown() -> None:
    old_revision = build_source_revision(mtime_ns=_epoch_ns(_STARTED_AT))
    old_definition = build_agent_definition(
        revision=old_revision, config=build_agent_definition_config(effort="high")
    )
    effective_before_edit = resolve_effective_definitions(
        user_definitions=(old_definition,), project_definitions=()
    )
    bound_before_edit = resolve_agent_definition_binding(
        effective_definitions=effective_before_edit,
        agent_type="implementer",
        started_at=_STARTED_AT + timedelta(seconds=1),
    )
    assert bound_before_edit == old_definition

    edited_revision = build_source_revision(
        mtime_ns=_epoch_ns(_STARTED_AT + timedelta(days=1)),
        content_hash="edited-content-hash",
    )
    edited_definition = build_agent_definition(
        revision=edited_revision, config=build_agent_definition_config(effort="medium")
    )
    assert edited_definition.agent_definition_id != old_definition.agent_definition_id

    effective_after_edit = resolve_effective_definitions(
        user_definitions=(edited_definition,), project_definitions=()
    )
    bound_after_edit = resolve_agent_definition_binding(
        effective_definitions=effective_after_edit,
        agent_type="implementer",
        started_at=_STARTED_AT + timedelta(seconds=1),
    )

    assert bound_after_edit is None
