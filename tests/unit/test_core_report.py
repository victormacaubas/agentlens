"""The core report workflow: discover, persist, query both windows, and render."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentlens.core.report import generate_report
from agentlens.core.session import FORMAT_JSON
from agentlens.errors import SourceError
from agentlens.models.session_facts import SessionFacts
from agentlens.models.skill_signals import SessionSkillSignal
from agentlens.render.artifact import report_artifact_path
from agentlens.store import Store
from tests.factories import (
    build_fact_session,
    build_resolved_window,
    build_session_facts,
    build_session_identity,
    build_session_skill_signal,
    build_tool_invocation_pair,
    build_transcript_path,
    build_transcript_text,
    build_unparseable_line,
)
from tests.fakes import FakeClock

_GENERATED_AT = datetime(2026, 2, 8, tzinfo=UTC)

# Covers `tests.factories.DEFAULT_TIMESTAMP`, so a freshly discovered transcript
# built with default factory timestamps falls inside this window.
_DISCOVERY_WINDOW = build_resolved_window(
    current_start=datetime(2025, 12, 25, tzinfo=UTC),
    current_end=datetime(2026, 1, 2, tzinfo=UTC),
    prior_start=datetime(2025, 12, 18, tzinfo=UTC),
    prior_end=datetime(2025, 12, 25, tzinfo=UTC),
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _write_transcript(path: Path) -> None:
    _write(path, build_transcript_text(build_tool_invocation_pair()))


def _session_facts(
    *,
    session_id: str,
    started_at: datetime,
    agent_type: str = "implementer",
    skill_signals: tuple[SessionSkillSignal, ...] = (),
) -> SessionFacts:
    session = build_fact_session(
        identity=build_session_identity(session_id=session_id),
        started_at=started_at,
        agent_type=agent_type,
    )
    return build_session_facts(session=session, skill_signals=skill_signals)


def test_dry_run_includes_newly_discovered_spawns_without_writing_the_store(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    claude_root = home / ".claude"
    window = _DISCOVERY_WINDOW
    _write_transcript(build_transcript_path(home, raw_session_id="new-spawn"))
    store_path = tmp_path / "store" / "agentlens.db"

    output = generate_report(
        window=window,
        agent_filter=None,
        store_path=store_path,
        claude_root=claude_root,
        output_format=FORMAT_JSON,
        clock=FakeClock(instant=_GENERATED_AT),
        dry_run=True,
    )

    document = json.loads(output)
    assert len(document["spawns"]) == 1
    assert not store_path.exists()


def test_dry_run_includes_new_spawns_alongside_an_existing_stores_rows(tmp_path: Path) -> None:
    home = tmp_path / "home"
    claude_root = home / ".claude"
    window = _DISCOVERY_WINDOW
    store_path = tmp_path / "store" / "agentlens.db"
    with Store(store_path) as store:
        store.upsert_session(
            _session_facts(session_id="session-existing", started_at=window.current_start)
        )
    before = store_path.read_bytes()
    _write_transcript(build_transcript_path(home, raw_session_id="new-spawn"))

    output = generate_report(
        window=window,
        agent_filter=None,
        store_path=store_path,
        claude_root=claude_root,
        output_format=FORMAT_JSON,
        clock=FakeClock(instant=_GENERATED_AT),
        dry_run=True,
    )

    document = json.loads(output)
    session_ids = {spawn["session_id"] for spawn in document["spawns"]}
    assert "session-existing" in session_ids
    assert len(session_ids) == 2
    assert store_path.read_bytes() == before


def test_dry_run_artifact_mode_logs_the_would_be_path_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.chdir(tmp_path)
    claude_root = tmp_path / "home" / ".claude"
    window = build_resolved_window()
    store_path = tmp_path / "store" / "agentlens.db"

    with caplog.at_level("INFO", logger="agentlens.core.report"):
        summary = generate_report(
            window=window,
            agent_filter=None,
            store_path=store_path,
            claude_root=claude_root,
            output_format=None,
            clock=FakeClock(instant=_GENERATED_AT),
            dry_run=True,
        )

    would_be_path = report_artifact_path(selector=window.selector, agent_filter=None)
    assert str(would_be_path) in summary
    assert not would_be_path.exists()
    assert not (tmp_path / "reports").exists()
    assert not store_path.exists()
    assert any(str(would_be_path) in record.getMessage() for record in caplog.records)


def test_normal_and_dry_run_documents_are_equal_apart_from_generation_timing(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    claude_root = home / ".claude"
    window = _DISCOVERY_WINDOW
    _write_transcript(build_transcript_path(home, raw_session_id="same-source"))
    normal_store_path = tmp_path / "normal" / "agentlens.db"
    dry_run_store_path = tmp_path / "dry" / "agentlens.db"

    normal_output = generate_report(
        window=window,
        agent_filter=None,
        store_path=normal_store_path,
        claude_root=claude_root,
        output_format=FORMAT_JSON,
        clock=FakeClock(instant=_GENERATED_AT),
        dry_run=False,
    )
    dry_run_output = generate_report(
        window=window,
        agent_filter=None,
        store_path=dry_run_store_path,
        claude_root=claude_root,
        output_format=FORMAT_JSON,
        clock=FakeClock(instant=_GENERATED_AT),
        dry_run=True,
    )

    assert json.loads(normal_output) == json.loads(dry_run_output)
    assert not dry_run_store_path.exists()


def test_json_document_reflects_the_current_and_prior_windows(tmp_path: Path) -> None:
    claude_root = tmp_path / "home" / ".claude"
    window = build_resolved_window(
        current_start=datetime(2026, 2, 1, tzinfo=UTC),
        current_end=datetime(2026, 2, 8, tzinfo=UTC),
        prior_start=datetime(2026, 1, 25, tzinfo=UTC),
        prior_end=datetime(2026, 2, 1, tzinfo=UTC),
    )
    store_path = tmp_path / "store" / "agentlens.db"
    with Store(store_path) as store:
        store.upsert_session(
            _session_facts(session_id="session-current", started_at=window.current_start)
        )
        store.upsert_session(
            _session_facts(session_id="session-prior", started_at=window.prior_start)
        )

    output = generate_report(
        window=window,
        agent_filter=None,
        store_path=store_path,
        claude_root=claude_root,
        output_format=FORMAT_JSON,
        clock=FakeClock(instant=_GENERATED_AT),
        dry_run=False,
    )

    document = json.loads(output)
    assert document["schema_version"] == 1
    assert document["generated_at"] == _GENERATED_AT.isoformat()
    assert [spawn["session_id"] for spawn in document["spawns"]] == ["session-current"]
    rollup = document["agent_rollups"][0]
    assert rollup["n_spawns"] == 1
    assert rollup["n_spawns_prior"] == 1


def test_zero_qualifying_spawns_returns_an_empty_but_valid_document(tmp_path: Path) -> None:
    claude_root = tmp_path / "home" / ".claude"
    store_path = tmp_path / "store" / "agentlens.db"
    window = build_resolved_window()

    output = generate_report(
        window=window,
        agent_filter=None,
        store_path=store_path,
        claude_root=claude_root,
        output_format=FORMAT_JSON,
        clock=FakeClock(instant=_GENERATED_AT),
        dry_run=False,
    )

    document = json.loads(output)
    assert document["spawns"] == []
    assert document["agent_rollups"] == []
    assert document["window"]["current_start"] == window.current_start.isoformat()


def test_agent_filter_narrows_spawns_and_rollups(tmp_path: Path) -> None:
    claude_root = tmp_path / "home" / ".claude"
    store_path = tmp_path / "store" / "agentlens.db"
    window = build_resolved_window()
    with Store(store_path) as store:
        store.upsert_session(
            _session_facts(
                session_id="session-implementer",
                started_at=window.current_start,
                agent_type="implementer",
            )
        )
        store.upsert_session(
            _session_facts(
                session_id="session-pathfinder",
                started_at=window.current_start,
                agent_type="pathfinder",
            )
        )

    output = generate_report(
        window=window,
        agent_filter="pathfinder",
        store_path=store_path,
        claude_root=claude_root,
        output_format=FORMAT_JSON,
        clock=FakeClock(instant=_GENERATED_AT),
        dry_run=False,
    )

    document = json.loads(output)
    assert [spawn["agent_type"] for spawn in document["spawns"]] == ["pathfinder"]
    assert [rollup["agent_type"] for rollup in document["agent_rollups"]] == ["pathfinder"]


def test_skill_signals_are_bulk_read_and_attached_to_their_matching_spawn(tmp_path: Path) -> None:
    claude_root = tmp_path / "home" / ".claude"
    store_path = tmp_path / "store" / "agentlens.db"
    window = build_resolved_window()
    with Store(store_path) as store:
        store.upsert_session(
            _session_facts(
                session_id="session-with-skill",
                started_at=window.current_start,
                skill_signals=(
                    build_session_skill_signal(
                        session_id="session-with-skill", skill_name="python-engineering-standards"
                    ),
                ),
            )
        )
        store.upsert_session(
            _session_facts(session_id="session-without-skill", started_at=window.current_start)
        )

    output = generate_report(
        window=window,
        agent_filter=None,
        store_path=store_path,
        claude_root=claude_root,
        output_format=FORMAT_JSON,
        clock=FakeClock(instant=_GENERATED_AT),
        dry_run=False,
    )

    document = json.loads(output)
    spawns_by_session = {spawn["session_id"]: spawn for spawn in document["spawns"]}
    assert [
        signal["skill_name"] for signal in spawns_by_session["session-with-skill"]["skill_signals"]
    ] == ["python-engineering-standards"]
    assert spawns_by_session["session-without-skill"]["skill_signals"] == []


def test_artifact_mode_writes_one_stable_artifact_across_repeated_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    claude_root = tmp_path / "home" / ".claude"
    store_path = tmp_path / "store" / "agentlens.db"
    window = build_resolved_window()
    with Store(store_path) as store:
        store.upsert_session(
            _session_facts(session_id="session-one", started_at=window.current_start)
        )

    first_summary = generate_report(
        window=window,
        agent_filter=None,
        store_path=store_path,
        claude_root=claude_root,
        output_format=None,
        clock=FakeClock(instant=_GENERATED_AT),
        dry_run=False,
    )
    artifacts_after_first = list((tmp_path / "reports").glob("report_*.json"))
    second_summary = generate_report(
        window=window,
        agent_filter=None,
        store_path=store_path,
        claude_root=claude_root,
        output_format=None,
        clock=FakeClock(instant=_GENERATED_AT),
        dry_run=False,
    )
    artifacts_after_second = list((tmp_path / "reports").glob("report_*.json"))

    assert len(artifacts_after_first) == 1
    assert artifacts_after_first == artifacts_after_second
    assert "artifact:" in first_summary
    assert "artifact:" in second_summary


def test_a_malformed_discovered_source_raises_source_error_and_writes_no_store(
    tmp_path: Path,
) -> None:
    claude_root = tmp_path / "home" / ".claude"
    path = build_transcript_path(tmp_path / "home", project="project-one")
    text = build_transcript_text(build_tool_invocation_pair()) + build_unparseable_line() + "\n"
    _write(path, text)
    path.with_suffix(".meta.json").write_text("{not valid json")
    store_path = tmp_path / "store" / "agentlens.db"

    with pytest.raises(SourceError):
        generate_report(
            window=build_resolved_window(),
            agent_filter=None,
            store_path=store_path,
            claude_root=claude_root,
            output_format=FORMAT_JSON,
            clock=FakeClock(instant=_GENERATED_AT),
            dry_run=False,
        )

    assert not store_path.exists()
