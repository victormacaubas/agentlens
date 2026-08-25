"""The ``agentlens session`` command: end to end, exit codes, and stream discipline."""

import json
import logging
import os
import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agentlens.cli import EXIT_OK, main
from agentlens.ingest.narrative import build_spawn_narrative
from agentlens.ingest.reading import read_transcript
from agentlens.ingest.sidecar import read_sidecar
from agentlens.judge.prompt import render_prompt
from agentlens.judge.rubric import RUBRIC_VERSION
from agentlens.models.facts import FactSession
from agentlens.models.judging import RubricDimension
from agentlens.store import Store
from agentlens.utils.clock import SystemClock
from agentlens.utils.hashing import hash_text
from tests.factories import (
    build_subagent_source_bundle,
    build_tool_invocation_pair,
    build_transcript_path,
    build_transcript_text,
    build_verdict_claim,
    build_verdict_claim_identity,
    write_transcript,
)


def _stored_spawns(store_path: Path) -> tuple[FactSession, ...]:
    """Every stored spawn, read through the store's own surface.

    A window wide enough to hold any fixture, so this reports what was stored
    rather than what a window selected.
    """
    if not store_path.exists():
        return ()
    with Store(store_path, clock=SystemClock()) as store:
        return store.read_spawns_in_window(
            datetime(2000, 1, 1, tzinfo=UTC), datetime(2100, 1, 1, tzinfo=UTC), None
        )


def _stored_spawn_count(store_path: Path) -> int:
    return len(_stored_spawns(store_path))


def _stored_verdict_count(store_path: Path) -> int:
    """Count verdicts across every stored spawn, via the per-spawn verdict read."""
    if not store_path.exists():
        return 0
    session_ids = [spawn.identity.session_id for spawn in _stored_spawns(store_path)]
    with Store(store_path, clock=SystemClock()) as store:
        return sum(len(store.read_verdicts_for_session(session_id)) for session_id in session_ids)


def _judge_input_hash(transcript_path: Path) -> str:
    bundle = build_subagent_source_bundle(transcript_path=transcript_path)
    transcript = read_transcript(bundle.transcript_path)
    sidecar = read_sidecar(bundle.sidecar_path)
    return hash_text(render_prompt(build_spawn_narrative(transcript.records, sidecar=sidecar)))


def _install_fake_claude(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, envelope: Mapping[str, object]
) -> None:
    """Put a fake ``claude`` executable on ``PATH`` that prints ``envelope`` and exits 0.

    Lets the ``--score`` path run end to end through the real
    ``ClaudeCliJudge`` without a real installed CLI, real auth, or
    ``unittest.mock``: the real backend still does a real
    ``subprocess.run``, only the binary it finds on ``PATH`` is substituted.
    """
    bin_dir = tmp_path / "fake-bin"
    bin_dir.mkdir()
    script = bin_dir / "claude"
    script.write_text(
        f"#!/usr/bin/env python3\nimport sys\nsys.stdout.write({json.dumps(dict(envelope))!r})\n"
    )
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")


def _scored_envelope() -> dict[str, object]:
    return {
        "is_error": False,
        "result": None,
        "total_cost_usd": 0.011002,
        "usage": {"input_tokens": 675, "output_tokens": 52},
        "modelUsage": {"claude-sonnet-5": {}},
        "duration_ms": 4820,
        "structured_output": {
            "overall_score": 4,
            "dimensions": {
                dimension.value: {"score": 4, "evidence": ["Evidence."]}
                for dimension in RubricDimension
            },
            "suggested_fixes": [],
        },
    }


def _not_logged_in_envelope() -> dict[str, object]:
    return {
        "is_error": True,
        "result": "Not logged in · Please run /login",
        "total_cost_usd": 0,
    }


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
    monkeypatch.setenv("HOME", str(tmp_path))
    transcript_path = build_transcript_path(tmp_path)
    write_transcript(transcript_path)
    store_path = tmp_path / "store" / "agentlens.db"

    exit_code = main(["session", "--file", str(transcript_path), "--store", str(store_path)])

    assert exit_code == EXIT_OK
    assert store_path.exists()
    assert _stored_spawn_count(store_path) == 1
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
    monkeypatch.setenv("HOME", str(tmp_path))
    transcript_path = build_transcript_path(tmp_path)
    write_transcript(transcript_path)
    store_path = tmp_path / "store" / "agentlens.db"

    exit_code = main(["session", "--file", str(transcript_path), "--store", str(store_path)])

    assert exit_code == EXIT_OK
    stored = _stored_spawns(store_path)
    assert len(stored) == 1
    spawn = stored[0]
    assert spawn.agent_id
    assert spawn.agent_definition_id is None
    assert spawn.parent_session_id
    assert spawn.started_at
    assert spawn.task_prompt_len > 0
    assert spawn.n_skills_fired == 0
    assert spawn.derivation_fingerprint
    assert spawn.derivation_observed_mtime_ns > 0


