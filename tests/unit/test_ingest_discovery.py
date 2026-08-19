"""Discovering subagent transcript sources across a Claude projects tree."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from agentlens.errors import SourceChangedError
from agentlens.ingest.discovery import discover_subagent_sources
from agentlens.ingest.transcript import parse_transcript
from tests.factories import (
    build_context_cache,
    build_main_session_path,
    build_transcript_path,
    build_transcript_text,
    build_user_record,
)


def _write_transcript(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_transcript_text([build_user_record()]))


def test_returns_empty_tuple_when_projects_root_does_not_exist(tmp_path: Path) -> None:
    assert discover_subagent_sources(tmp_path / "projects") == ()


def test_discovers_every_subagent_transcript_across_multiple_projects(tmp_path: Path) -> None:
    first = build_transcript_path(tmp_path, project="project-one", raw_session_id="agent-a")
    second = build_transcript_path(tmp_path, project="project-two", raw_session_id="agent-b")
    _write_transcript(first)
    _write_transcript(second)

    discovered = discover_subagent_sources(tmp_path / ".claude" / "projects")

    assert set(discovered) == {first.resolve(), second.resolve()}


def test_ordering_is_deterministic_by_resolved_path_not_filesystem_order(tmp_path: Path) -> None:
    written_last = build_transcript_path(tmp_path, project="project-zzz", raw_session_id="agent-a")
    written_first = build_transcript_path(tmp_path, project="project-aaa", raw_session_id="agent-a")
    _write_transcript(written_last)
    _write_transcript(written_first)

    discovered = discover_subagent_sources(tmp_path / ".claude" / "projects")

    assert discovered == tuple(sorted(discovered, key=str))
    assert discovered == (written_first.resolve(), written_last.resolve())


def test_duplicate_raw_session_ids_across_projects_qualify_to_distinct_sessions(
    tmp_path: Path,
) -> None:
    first = build_transcript_path(tmp_path, project="project-one", raw_session_id="agent-shared")
    second = build_transcript_path(tmp_path, project="project-two", raw_session_id="agent-shared")
    _write_transcript(first)
    _write_transcript(second)

    discovered = discover_subagent_sources(tmp_path / ".claude" / "projects")
    assert len(discovered) == 2

    cache = build_context_cache()
    session_ids = {
        parse_transcript(path, context_cache=cache).session.identity.session_id
        for path in discovered
    }
    assert len(session_ids) == 2


def test_main_session_transcript_is_never_discovered(tmp_path: Path) -> None:
    subagent = build_transcript_path(tmp_path, project="project-one")
    main_session = build_main_session_path(tmp_path, project="project-one")
    _write_transcript(subagent)
    _write_transcript(main_session)

    discovered = discover_subagent_sources(tmp_path / ".claude" / "projects")

    assert discovered == (subagent.resolve(),)


def test_a_discovered_source_that_changes_mid_read_raises_when_parsed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A source that changes after discovery but during its own read is caught at read time.

    Discovery only locates the path; it never inspects content, so a source
    that changes after being discovered but before being read is still
    returned unchanged. Reading that discovered path is where the change is
    caught.
    """
    path = build_transcript_path(tmp_path, project="project-one")
    _write_transcript(path)

    discovered = discover_subagent_sources(tmp_path / ".claude" / "projects")
    assert discovered == (path.resolve(),)

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
        parse_transcript(discovered[0], context_cache=build_context_cache())
