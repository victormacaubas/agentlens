"""Batch-ingesting every subagent source under a projects tree, one transaction at a time."""

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentlens.core.ingest_run import batch_ingest_subagents
from agentlens.errors import SourceError
from agentlens.ingest.context import SubagentContextCache
from agentlens.ingest.discovery import discover_subagent_sources
from agentlens.ingest.transcript import parse_transcript
from agentlens.models.session_facts import SessionFacts
from agentlens.store import Store, UpsertOutcome
from tests.factories import (
    build_agent_definition_text,
    build_skill_md_text,
    build_transcript_path,
    build_transcript_text,
    build_unparseable_line,
    build_user_record,
    write_sidecar,
    write_transcript,
)
from tests.fakes import FakeClock

_PREDATES_EVERY_SPAWN = datetime(2020, 1, 1, tzinfo=UTC).timestamp()
_CLOCK = FakeClock(instant=datetime(2026, 1, 1, tzinfo=UTC))


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _write_transcript(path: Path, *, cwd: str | None = None) -> None:
    """A single-user-record transcript, with its sidecar written separately."""
    write_transcript(path, records=[build_user_record(cwd=cwd)], with_sidecar=False)


def _stored_spawns(store_path: Path) -> tuple[SessionFacts, ...]:
    """Read back every stored spawn through the store's own surface.

    A window wide enough to hold any fixture, so this reports what was stored
    rather than what a window selected.
    """
    if not store_path.exists():
        return ()
    with Store(store_path, clock=_CLOCK) as store:
        spawns = store.read_spawns_in_window(
            datetime(2000, 1, 1, tzinfo=UTC), datetime(2100, 1, 1, tzinfo=UTC), None
        )
        stored = tuple(store.read_session(spawn.identity.session_id) for spawn in spawns)
    return tuple(facts for facts in stored if facts is not None)


def _stored_spawn_count(store_path: Path) -> int:
    return len(_stored_spawns(store_path))


def _snapshot(tree: Path) -> dict[str, tuple[int, int]]:
    """Snapshot every file's revision under ``tree``, plus every symlink's resolved target.

    A directory symlink is not traversed by a plain ``rglob`` on this
    project's Python floor, so its target is walked separately — a write that
    reached through the link instead of the tree itself would otherwise pass
    unnoticed, since the link's own target lives outside ``tree`` entirely.
    """
    entries: dict[str, tuple[int, int]] = {}
    for entry in sorted(tree.rglob("*")):
        if entry.is_symlink():
            target = entry.resolve()
            if target.is_dir():
                for nested in sorted(target.rglob("*")):
                    if nested.is_file():
                        entries[str(nested)] = (nested.stat().st_mtime_ns, nested.stat().st_size)
            elif target.is_file():
                entries[str(target)] = (target.stat().st_mtime_ns, target.stat().st_size)
            continue
        if entry.is_file():
            entries[str(entry.relative_to(tree))] = (
                entry.stat().st_mtime_ns,
                entry.stat().st_size,
            )
    return entries


