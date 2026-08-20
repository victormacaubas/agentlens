"""The root Click dispatch: help, logging, and stream discipline through ``main``."""

import json
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


def test_root_help_exits_0_and_lists_the_session_subcommand_without_a_traceback(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(["--help"])

    captured = capsys.readouterr()
    assert exit_code == EXIT_OK
    assert "session" in captured.out
    assert "Traceback" not in captured.out
    assert "Traceback" not in captured.err


def test_session_help_exits_0_without_a_traceback(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["session", "--help"])

    captured = capsys.readouterr()
    assert exit_code == EXIT_OK
    assert "--file" in captured.out
    assert "Traceback" not in captured.out
    assert "Traceback" not in captured.err


def test_no_arguments_shows_root_help_and_exits_0(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main([])

    captured = capsys.readouterr()
    assert exit_code == EXIT_OK
    assert "session" in captured.out
    assert "Traceback" not in captured.out


def test_resolved_session_arguments_are_logged_once_on_stderr_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    transcript_path = build_transcript_path(tmp_path)
    _write_transcript(transcript_path)
    store_path = tmp_path / "store" / "agentlens.db"

    exit_code = main(["session", "--file", str(transcript_path), "--store", str(store_path)])

    captured = capsys.readouterr()
    assert exit_code == EXIT_OK
    assert captured.err.count("Resolved session arguments") == 1
    assert "Resolved session arguments" not in captured.out


def test_repeated_main_calls_do_not_duplicate_log_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    transcript_path = build_transcript_path(tmp_path)
    _write_transcript(transcript_path)
    store_path = tmp_path / "store" / "agentlens.db"
    argv = ["session", "--file", str(transcript_path), "--store", str(store_path)]

    first_exit = main(argv)
    first_captured = capsys.readouterr()
    second_exit = main(argv)
    second_captured = capsys.readouterr()

    assert first_exit == EXIT_OK
    assert second_exit == EXIT_OK
    assert first_captured.err.count("Resolved session arguments") == 1
    assert second_captured.err.count("Resolved session arguments") == 1


def test_json_format_stdout_is_one_parseable_document_with_diagnostics_on_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
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

    captured = capsys.readouterr()
    assert exit_code == EXIT_OK
    document = json.loads(captured.out)
    assert document["schema_version"] == 1
    assert "Resolved session arguments" in captured.err


def test_nonexistent_transcript_through_root_dispatch_exits_3_without_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    transcript_path = build_transcript_path(tmp_path)
    store_path = tmp_path / "store" / "agentlens.db"

    exit_code = main(["session", "--file", str(transcript_path), "--store", str(store_path)])

    captured = capsys.readouterr()
    assert exit_code == 3
    assert "Traceback" not in captured.err
    assert "Traceback" not in captured.out
