"""The report artifact: a stable path under reports/, overwritten in place."""

import json
from pathlib import Path

from agentlens.render.artifact import (
    report_artifact_path,
    session_artifact_path,
    write_report_artifact,
    write_session_artifact,
)
from tests.factories import build_window_selector


def test_session_artifact_path_is_stable_for_a_given_session_id(tmp_path: Path) -> None:
    first = session_artifact_path("session-abc123", reports_dir=tmp_path)
    second = session_artifact_path("session-abc123", reports_dir=tmp_path)

    assert first == second
    assert first == tmp_path / "session_session-abc123.json"


def test_write_session_artifact_creates_reports_directory(tmp_path: Path) -> None:
    reports_dir = tmp_path / "reports"

    path = write_session_artifact(
        {"schema_version": 1}, session_id="session-abc123", reports_dir=reports_dir
    )

    assert path == reports_dir / "session_session-abc123.json"
    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8")) == {"schema_version": 1}


def test_repeated_runs_leave_exactly_one_artifact_file_for_a_session(tmp_path: Path) -> None:
    write_session_artifact({"schema_version": 1}, session_id="session-abc123", reports_dir=tmp_path)
    write_session_artifact(
        {"schema_version": 1, "scoring_status": "unscored"},
        session_id="session-abc123",
        reports_dir=tmp_path,
    )

    matching = list(tmp_path.glob("session_session-abc123*.json"))
    assert len(matching) == 1
    assert json.loads(matching[0].read_text(encoding="utf-8")) == {
        "schema_version": 1,
        "scoring_status": "unscored",
    }


def test_write_session_artifact_for_a_different_session_writes_a_separate_file(
    tmp_path: Path,
) -> None:
    write_session_artifact({"schema_version": 1}, session_id="session-one", reports_dir=tmp_path)
    write_session_artifact({"schema_version": 1}, session_id="session-two", reports_dir=tmp_path)

    assert len(list(tmp_path.glob("session_*.json"))) == 2


def test_report_artifact_path_is_stable_for_the_same_scope(tmp_path: Path) -> None:
    selector = build_window_selector(since_duration="7d")

    first = report_artifact_path(
        selector=selector, agent_filter="implementer", reports_dir=tmp_path
    )
    second = report_artifact_path(
        selector=selector, agent_filter="implementer", reports_dir=tmp_path
    )

    assert first == second
    assert first.parent == tmp_path
    assert first.name.startswith("report_")
    assert first.name.endswith(".json")


def test_report_artifact_path_differs_by_selector_and_by_agent_filter(tmp_path: Path) -> None:
    since_7d = build_window_selector(since_duration="7d")
    since_14d = build_window_selector(since_duration="14d")

    all_agents = report_artifact_path(selector=since_7d, agent_filter=None, reports_dir=tmp_path)
    one_agent = report_artifact_path(
        selector=since_7d, agent_filter="implementer", reports_dir=tmp_path
    )
    different_window = report_artifact_path(
        selector=since_14d, agent_filter=None, reports_dir=tmp_path
    )

    assert len({all_agents, one_agent, different_window}) == 3


def test_report_artifact_filename_never_embeds_the_raw_selector_or_filter_text(
    tmp_path: Path,
) -> None:
    selector = build_window_selector(since_duration="7d; rm -rf /")

    path = report_artifact_path(
        selector=selector, agent_filter="../../etc/passwd", reports_dir=tmp_path
    )

    assert "rm" not in path.name
    assert "etc" not in path.name
    assert ".." not in path.name


def test_write_report_artifact_creates_reports_directory_and_writes_the_document(
    tmp_path: Path,
) -> None:
    selector = build_window_selector(since_duration="7d")
    reports_dir = tmp_path / "reports"

    path = write_report_artifact(
        {"schema_version": 1}, selector=selector, agent_filter=None, reports_dir=reports_dir
    )

    assert path == report_artifact_path(
        selector=selector, agent_filter=None, reports_dir=reports_dir
    )
    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8")) == {"schema_version": 1}


def test_repeated_report_runs_for_the_same_scope_overwrite_one_artifact(tmp_path: Path) -> None:
    selector = build_window_selector(since_duration="7d")

    write_report_artifact(
        {"schema_version": 1, "spawns": []},
        selector=selector,
        agent_filter=None,
        reports_dir=tmp_path,
    )
    write_report_artifact(
        {"schema_version": 1, "spawns": [1]},
        selector=selector,
        agent_filter=None,
        reports_dir=tmp_path,
    )

    matching = list(tmp_path.glob("report_*.json"))
    assert len(matching) == 1
    assert json.loads(matching[0].read_text(encoding="utf-8")) == {
        "schema_version": 1,
        "spawns": [1],
    }


def test_session_and_report_artifacts_leave_the_existing_session_path_unchanged(
    tmp_path: Path,
) -> None:
    assert session_artifact_path("session-abc123", reports_dir=tmp_path) == (
        tmp_path / "session_session-abc123.json"
    )
