"""The ``agentlens score`` command: end to end, exit codes, and stream discipline."""

import json
import os
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agentlens.cli import EXIT_CODES, EXIT_OK, main
from agentlens.errors import JudgeError
from agentlens.models.judging import RubricDimension
from agentlens.store import Store
from agentlens.utils.clock import SystemClock
from tests.factories import (
    build_assistant_record,
    build_sidecar,
    build_tool_result_block,
    build_tool_use_block,
    build_transcript_path,
    build_transcript_text,
    build_user_record,
)

_EXIT_JUDGE_FAILURE = EXIT_CODES[JudgeError]


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


def _install_fake_claude(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    envelopes: Sequence[Mapping[str, object]],
    directory_name: str = "fake-bin",
) -> Path:
    """Put a fake ``claude`` executable on ``PATH`` that plays back ``envelopes`` in order.

    Once ``envelopes`` is exhausted, its last entry repeats for every further
    call, the same way ``tests.fakes.FakeJudgeBackend`` behaves. Lets a real
    ``ClaudeCliJudge`` run end to end through a real ``subprocess.run``, only
    substituting the binary it finds on ``PATH``.
    """
    bin_dir = tmp_path / directory_name
    bin_dir.mkdir()
    counter_path = tmp_path / "fake-claude-calls.txt"
    counter_path.write_text("0")
    envelopes_path = tmp_path / "fake-claude-envelopes.json"
    envelopes_path.write_text(json.dumps([dict(envelope) for envelope in envelopes]))
    script = bin_dir / "claude"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        f"envelopes = json.load(open({str(envelopes_path)!r}))\n"
        f"counter_path = {str(counter_path)!r}\n"
        "n = int(open(counter_path).read())\n"
        "open(counter_path, 'w').write(str(n + 1))\n"
        "idx = min(n, len(envelopes) - 1)\n"
        "print(json.dumps(envelopes[idx]))\n"
    )
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return counter_path


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


def _stored_verdict_count(store_path: Path) -> int:
    if not store_path.exists():
        return 0
    with Store(store_path, clock=SystemClock()) as store:
        rows = store.read_spawns_in_window(
            datetime(2000, 1, 1, tzinfo=UTC), datetime(2100, 1, 1, tzinfo=UTC), None
        )
        return sum(len(store.read_verdicts_for_session(row.identity.session_id)) for row in rows)


def _install_dry_run_canary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return _install_fake_claude(
        tmp_path,
        monkeypatch,
        envelopes=[_scored_envelope()],
        directory_name="dryrun-canary-bin",
    )


def _assert_dry_run_canary_untouched(counter_path: Path) -> None:
    assert counter_path.read_text() == "0"


def test_score_help_exits_0_without_a_traceback(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["score", "--help"])

    captured = capsys.readouterr()
    assert exit_code == EXIT_OK
    assert "--since" in captured.out
    assert "Traceback" not in captured.out
    assert "Traceback" not in captured.err


def test_a_scoring_run_covers_and_scores_every_spawn_in_the_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    _install_fake_claude(tmp_path, monkeypatch, envelopes=[_scored_envelope()])
    now = datetime.now(UTC)
    transcript_a = build_transcript_path(tmp_path, project="project-one", raw_session_id="a")
    transcript_b = build_transcript_path(tmp_path, project="project-two", raw_session_id="b")
    _write_subagent_transcript(transcript_a, timestamp=_iso(now - timedelta(hours=1)))
    _write_subagent_transcript(transcript_b, timestamp=_iso(now - timedelta(hours=2)))
    store_path = tmp_path / "store" / "agentlens.db"

    exit_code = main(["score", "--since", "7d", "--format", "json", "--store", str(store_path)])

    captured = capsys.readouterr()
    assert exit_code == EXIT_OK
    document = json.loads(captured.out)
    assert document["scored"] == 2
    assert document["reused"] == 0
    assert document["failed"] == 0
    assert "stop_reason" not in document
    assert _stored_verdict_count(store_path) == 2


def test_resolved_score_arguments_are_logged_once_on_stderr_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    store_path = tmp_path / "store" / "agentlens.db"

    exit_code = main(["score", "--since", "7d", "--format", "json", "--store", str(store_path)])

    captured = capsys.readouterr()
    assert exit_code == EXIT_OK
    assert captured.err.count("Resolved score arguments") == 1
    assert "Resolved score arguments" not in captured.out


def test_agent_filter_excludes_other_agent_types_from_scoring_and_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    _install_fake_claude(tmp_path, monkeypatch, envelopes=[_scored_envelope()])
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
            "score",
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
    assert document["scored"] == 1
    assert _stored_verdict_count(store_path) == 1


