"""The report artifact: a stable path under reports/, overwritten in place."""

import json
from pathlib import Path

from agentlens.render.artifact import session_artifact_path, write_session_artifact


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
