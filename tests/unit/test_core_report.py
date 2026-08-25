"""The core report workflow: discover, persist, query both windows, and render."""

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentlens.core.report import generate_report
from agentlens.core.session import FORMAT_JSON
from agentlens.errors import SourceError
from agentlens.models.agent_definitions import AgentDefinition
from agentlens.models.session_facts import SessionFacts
from agentlens.models.skill_signals import SessionSkillSignal
from agentlens.render.artifact import report_artifact_path
from agentlens.store import Store
from tests.factories import (
    build_agent_definition_text,
    build_assistant_record,
    build_fact_session,
    build_main_session_path,
    build_resolved_window,
    build_session_facts,
    build_session_identity,
    build_session_skill_signal,
    build_skill_md_path,
    build_skill_md_text,
    build_tool_invocation_pair,
    build_tool_use_block,
    build_transcript_path,
    build_transcript_text,
    build_unparseable_line,
    build_user_record,
    write_sidecar,
    write_transcript,
)
from tests.fakes import FakeClock

_GENERATED_AT = datetime(2026, 2, 8, tzinfo=UTC)
_CLOCK = FakeClock(instant=_GENERATED_AT)

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


_PREDATES_EVERY_SPAWN = datetime(2020, 1, 1, tzinfo=UTC).timestamp()


def _skill_fire_record(skill_name: str) -> dict[str, object]:
    return build_assistant_record(
        uuid="uuid-fire",
        message_id="msg-fire",
        content=[
            build_tool_use_block(
                tool_use_id="toolu-fire", name="Skill", input={"skill": skill_name}
            )
        ],
        stop_reason="tool_use",
    )


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
    write_transcript(build_transcript_path(home, raw_session_id="new-spawn"), with_sidecar=False)
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
    with Store(store_path, clock=_CLOCK) as store:
        store.upsert_session(
            _session_facts(session_id="session-existing", started_at=window.current_start)
        )
    before = store_path.read_bytes()
    write_transcript(build_transcript_path(home, raw_session_id="new-spawn"), with_sidecar=False)

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


def test_report_covers_only_subagent_spawns_when_a_main_session_is_present(
    tmp_path: Path,
) -> None:
    """A main-session transcript beside a subagent must not become a reportable row.

    The subagent's ``parent_session_id`` names the main session, which proves
    the main transcript really was there and was recognised as the parent, so
    the absent row is an exclusion rather than a tree the report never saw.
    """
    home = tmp_path / "home"
    claude_root = home / ".claude"
    store_path = tmp_path / "store" / "agentlens.db"

    write_transcript(build_main_session_path(home, project="project-one"), with_sidecar=False)
    write_transcript(
        build_transcript_path(home, project="project-one", raw_session_id="agent-a"),
        with_sidecar=False,
    )

    document = json.loads(
        generate_report(
            window=_DISCOVERY_WINDOW,
            agent_filter=None,
            store_path=store_path,
            claude_root=claude_root,
            output_format=FORMAT_JSON,
            clock=FakeClock(instant=_GENERATED_AT),
            dry_run=False,
        )
    )

    assert [spawn["session_kind"] for spawn in document["spawns"]] == ["subagent"]
    parent_session_id = document["spawns"][0]["parent_session_id"]
    assert parent_session_id is not None
    with Store(store_path, clock=_CLOCK) as store:
        assert store.read_session(parent_session_id) is None


def test_rebuilding_the_store_from_the_same_source_tree_reproduces_every_deterministic_row(
    tmp_path: Path,
) -> None:
    """The store is a disposable cache, so rebuilding it must not change content.

    Covers all five categories of added data in one pass: the definition
    catalog through a direct catalog read, and session context, skill rows,
    spawn rows, and both windows' aggregates through the rendered document,
    which carries each spawn's whole ``FactSession`` and its bridge rows.
    """
    home = tmp_path / "home"
    claude_root = home / ".claude"
    project = tmp_path / "project-one"

    definition_path = project / ".claude" / "agents" / "implementer.md"
    _write(definition_path, build_agent_definition_text(name="implementer", skills=("tdd",)))
    os.utime(definition_path, (_PREDATES_EVERY_SPAWN, _PREDATES_EVERY_SPAWN))
    _write(build_skill_md_path(project, skill_name="tdd"), build_skill_md_text(name="tdd"))

    declared_only = build_transcript_path(home, project="project-one", raw_session_id="agent-a")
    _write(declared_only, build_transcript_text([build_user_record(cwd=str(project))]))
    write_sidecar(declared_only, agent_type="implementer")

    also_fired = build_transcript_path(home, project="project-one", raw_session_id="agent-b")
    _write(
        also_fired,
        build_transcript_text([build_user_record(cwd=str(project)), _skill_fire_record("tdd")]),
    )
    write_sidecar(also_fired, agent_type="implementer")

    def build(store_path: Path) -> tuple[str, list[AgentDefinition | None]]:
        document = generate_report(
            window=_DISCOVERY_WINDOW,
            agent_filter=None,
            store_path=store_path,
            claude_root=claude_root,
            output_format=FORMAT_JSON,
            clock=FakeClock(instant=_GENERATED_AT),
            dry_run=False,
        )
        with Store(store_path, clock=_CLOCK) as store:
            catalog = [
                store.read_agent_definition(spawn["agent_definition_id"])
                for spawn in json.loads(document)["spawns"]
            ]
        return document, catalog

    first_document, first_catalog = build(tmp_path / "first" / "agentlens.db")
    second_document, second_catalog = build(tmp_path / "second" / "agentlens.db")

    # Pin the population before comparing it. Equality between two empty
    # documents would hold for a tree that discovered nothing at all.
    first = json.loads(first_document)
    assert len(first["spawns"]) == 2
    assert len(first["agent_rollups"]) == 1
    assert [signal["fired"] for spawn in first["spawns"] for signal in spawn["skill_signals"]] == [
        True,
        False,
    ]
    assert all(definition is not None for definition in first_catalog)

    assert first == json.loads(second_document)
    assert first_catalog == second_catalog


def test_normal_and_dry_run_documents_are_equal_apart_from_generation_timing(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    claude_root = home / ".claude"
    window = _DISCOVERY_WINDOW
    write_transcript(build_transcript_path(home, raw_session_id="same-source"), with_sidecar=False)
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
    with Store(store_path, clock=_CLOCK) as store:
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
    with Store(store_path, clock=_CLOCK) as store:
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
    with Store(store_path, clock=_CLOCK) as store:
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
    with Store(store_path, clock=_CLOCK) as store:
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