def test_agent_filter_matching_nothing_succeeds_with_everything_at_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    now = datetime.now(UTC)
    transcript_path = build_transcript_path(tmp_path)
    _write_subagent_transcript(
        transcript_path, agent_type="implementer", timestamp=_iso(now - timedelta(hours=1))
    )
    store_path = tmp_path / "store" / "agentlens.db"

    exit_code = main(
        [
            "score",
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
    assert document["scored"] == 0
    assert document["reused"] == 0
    assert document["skipped"] == 0
    assert document["failed"] == 0


def test_a_zero_coverage_run_renders_as_covered_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    store_path = tmp_path / "store" / "agentlens.db"

    exit_code = main(["score", "--since", "7d", "--store", str(store_path)])

    captured = capsys.readouterr()
    assert exit_code == EXIT_OK
    assert "covered: 0 spawn(s)" in captured.out


def test_a_completed_run_with_some_failed_spawns_exits_0_with_the_failed_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    _install_fake_claude(
        tmp_path,
        monkeypatch,
        envelopes=[
            _not_logged_in_envelope(),
            _not_logged_in_envelope(),
            _not_logged_in_envelope(),
            _scored_envelope(),
        ],
    )
    now = datetime.now(UTC)
    first = build_transcript_path(tmp_path, project="project-one", raw_session_id="first")
    second = build_transcript_path(tmp_path, project="project-two", raw_session_id="second")
    _write_subagent_transcript(first, timestamp=_iso(now - timedelta(hours=2)))
    _write_subagent_transcript(second, timestamp=_iso(now - timedelta(hours=1)))
    store_path = tmp_path / "store" / "agentlens.db"

    exit_code = main(["score", "--since", "7d", "--format", "json", "--store", str(store_path)])

    captured = capsys.readouterr()
    assert exit_code == EXIT_OK
    document = json.loads(captured.out)
    assert document["scored"] == 1
    assert document["failed"] == 1
    assert "stop_reason" not in document


def test_a_breaker_stopped_run_exits_with_the_judge_failure_code_and_names_the_cause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    _install_fake_claude(tmp_path, monkeypatch, envelopes=[_not_logged_in_envelope()])
    now = datetime.now(UTC)
    for raw_id in ("a", "b", "c"):
        path = build_transcript_path(tmp_path, project="project-one", raw_session_id=raw_id)
        _write_subagent_transcript(path, timestamp=_iso(now - timedelta(hours=1)))
    store_path = tmp_path / "store" / "agentlens.db"

    exit_code = main(["score", "--since", "7d", "--format", "json", "--store", str(store_path)])

    captured = capsys.readouterr()
    assert exit_code == _EXIT_JUDGE_FAILURE
    document = json.loads(captured.out)
    assert document["failed"] == 3
    assert document["stop_reason"] == "judge_unusable"
    assert "judge" in captured.err.lower()


def test_a_ceiling_stopped_run_exits_0_and_names_the_ceiling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    _install_fake_claude(tmp_path, monkeypatch, envelopes=[_scored_envelope()])
    now = datetime.now(UTC)
    for raw_id in ("a", "b"):
        path = build_transcript_path(tmp_path, project="project-one", raw_session_id=raw_id)
        _write_subagent_transcript(path, timestamp=_iso(now - timedelta(hours=1)))
    store_path = tmp_path / "store" / "agentlens.db"

    exit_code = main(
        [
            "score",
            "--since",
            "7d",
            "--format",
            "json",
            "--store",
            str(store_path),
            "--max-run-cost-usd",
            "0.01",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == EXIT_OK
    document = json.loads(captured.out)
    assert document["scored"] == 1
    assert document["unattempted"] == 1
    assert document["stop_reason"] == "cost_ceiling_reached"


def test_dryrun_over_an_unscored_window_reports_counts_with_no_judge_process_started(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    canary_counter = _install_dry_run_canary(tmp_path, monkeypatch)
    now = datetime.now(UTC)
    for raw_id in ("a", "b"):
        path = build_transcript_path(tmp_path, project="project-one", raw_session_id=raw_id)
        _write_subagent_transcript(path, timestamp=_iso(now - timedelta(hours=1)))
    store_path = tmp_path / "store" / "agentlens.db"

    exit_code = main(
        ["score", "--since", "7d", "--format", "json", "--store", str(store_path), "--dryrun"]
    )

    captured = capsys.readouterr()
    assert exit_code == EXIT_OK
    document = json.loads(captured.out)
    assert document["would_score"] == 2
    assert document["would_reuse"] == 0
    assert document["cost_upper_bound_usd"] == pytest.approx(1.0)
    assert _stored_verdict_count(store_path) == 0
    assert "skip" in captured.err.lower()
    _assert_dry_run_canary_untouched(canary_counter)


def test_dryrun_over_a_fully_reused_window_contributes_nothing_to_the_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    _install_fake_claude(tmp_path, monkeypatch, envelopes=[_scored_envelope()])
    now = datetime.now(UTC)
    for raw_id in ("a", "b"):
        path = build_transcript_path(tmp_path, project="project-one", raw_session_id=raw_id)
        _write_subagent_transcript(path, timestamp=_iso(now - timedelta(hours=1)))
    store_path = tmp_path / "store" / "agentlens.db"
    main(
        [
            "score",
            "--since",
            "7d",
            "--judge-model",
            "claude-sonnet-5",
            "--format",
            "json",
            "--store",
            str(store_path),
        ]
    )
    capsys.readouterr()
    verdicts_before = _stored_verdict_count(store_path)

    canary_counter = _install_dry_run_canary(tmp_path, monkeypatch)
    exit_code = main(
        [
            "score",
            "--since",
            "7d",
            "--judge-model",
            "claude-sonnet-5",
            "--format",
            "json",
            "--store",
            str(store_path),
            "--dryrun",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == EXIT_OK
    document = json.loads(captured.out)
    assert document["would_score"] == 0
    assert document["would_reuse"] == 2
    assert document["cost_upper_bound_usd"] == pytest.approx(0.0)
    assert _stored_verdict_count(store_path) == verdicts_before == 2
    _assert_dry_run_canary_untouched(canary_counter)


def test_dryrun_cost_bound_is_capped_by_the_ceiling_when_the_spawn_count_dominates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    canary_counter = _install_dry_run_canary(tmp_path, monkeypatch)
    now = datetime.now(UTC)
    for i in range(10):
        path = build_transcript_path(tmp_path, project="project-one", raw_session_id=f"s-{i}")
        _write_subagent_transcript(path, timestamp=_iso(now - timedelta(hours=1)))
    store_path = tmp_path / "store" / "agentlens.db"

    exit_code = main(
        [
            "score",
            "--since",
            "7d",
            "--format",
            "json",
            "--store",
            str(store_path),
            "--dryrun",
            "--max-run-cost-usd",
            "0.01",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == EXIT_OK
    document = json.loads(captured.out)
    assert document["would_score"] == 10
    assert document["cost_upper_bound_usd"] == pytest.approx(0.51)
    _assert_dry_run_canary_untouched(canary_counter)


def test_dryrun_bound_reads_as_a_bound_not_an_estimate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    canary_counter = _install_dry_run_canary(tmp_path, monkeypatch)
    now = datetime.now(UTC)
    transcript_path = build_transcript_path(tmp_path)
    _write_subagent_transcript(transcript_path, timestamp=_iso(now - timedelta(hours=1)))
    store_path = tmp_path / "store" / "agentlens.db"

    exit_code = main(["score", "--since", "7d", "--store", str(store_path), "--dryrun"])

    captured = capsys.readouterr()
    assert exit_code == EXIT_OK
    assert "bound" in captured.out.lower()
    assert "estimate" not in captured.out.lower()
    assert "predict" not in captured.out.lower()
    _assert_dry_run_canary_untouched(canary_counter)


def test_dryrun_writes_neither_a_verdict_nor_a_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    canary_counter = _install_dry_run_canary(tmp_path, monkeypatch)
    now = datetime.now(UTC)
    transcript_path = build_transcript_path(tmp_path)
    _write_subagent_transcript(transcript_path, timestamp=_iso(now - timedelta(hours=1)))
    store_path = tmp_path / "store" / "agentlens.db"

    verdicts_before = _stored_verdict_count(store_path)
    exit_code = main(["score", "--since", "7d", "--store", str(store_path), "--dryrun"])
    verdicts_after = _stored_verdict_count(store_path)

    assert exit_code == EXIT_OK
    assert verdicts_before == verdicts_after == 0
    _assert_dry_run_canary_untouched(canary_counter)


def test_dryrun_over_a_never_ingested_transcript_never_writes_the_real_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    canary_counter = _install_dry_run_canary(tmp_path, monkeypatch)
    now = datetime.now(UTC)
    transcript_path = build_transcript_path(tmp_path)
    _write_subagent_transcript(transcript_path, timestamp=_iso(now - timedelta(hours=1)))
    store_path = tmp_path / "store" / "agentlens.db"
    assert not store_path.exists()

    exit_code = main(["score", "--since", "7d", "--store", str(store_path), "--dryrun"])

    assert exit_code == EXIT_OK
    assert not store_path.exists(), (
        "a dry run over a never-before-ingested transcript must not write its "
        "deterministic facts to the real store, only to a disposable clone"
    )
    _assert_dry_run_canary_untouched(canary_counter)
