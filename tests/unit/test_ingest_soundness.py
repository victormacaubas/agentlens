"""Snapshot soundness: a file that changes between the two revision stats."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from agentlens.errors import SourceChangedError
from agentlens.ingest.transcript import parse_transcript
from tests.factories import (
    build_context_cache,
    build_tool_invocation_pair,
    build_transcript_path,
    build_transcript_text,
)


def test_raises_source_changed_error_when_file_changes_between_the_two_stats(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = build_transcript_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_transcript_text(build_tool_invocation_pair()))

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
        parse_transcript(path, context_cache=build_context_cache())


def test_unchanged_file_is_accepted(tmp_path: Path) -> None:
    path = build_transcript_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_transcript_text(build_tool_invocation_pair()))

    facts = parse_transcript(path, context_cache=build_context_cache())

    assert facts.session.revision.content_hash