def test_batch_ingest_discovers_parses_and_persists_across_multiple_projects(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    claude_root = home / ".claude"
    project_one = tmp_path / "project-one"
    project_two = tmp_path / "project-two"

    definition_path = project_one / ".claude" / "agents" / "implementer.md"
    _write(definition_path, build_agent_definition_text(name="implementer"))
    os.utime(definition_path, (_PREDATES_EVERY_SPAWN, _PREDATES_EVERY_SPAWN))

    first = build_transcript_path(home, project="project-one", raw_session_id="agent-a")
    _write_transcript(first, cwd=str(project_one))
    write_sidecar(first, agent_type="implementer")

    second = build_transcript_path(home, project="project-two", raw_session_id="agent-b")
    _write_transcript(second, cwd=str(project_two))

    store_path = tmp_path / "store" / "agentlens.db"

    outcomes = batch_ingest_subagents(
        projects_root=claude_root / "projects",
        claude_root=claude_root,
        store_path=store_path,
        clock=_CLOCK,
    )

    stored = _stored_spawns(store_path)
    bound_definition_ids = {
        facts.session.agent_definition_id
        for facts in stored
        if facts.session.identity.source_project == "project-one"
    }
    with Store(store_path, clock=_CLOCK) as store:
        cataloged = {
            definition_id: store.read_agent_definition(definition_id)
            for definition_id in bound_definition_ids
            if definition_id is not None
        }

    assert len(outcomes) == 2
    assert len(stored) == 2
    assert bound_definition_ids == set(cataloged)
    assert None not in bound_definition_ids
    assert all(definition is not None for definition in cataloged.values())


def test_repeated_batch_ingest_remains_idempotent_under_wal(tmp_path: Path) -> None:
    home = tmp_path / "home"
    claude_root = home / ".claude"
    transcript = build_transcript_path(home, project="project-one", raw_session_id="agent-a")
    _write_transcript(transcript)
    store_path = tmp_path / "store" / "agentlens.db"

    first_outcomes = batch_ingest_subagents(
        projects_root=claude_root / "projects",
        claude_root=claude_root,
        store_path=store_path,
        clock=_CLOCK,
    )
    with Store(store_path, clock=_CLOCK) as store:
        first_sessions = tuple(
            store.read_session(spawn.identity.session_id)
            for spawn in store.read_spawns_in_window(
                datetime(2000, 1, 1, tzinfo=UTC),
                datetime(2100, 1, 1, tzinfo=UTC),
                None,
            )
        )
    second_outcomes = batch_ingest_subagents(
        projects_root=claude_root / "projects",
        claude_root=claude_root,
        store_path=store_path,
        clock=_CLOCK,
    )
    with Store(store_path, clock=_CLOCK) as store:
        second_sessions = tuple(
            store.read_session(spawn.identity.session_id)
            for spawn in store.read_spawns_in_window(
                datetime(2000, 1, 1, tzinfo=UTC),
                datetime(2100, 1, 1, tzinfo=UTC),
                None,
            )
        )

    assert first_outcomes == (UpsertOutcome.REPLACED,)
    assert second_outcomes == (UpsertOutcome.SKIPPED_IDENTICAL,)
    assert first_sessions == second_sessions
    assert len(first_sessions) == 1
    assert first_sessions[0] is not None


def test_unreadable_lines_are_counted_and_do_not_abort_the_batch(tmp_path: Path) -> None:
    home = tmp_path / "home"
    claude_root = home / ".claude"
    path = build_transcript_path(home, project="project-one")
    text = build_transcript_text([build_user_record()]) + build_unparseable_line() + "\n"
    _write(path, text)
    store_path = tmp_path / "store" / "agentlens.db"

    outcomes = batch_ingest_subagents(
        projects_root=claude_root / "projects",
        claude_root=claude_root,
        store_path=store_path,
        clock=_CLOCK,
    )

    stored = _stored_spawns(store_path)

    assert len(outcomes) == 1
    assert len(stored) == 1
    assert stored[0].session.unreadable_line_count == 1


def test_a_hard_source_failure_anywhere_aborts_before_any_write(tmp_path: Path) -> None:
    home = tmp_path / "home"
    claude_root = home / ".claude"
    good = build_transcript_path(home, project="project-one", raw_session_id="agent-good")
    _write_transcript(good)
    bad = build_transcript_path(home, project="project-two", raw_session_id="agent-bad")
    _write_transcript(bad)
    bad.with_suffix(".meta.json").write_text("{not valid json")
    store_path = tmp_path / "store" / "agentlens.db"

    with pytest.raises(SourceError):
        batch_ingest_subagents(
            projects_root=claude_root / "projects",
            claude_root=claude_root,
            store_path=store_path,
            clock=_CLOCK,
        )

    assert not store_path.exists()


def test_a_hard_source_failure_leaves_an_existing_store_untouched(tmp_path: Path) -> None:
    home = tmp_path / "home"
    claude_root = home / ".claude"
    good = build_transcript_path(home, project="project-one", raw_session_id="agent-good")
    _write_transcript(good)
    store_path = tmp_path / "store" / "agentlens.db"
    batch_ingest_subagents(
        projects_root=claude_root / "projects",
        claude_root=claude_root,
        store_path=store_path,
        clock=_CLOCK,
    )
    assert _stored_spawn_count(store_path) == 1

    bad = build_transcript_path(home, project="project-two", raw_session_id="agent-bad")
    _write_transcript(bad)
    bad.with_suffix(".meta.json").write_text("{not valid json")

    with pytest.raises(SourceError):
        batch_ingest_subagents(
            projects_root=claude_root / "projects",
            claude_root=claude_root,
            store_path=store_path,
            clock=_CLOCK,
        )

    assert _stored_spawn_count(store_path) == 1


def test_no_path_under_claude_changes_across_success_failure_and_dry_run(
    tmp_path: Path,
) -> None:
    """Neither a successful batch, a failing one, nor a parse-only ("dry run") pass writes anywhere.

    The synthetic tree mirrors the real machine's shape: a symlinked
    definition with an absolute target and a symlinked skill directory with a
    relative target, both resolving outside ``.claude`` entirely.
    """
    home = tmp_path / "home"
    claude_root = home / ".claude"

    definition_target = tmp_path / "external-defs" / "implementer.md"
    _write(definition_target, build_agent_definition_text(name="implementer"))
    definition_link = claude_root / "agents" / "implementer.md"
    definition_link.parent.mkdir(parents=True, exist_ok=True)
    definition_link.symlink_to(definition_target.absolute())

    skill_target_dir = tmp_path / "external-skills" / "example-skill"
    _write(skill_target_dir / "SKILL.md", build_skill_md_text(name="example-skill"))
    skill_link = claude_root / "skills" / "example-skill"
    skill_link.parent.mkdir(parents=True, exist_ok=True)
    skill_link.symlink_to(Path("..") / ".." / "external-skills" / "example-skill")

    good = build_transcript_path(home, project="project-one", raw_session_id="agent-good")
    _write_transcript(good)
    store_path = tmp_path / "store" / "agentlens.db"

    before = _snapshot(claude_root)
    outcomes = batch_ingest_subagents(
        projects_root=claude_root / "projects",
        claude_root=claude_root,
        store_path=store_path,
        clock=_CLOCK,
    )
    assert len(outcomes) == 1
    assert _snapshot(claude_root) == before

    bad = build_transcript_path(home, project="project-two", raw_session_id="agent-bad")
    _write_transcript(bad)
    bad.with_suffix(".meta.json").write_text("{not valid json")

    before = _snapshot(claude_root)
    with pytest.raises(SourceError):
        batch_ingest_subagents(
            projects_root=claude_root / "projects",
            claude_root=claude_root,
            store_path=store_path,
            clock=_CLOCK,
        )
    assert _snapshot(claude_root) == before

    bad.with_suffix(".meta.json").unlink()
    before = _snapshot(claude_root)
    context_cache = SubagentContextCache(claude_root)
    parsed = tuple(
        parse_transcript(bundle, context_cache=context_cache)
        for bundle in discover_subagent_sources(claude_root / "projects")
    )
    assert len(parsed) == 2
    assert _snapshot(claude_root) == before
