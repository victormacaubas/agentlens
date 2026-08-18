"""The store: schema creation, the staleness rule, upsert atomicity, and reads."""

import sqlite3
from pathlib import Path

import pytest

from agentlens.errors import StoreError
from agentlens.models.session_facts import SessionFacts
from agentlens.store import Store, UpsertOutcome
from tests.factories import (
    build_fact_session,
    build_fact_tool_event,
    build_session_facts,
    build_session_identity,
    build_source_revision,
)


def _count_rows(path: Path, table: str) -> int:
    with sqlite3.connect(path) as connection:
        row = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()  # noqa: S608
        return int(row[0])


def _count_rows_for_session(path: Path, table: str, session_id: str) -> int:
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            f"SELECT COUNT(*) FROM {table} WHERE session_id = ?",  # noqa: S608
            (session_id,),
        ).fetchone()
        return int(row[0])


def _facts_for(
    *,
    session_id: str,
    content_hash: str = "content-hash-1",
    mtime_ns: int = 1_700_000_000_000_000_000,
    n_events: int = 1,
    **session_kwargs: object,
) -> SessionFacts:
    identity = build_session_identity(session_id=session_id)
    revision = build_source_revision(content_hash=content_hash, mtime_ns=mtime_ns)
    session = build_fact_session(
        identity=identity,
        revision=revision,
        n_invocations=n_events,
        **session_kwargs,  # type: ignore[arg-type]
    )
    events = tuple(
        build_fact_tool_event(session_id=session_id, ordinal=ordinal) for ordinal in range(n_events)
    )
    return build_session_facts(session=session, tool_events=events)


def test_store_creates_database_file_on_first_use(tmp_path: Path) -> None:
    db_path = tmp_path / "nested" / "agentlens.db"
    assert not db_path.exists()
    with Store(db_path):
        pass
    assert db_path.exists()


def test_store_creates_both_tables_on_first_use(tmp_path: Path) -> None:
    db_path = tmp_path / "agentlens.db"
    with Store(db_path):
        pass
    with sqlite3.connect(db_path) as connection:
        table_names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    assert {"fact_session", "fact_tool_event"} <= table_names


def test_store_honors_a_caller_supplied_location(tmp_path: Path) -> None:
    first_path = tmp_path / "first.db"
    second_path = tmp_path / "second.db"
    with Store(first_path) as store:
        store.upsert_session(_facts_for(session_id="session-a"))
    with Store(second_path) as store:
        store.upsert_session(_facts_for(session_id="session-b"))
    assert first_path.exists()
    assert second_path.exists()
    with Store(first_path) as store:
        assert store.read_session("session-a") is not None
        assert store.read_session("session-b") is None
    with Store(second_path) as store:
        assert store.read_session("session-b") is not None
        assert store.read_session("session-a") is None


def test_read_session_returns_none_for_an_unknown_session_id(tmp_path: Path) -> None:
    with Store(tmp_path / "agentlens.db") as store:
        assert store.read_session("does-not-exist") is None


def test_four_spawns_of_the_same_agent_type_are_four_distinct_rows(tmp_path: Path) -> None:
    session_ids = [f"session-{index}" for index in range(4)]
    db_path = tmp_path / "agentlens.db"
    with Store(db_path) as store:
        for index, session_id in enumerate(session_ids):
            outcome = store.upsert_session(
                _facts_for(session_id=session_id, agent_type="implementer", n_events=index + 1)
            )
            assert outcome == UpsertOutcome.REPLACED
        assert _count_rows(db_path, "fact_session") == 4
        for index, session_id in enumerate(session_ids):
            stored = store.read_session(session_id)
            assert stored is not None
            assert stored.session.n_invocations == index + 1
            assert len(stored.tool_events) == index + 1