def test_nonexistent_transcript_path_exits_3_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
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
    monkeypatch.setenv("HOME", str(tmp_path))
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
    monkeypatch.setenv("HOME", str(tmp_path))
    transcript_path = build_transcript_path(tmp_path)
    write_transcript(transcript_path)
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
    assert document["schema_version"] == 3
    assert len(document["spawns"]) == 1
    resolved_lines = [
        line for line in captured.err.splitlines() if "Resolved session arguments" in line
    ]
    assert len(resolved_lines) == 1
    assert not (tmp_path / "reports").exists()


def test_rerunning_the_same_transcript_leaves_the_row_count_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    transcript_path = build_transcript_path(tmp_path)
    write_transcript(transcript_path)
    store_path = tmp_path / "store" / "agentlens.db"
    argv = ["session", "--file", str(transcript_path), "--store", str(store_path)]

    first_exit = main(argv)
    first_count = _stored_spawn_count(store_path)
    second_exit = main(argv)
    second_count = _stored_spawn_count(store_path)

    assert first_exit == EXIT_OK
    assert second_exit == EXIT_OK
    assert first_count == 1
    assert second_count == first_count


def test_dry_run_writes_neither_the_store_nor_the_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    transcript_path = build_transcript_path(tmp_path)
    write_transcript(transcript_path)
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
    monkeypatch.setenv("HOME", str(tmp_path))
    transcript_path = build_transcript_path(tmp_path)
    write_transcript(transcript_path)
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
    monkeypatch.setenv("HOME", str(tmp_path))
    transcript_path = build_transcript_path(tmp_path)
    write_transcript(transcript_path)
    claude_dir = tmp_path / ".claude"
    before = _snapshot(claude_dir)
    missing_path = build_transcript_path(tmp_path, raw_session_id="does-not-exist")
    store_path = tmp_path / "store" / "agentlens.db"

    exit_code = main(["session", "--file", str(missing_path), "--store", str(store_path)])

    assert exit_code == 3
    assert _snapshot(claude_dir) == before


