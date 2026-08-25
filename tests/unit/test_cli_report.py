"""The ``agentlens report`` command: end to end, exit codes, and stream discipline."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agentlens.cli import EXIT_OK, EXIT_UNEXPECTED, main
from tests.factories import (
    build_assistant_record,
    build_sidecar,
    build_tool_result_block,
    build_tool_use_block,
    build_transcript_path,
    build_transcript_text,
    build_user_record,
)


def _iso(instant: datetime) -> str:
    return instant.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _write_subagent_transcript(
    path: Path, *, agent_type: str = "implementer", timestamp: str
) -> None:
    assistant = build_assistant_record(
        uuid="uuid-a1",
        parent_uuid="uuid-user-0",
        timestamp=timestamp,
        content=[build_tool_use_block(tool_use_id="toolu_1")],
        stop_reason="tool_use",
    )
    result = build_user_record(
        uuid="uuid-r1",
        parent_uuid="uuid-a1",
        timestamp=timestamp,
        content=[build_tool_result_block(tool_use_id="toolu_1", content="done")],
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_transcript_text([assistant, result]))
    path.with_suffix(".meta.json").write_text(json.dumps(build_sidecar(agent_type=agent_type)))


def test_report_help_exits_0_without_a_traceback(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["report", "--help"])

    captured = capsys.readouterr()
    assert exit_code == EXIT_OK
    assert "--since" in captured.out
    assert "Traceback" not in captured.out
    assert "Traceback" not in captured.err


def test_since_7d_emits_real_current_and_prior_numbers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    now = datetime.now(UTC)
    current_a = build_transcript_path(tmp_path, project="project-one", raw_session_id="current-a")
    current_b = build_transcript_path(tmp_path, project="project-two", raw_session_id="current-b")
    prior = build_transcript_path(tmp_path, project="project-one", raw_session_id="prior-a")
    _write_subagent_transcript(current_a, timestamp=_iso(now - timedelta(hours=1)))
    _write_subagent_transcript(current_b, timestamp=_iso(now - timedelta(hours=2)))
    _write_subagent_transcript(prior, timestamp=_iso(now - timedelta(days=8)))
    store_path = tmp_path / "store" / "agentlens.db"

    exit_code = main(["report", "--since", "7d", "--format", "json", "--store", str(store_path)])

    captured = capsys.readouterr()
    assert exit_code == EXIT_OK
    document = json.loads(captured.out)
    assert document["schema_version"] == 1
    assert len(document["spawns"]) == 2
    rollup = document["agent_rollups"][0]
    assert rollup["n_spawns"] == 2
    assert rollup["n_spawns_prior"] == 1


def test_resolved_report_arguments_are_logged_once_on_stderr_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    store_path = tmp_path / "store" / "agentlens.db"

    exit_code = main(["report", "--since", "7d", "--format", "json", "--store", str(store_path)])

    captured = capsys.readouterr()
    assert exit_code == EXIT_OK
    assert captured.err.count("Resolved report arguments") == 1
    assert "Resolved report arguments" not in captured.out


def test_agent_filter_narrows_the_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    now = datetime.now(UTC)
    implementer = build_transcript_path(
        tmp_path, project="project-one", raw_session_id="implementer-a"
    )
    pathfinder = build_transcript_path(
        tmp_path, project="project-two", raw_session_id="pathfinder-a"
    )
    _write_subagent_transcript(
        implementer, agent_type="implementer", timestamp=_iso(now - timedelta(hours=1))
    )
    _write_subagent_transcript(
        pathfinder, agent_type="pathfinder", timestamp=_iso(now - timedelta(hours=1))
    )
    store_path = tmp_path / "store" / "agentlens.db"

    exit_code = main(
        [
            "report",
            "--since",
            "7d",
            "--agent",
            "pathfinder",
            "--format",
            "json",
            "--store",
            str(store_path),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == EXIT_OK
    document = json.loads(captured.out)
    assert [spawn["agent_type"] for spawn in document["spawns"]] == ["pathfinder"]
    assert document["agent_filter"] == "pathfinder"


def test_zero_results_succeeds_with_an_empty_deterministic_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    store_path = tmp_path / "store" / "agentlens.db"

    exit_code = main(["report", "--since", "7d", "--format", "json", "--store", str(store_path)])

    captured = capsys.readouterr()
    assert exit_code == EXIT_OK
    document = json.loads(captured.out)
    assert document["spawns"] == []
    assert document["agent_rollups"] == []


def test_rerunning_the_same_report_scope_leaves_one_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    now = datetime.now(UTC)
    transcript_path = build_transcript_path(tmp_path)
    _write_subagent_transcript(transcript_path, timestamp=_iso(now - timedelta(hours=1)))
    store_path = tmp_path / "store" / "agentlens.db"
    argv = ["report", "--since", "7d", "--store", str(store_path)]

    first_exit = main(argv)
    artifacts_after_first = list((tmp_path / "reports").glob("report_*.json"))
    second_exit = main(argv)
    artifacts_after_second = list((tmp_path / "reports").glob("report_*.json"))

    assert first_exit == EXIT_OK
    assert second_exit == EXIT_OK
    assert len(artifacts_after_first) == 1
    assert artifacts_after_first == artifacts_after_second


def test_missing_window_selector_exits_2_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    store_path = tmp_path / "store" / "agentlens.db"

    exit_code = main(["report", "--store", str(store_path)])

    assert exit_code == 2
    assert not store_path.exists()
    assert not (tmp_path / "reports").exists()


def test_malformed_source_exits_3_and_writes_no_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    now = datetime.now(UTC)
    transcript_path = build_transcript_path(tmp_path)
    _write_subagent_transcript(transcript_path, timestamp=_iso(now - timedelta(hours=1)))
    transcript_path.with_suffix(".meta.json").write_text("{not valid json")
    store_path = tmp_path / "store" / "agentlens.db"

    exit_code = main(["report", "--since", "7d", "--store", str(store_path)])

    assert exit_code == 3
    assert not store_path.exists()


def test_invalid_store_target_exits_4(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    blocking_file = tmp_path / "store"
    blocking_file.write_text("not a directory")
    store_path = blocking_file / "agentlens.db"

    exit_code = main(["report", "--since", "7d", "--store", str(store_path)])

    assert exit_code == 4


def test_unexpected_artifact_write_failure_exits_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "reports").write_text("not a directory")
    store_path = tmp_path / "store" / "agentlens.db"

    exit_code = main(["report", "--since", "7d", "--store", str(store_path)])

    assert exit_code == EXIT_UNEXPECTED


def test_dry_run_json_report_discovers_new_spawns_and_writes_no_store_or_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    now = datetime.now(UTC)
    transcript_path = build_transcript_path(tmp_path)
    _write_subagent_transcript(transcript_path, timestamp=_iso(now - timedelta(hours=1)))
    store_path = tmp_path / "store" / "agentlens.db"

    exit_code = main(
        ["report", "--since", "7d", "--format", "json", "--store", str(store_path), "--dryrun"]
    )

    captured = capsys.readouterr()
    assert exit_code == EXIT_OK
    document = json.loads(captured.out)
    assert len(document["spawns"]) == 1
    assert not store_path.exists()
    assert not (tmp_path / "reports").exists()


def test_dry_run_artifact_mode_writes_nothing_and_names_the_would_be_path_on_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    now = datetime.now(UTC)
    transcript_path = build_transcript_path(tmp_path)
    _write_subagent_transcript(transcript_path, timestamp=_iso(now - timedelta(hours=1)))
    store_path = tmp_path / "store" / "agentlens.db"

    exit_code = main(["report", "--since", "7d", "--store", str(store_path), "--dryrun"])

    captured = capsys.readouterr()
    assert exit_code == EXIT_OK
    assert "artifact:" in captured.out
    assert "Dry run: would write report artifact" not in captured.out
    assert "Dry run: would write report artifact" in captured.err
    assert not store_path.exists()
    assert not (tmp_path / "reports").exists()