def test_reingesting_the_same_session_leaves_no_duplicate_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "agentlens.db"
    with Store(db_path) as store:
        store.upsert_session(
            _facts_for(session_id="session-a", content_hash="hash-1", mtime_ns=100, n_events=3)
        )
        outcome = store.upsert_session(
            _facts_for(session_id="session-a", content_hash="hash-2", mtime_ns=200, n_events=3)
        )
        assert outcome == UpsertOutcome.REPLACED
        assert _count_rows(db_path, "fact_session") == 1
        assert _count_rows_for_session(db_path, "fact_tool_event", "session-a") == 3


def test_reingesting_with_fewer_invocations_leaves_no_orphan_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "agentlens.db"
    with Store(db_path) as store:
        store.upsert_session(
            _facts_for(session_id="session-a", content_hash="hash-1", mtime_ns=100, n_events=5)
        )
        outcome = store.upsert_session(
            _facts_for(session_id="session-a", content_hash="hash-2", mtime_ns=200, n_events=2)
        )
        assert outcome == UpsertOutcome.REPLACED
        assert _count_rows_for_session(db_path, "fact_tool_event", "session-a") == 2
        stored = store.read_session("session-a")
        assert stored is not None
        assert len(stored.tool_events) == 2


def test_older_snapshot_is_refused_and_stored_rows_are_untouched(tmp_path: Path) -> None:
    db_path = tmp_path / "agentlens.db"
    with Store(db_path) as store:
        store.upsert_session(
            _facts_for(
                session_id="session-a",
                content_hash="hash-1",
                mtime_ns=200,
                n_events=3,
                agent_type="original",
            )
        )
        outcome = store.upsert_session(
            _facts_for(
                session_id="session-a",
                content_hash="hash-2",
                mtime_ns=100,
                n_events=1,
                agent_type="stale",
            )
        )
        assert outcome == UpsertOutcome.REFUSED_STALE
        stored = store.read_session("session-a")
        assert stored is not None
        assert stored.session.agent_type == "original"
        assert len(stored.tool_events) == 3


def test_identical_snapshot_is_a_no_op(tmp_path: Path) -> None:
    db_path = tmp_path / "agentlens.db"
    with Store(db_path) as store:
        store.upsert_session(
            _facts_for(
                session_id="session-a",
                content_hash="hash-1",
                mtime_ns=100,
                n_events=3,
                agent_type="original",
            )
        )
        outcome = store.upsert_session(
            _facts_for(
                session_id="session-a",
                content_hash="hash-1",
                mtime_ns=999,
                n_events=1,
                agent_type="ignored",
            )
        )
        assert outcome == UpsertOutcome.SKIPPED_IDENTICAL
        stored = store.read_session("session-a")
        assert stored is not None
        assert stored.session.agent_type == "original"
        assert len(stored.tool_events) == 3


def test_an_internally_inconsistent_snapshot_writes_nothing(tmp_path: Path) -> None:
    """Duplicate ordinals within one snapshot violate the tool-event key.

    The write must fail atomically: the offending snapshot's events collide on
    insert, and the existing, previously stored rows for that session are left
    exactly as they were.
    """
    db_path = tmp_path / "agentlens.db"
    with Store(db_path) as store:
        store.upsert_session(
            _facts_for(
                session_id="session-a",
                content_hash="hash-1",
                mtime_ns=100,
                n_events=3,
                agent_type="original",
            )
        )
        identity = build_session_identity(session_id="session-a")
        revision = build_source_revision(content_hash="hash-2", mtime_ns=200)
        session = build_fact_session(identity=identity, revision=revision, agent_type="corrupt")
        duplicated_events = (
            build_fact_tool_event(session_id="session-a", ordinal=0),
            build_fact_tool_event(session_id="session-a", ordinal=0),
        )
        malformed = build_session_facts(session=session, tool_events=duplicated_events)

        with pytest.raises(StoreError):
            store.upsert_session(malformed)

        stored = store.read_session("session-a")
        assert stored is not None
        assert stored.session.agent_type == "original"
        assert len(stored.tool_events) == 3
        assert _count_rows_for_session(db_path, "fact_tool_event", "session-a") == 3