def test_score_flag_persists_a_verdict_and_the_json_document_carries_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    _install_fake_claude(tmp_path, monkeypatch, envelope=_scored_envelope())
    transcript_path = build_transcript_path(tmp_path)
    write_transcript(transcript_path)
    store_path = tmp_path / "store" / "agentlens.db"

    exit_code = main(
        [
            "session",
            "--file",
            str(transcript_path),
            "--store",
            str(store_path),
            "--score",
            "--format",
            "json",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == EXIT_OK
    assert _stored_verdict_count(store_path) == 1
    document = json.loads(captured.out)
    assert "verdict" in document["spawns"][0]


def test_judge_failure_exits_5_and_keeps_the_deterministic_facts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    _install_fake_claude(tmp_path, monkeypatch, envelope=_not_logged_in_envelope())
    transcript_path = build_transcript_path(tmp_path)
    write_transcript(transcript_path)
    store_path = tmp_path / "store" / "agentlens.db"

    exit_code = main(
        ["session", "--file", str(transcript_path), "--store", str(store_path), "--score"]
    )

    assert exit_code == 5
    assert _stored_spawn_count(store_path) == 1
    assert _stored_verdict_count(store_path) == 0


def test_claimed_elsewhere_exits_0_without_running_the_judge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    transcript_path = build_transcript_path(tmp_path)
    write_transcript(transcript_path)
    store_path = tmp_path / "store" / "agentlens.db"
    clock = SystemClock()

    assert main(["session", "--file", str(transcript_path), "--store", str(store_path)]) == EXIT_OK
    with Store(store_path, clock=clock) as store:
        rows = store.read_spawns_in_window(
            datetime(2000, 1, 1, tzinfo=UTC),
            datetime(2100, 1, 1, tzinfo=UTC),
            None,
        )
        assert len(rows) == 1
        identity = build_verdict_claim_identity(
            session_id=rows[0].identity.session_id,
            judge_input_hash=_judge_input_hash(transcript_path),
            rubric_version=RUBRIC_VERSION,
            requested_model="sonnet",
        )
        store.acquire_verdict_claim(
            build_verdict_claim(
                identity=identity,
                owner="other-scorer",
                expires_at=clock.now() + timedelta(minutes=3),
            )
        )

    exit_code = main(
        ["session", "--file", str(transcript_path), "--store", str(store_path), "--score"]
    )

    assert exit_code == EXIT_OK
    assert _stored_verdict_count(store_path) == 0


def test_source_failure_still_exits_3_when_scoring_was_requested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    transcript_path = build_transcript_path(tmp_path)
    store_path = tmp_path / "store" / "agentlens.db"

    exit_code = main(
        ["session", "--file", str(transcript_path), "--store", str(store_path), "--score"]
    )

    assert exit_code == 3
    assert not store_path.exists()


def test_resolved_arguments_are_logged_with_scoring_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    _install_fake_claude(tmp_path, monkeypatch, envelope=_scored_envelope())
    transcript_path = build_transcript_path(tmp_path)
    write_transcript(transcript_path)
    store_path = tmp_path / "store" / "agentlens.db"

    with caplog.at_level(logging.INFO, logger="agentlens.cli"):
        exit_code = main(
            [
                "session",
                "--file",
                str(transcript_path),
                "--store",
                str(store_path),
                "--score",
                "--judge-model",
                "opus",
            ]
        )

    assert exit_code == EXIT_OK
    resolved_messages = [
        record.message
        for record in caplog.records
        if "Resolved session arguments" in record.message
    ]
    assert len(resolved_messages) == 1
    payload = json.loads(resolved_messages[0].split(": ", 1)[1])
    assert payload["score"] is True
    assert payload["requested_model"] == "opus"
    assert isinstance(payload["owner"], str)
    assert re.fullmatch(r"[A-Za-z0-9_-]+", payload["owner"])
    assert str(tmp_path) not in payload["owner"]


def test_resolved_arguments_name_scoring_as_unrequested_when_the_flag_is_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    transcript_path = build_transcript_path(tmp_path)
    write_transcript(transcript_path)
    store_path = tmp_path / "store" / "agentlens.db"

    with caplog.at_level(logging.INFO, logger="agentlens.cli"):
        exit_code = main(["session", "--file", str(transcript_path), "--store", str(store_path)])

    assert exit_code == EXIT_OK
    resolved_messages = [
        record.message
        for record in caplog.records
        if "Resolved session arguments" in record.message
    ]
    assert len(resolved_messages) == 1
    payload = json.loads(resolved_messages[0].split(": ", 1)[1])
    assert payload["score"] is False
    assert payload["requested_model"] == "sonnet"
    assert re.fullmatch(r"[A-Za-z0-9_-]+", payload["owner"])


def test_session_invocations_log_distinct_random_scoring_owners(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    transcript_path = build_transcript_path(tmp_path)
    write_transcript(transcript_path)
    store_path = tmp_path / "store" / "agentlens.db"
    argv = ["session", "--file", str(transcript_path), "--store", str(store_path)]

    with caplog.at_level(logging.INFO, logger="agentlens.cli"):
        assert main(argv) == EXIT_OK
        assert main(argv) == EXIT_OK

    owners = [
        json.loads(record.message.split(": ", 1)[1])["owner"]
        for record in caplog.records
        if "Resolved session arguments" in record.message
    ]
    assert len(owners) == 2
    assert owners[0] != owners[1]


def test_dry_run_with_score_writes_nothing_and_logs_the_dry_run_scoring_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    transcript_path = build_transcript_path(tmp_path)
    write_transcript(transcript_path)
    store_path = tmp_path / "store" / "agentlens.db"

    with caplog.at_level(logging.INFO, logger="agentlens"):
        exit_code = main(
            [
                "session",
                "--file",
                str(transcript_path),
                "--store",
                str(store_path),
                "--dryrun",
                "--score",
            ]
        )

    assert exit_code == EXIT_OK
    assert not store_path.exists()
    dry_run_messages = [
        record.message for record in caplog.records if "would score" in record.message
    ]
    assert len(dry_run_messages) == 1
    assert "sonnet" in dry_run_messages[0]
