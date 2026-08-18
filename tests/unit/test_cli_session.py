"""The ``agentlens session`` command: end to end, exit codes, and stream discipline."""

import json
import sqlite3
from pathlib import Path

import pytest

from agentlens.cli import EXIT_OK, main
from tests.factories import (
    build_sidecar,
    build_tool_invocation_pair,
    build_transcript_path,
    build_transcript_text,
)


def _write_transcript(path: Path, *, with_sidecar: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_transcript_text(build_tool_invocation_pair()))
    if with_sidecar:
        path.with_suffix(".meta.json").write_text(json.dumps(build_sidecar()))


def _fact_session_count(store_path: Path) -> int:
    with sqlite3.connect(store_path) as connection:
        row = connection.execute("SELECT COUNT(*) FROM fact_session").fetchone()
        return int(row[0])


def _snapshot(tree: Path) -> dict[str, tuple[int, int]]:
    return {
        str(file_path.relative_to(tree)): (file_path.stat().st_mtime_ns, file_path.stat().st_size)
        for file_path in sorted(tree.rglob("*"))
        if file_path.is_file()
    }


def test_happy_path_produces_a_populated_store_an_artifact_and_exit_0(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    transcript_path = build_transcript_path(tmp_path)
    _write_transcript(transcript_path)
    store_path = tmp_path / "store" / "agentlens.db"

    exit_code = main(["session", "--file", str(transcript_path), "--store", str(store_path)])

    assert exit_code == EXIT_OK
    assert store_path.exists()
    assert _fact_session_count(store_path) == 1
    artifacts = list((tmp_path / "reports").glob("session_*.json"))
    assert len(artifacts) == 1


def test_deterministic_reporting_context_is_populated_after_the_session_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The expanded ``fact_session`` row round-trips through the full CLI path.

    Proves the new columns are populated for a real run, not only through the
    store's own unit tests, while the command's exit code and output stay the
    ones already pinned above.
    """
    monkeypatch.chdir(tmp_path)
    transcript_path = build_transcript_path(tmp_path)
    _write_transcript(transcript_path)
    store_path = tmp_path / "store" / "agentlens.db"

    exit_code = main(["session", "--file", str(transcript_path), "--store", str(store_path)])

    assert exit_code == EXIT_OK
    with sqlite3.connect(store_path) as connection:
        row = connection.execute(
            "SELECT agent_id, agent_definition_id, parent_session_id, started_at, "
            "task_prompt_len, n_skills_fired, derivation_fingerprint, "
            "derivation_observed_mtime_ns FROM fact_session"
        ).fetchone()
    assert row is not None
    (
        agent_id,
        agent_definition_id,
        parent_session_id,
        started_at,
        task_prompt_len,
        n_skills_fired,
        derivation_fingerprint,
        derivation_observed_mtime_ns,
    ) = row
    assert agent_id
    assert agent_definition_id is None
    assert parent_session_id
    assert started_at
    assert task_prompt_len > 0
    assert n_skills_fired == 0
    assert derivation_fingerprint
    assert derivation_observed_mtime_ns > 0


def test_nonexistent_transcript_path_exits_3_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    transcript_path = build_transcript_path(tmp_path)
    store_path = tmp_path / "store" / "agentlens.db"

    exit_code = main(["session", "--file", str(transcript_path), "--store", str(store_path)])

    assert exit_code == 3
    assert not store_path.exists()
    assert not (tmp_path / "reports").exists()


def test_transcript_outside_a_project_tree_exits_3_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    transcript_path = tmp_path / "agent-loose.jsonl"
    transcript_path.write_text(build_transcript_text(build_tool_invocation_pair()))
    store_path = tmp_path / "store" / "agentlens.db"

    exit_code = main(["session", "--file", str(transcript_path), "--store", str(store_path)])

    assert exit_code == 3
    assert not store_path.exists()
    assert not (tmp_path / "reports").exists()


def test_format_json_writes_only_the_json_document_to_stdout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    transcript_path = build_transcript_path(tmp_path)
    _write_transcript(transcript_path)
    store_path = tmp_path / "store" / "agentlens.db"

    exit_code = main(
        [
            "session",
            "--file",
            str(transcript_path),
            "--store",
            str(store_path),
            "--format",
            "json",
        ]
    )

    assert exit_code == EXIT_OK
    captured = capsys.readouterr()
    document = json.loads(captured.out)
    assert document["schema_version"] == 1
    assert len(document["spawns"]) == 1
    assert not (tmp_path / "reports").exists()


def test_rerunning_the_same_transcript_leaves_the_row_count_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    transcript_path = build_transcript_path(tmp_path)
    _write_transcript(transcript_path)
    store_path = tmp_path / "store" / "agentlens.db"
    argv = ["session", "--file", str(transcript_path), "--store", str(store_path)]

    first_exit = main(argv)
    first_count = _fact_session_count(store_path)
    second_exit = main(argv)
    second_count = _fact_session_count(store_path)

    assert first_exit == EXIT_OK
    assert second_exit == EXIT_OK
    assert first_count == 1
    assert second_count == first_count


def test_dry_run_writes_neither_the_store_nor_the_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    transcript_path = build_transcript_path(tmp_path)
    _write_transcript(transcript_path)
    store_path = tmp_path / "store" / "agentlens.db"

    exit_code = main(
        ["session", "--file", str(transcript_path), "--store", str(store_path), "--dryrun"]
    )

    assert exit_code == EXIT_OK
    assert not store_path.exists()
    assert not (tmp_path / "reports").exists()


def test_success_run_touches_no_file_under_the_claude_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    transcript_path = build_transcript_path(tmp_path)
    _write_transcript(transcript_path)
    claude_dir = tmp_path / ".claude"
    store_path = tmp_path / "store" / "agentlens.db"
    before = _snapshot(claude_dir)

    exit_code = main(["session", "--file", str(transcript_path), "--store", str(store_path)])

    assert exit_code == EXIT_OK
    assert _snapshot(claude_dir) == before


def test_failure_run_touches_no_file_under_the_claude_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    transcript_path = build_transcript_path(tmp_path)
    _write_transcript(transcript_path)
    claude_dir = tmp_path / ".claude"
    before = _snapshot(claude_dir)
    missing_path = build_transcript_path(tmp_path, raw_session_id="does-not-exist")
    store_path = tmp_path / "store" / "agentlens.db"

    exit_code = main(["session", "--file", str(missing_path), "--store", str(store_path)])

    assert exit_code == 3
    assert _snapshot(claude_dir) == before
