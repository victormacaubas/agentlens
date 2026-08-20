"""Session identity: derivation from a subagent transcript's file path."""

from pathlib import Path

import pytest

from agentlens.errors import MalformedSourceError
from agentlens.ingest.transcript import parse_transcript
from tests.factories import (
    build_context_cache,
    build_subagent_source_bundle,
    build_tool_invocation_pair,
    build_transcript_path,
    build_transcript_text,
)


def _write_transcript(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_transcript_text(build_tool_invocation_pair()))


def test_same_transcript_parsed_twice_yields_the_same_session_id(tmp_path: Path) -> None:
    path = build_transcript_path(tmp_path, raw_session_id="same-id")
    _write_transcript(path)

    first = parse_transcript(
        build_subagent_source_bundle(transcript_path=path), context_cache=build_context_cache()
    )
    second = parse_transcript(
        build_subagent_source_bundle(transcript_path=path), context_cache=build_context_cache()
    )

    assert first.session.identity.session_id == second.session.identity.session_id


def test_same_raw_id_in_two_projects_yields_two_session_ids_and_own_projects(
    tmp_path: Path,
) -> None:
    path_a = build_transcript_path(tmp_path, project="project-a", raw_session_id="shared-id")
    path_b = build_transcript_path(tmp_path, project="project-b", raw_session_id="shared-id")
    _write_transcript(path_a)
    _write_transcript(path_b)

    facts_a = parse_transcript(
        build_subagent_source_bundle(transcript_path=path_a), context_cache=build_context_cache()
    )
    facts_b = parse_transcript(
        build_subagent_source_bundle(transcript_path=path_b), context_cache=build_context_cache()
    )

    assert facts_a.session.identity.session_id != facts_b.session.identity.session_id
    assert facts_a.session.identity.source_project == "project-a"
    assert facts_b.session.identity.source_project == "project-b"
    assert facts_a.session.identity.raw_session_id == "shared-id"
    assert facts_b.session.identity.raw_session_id == "shared-id"


def test_refuses_a_path_with_no_owning_projects_directory(tmp_path: Path) -> None:
    path = tmp_path / "agent-orphan.jsonl"
    _write_transcript(path)

    with pytest.raises(MalformedSourceError):
        parse_transcript(
            build_subagent_source_bundle(transcript_path=path),
            context_cache=build_context_cache(),
        )


def test_refuses_a_main_session_path_with_no_subagents_segment(tmp_path: Path) -> None:
    path = tmp_path / ".claude" / "projects" / "project-one" / "session-uuid.jsonl"
    _write_transcript(path)

    with pytest.raises(MalformedSourceError):
        parse_transcript(
            build_subagent_source_bundle(transcript_path=path),
            context_cache=build_context_cache(),
        )
