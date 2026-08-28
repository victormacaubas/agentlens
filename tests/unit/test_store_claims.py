"""Verdict claims, natural-key verdict reads, and store concurrency configuration."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from threading import Barrier
from time import monotonic

import pytest

from agentlens.errors import StoreError
from agentlens.models.identity import VerdictIdentity
from agentlens.store import ClaimOutcome, Store, open_disposable_clone
from agentlens.store.connection import STORE_BUSY_TIMEOUT_MS, STORE_JOURNAL_MODE
from agentlens.store.rows import row_to_verdict_claim
from agentlens.store.schema import VERDICT_CLAIM_COLUMN_NAMES
from tests.factories import (
    build_fact_session,
    build_fact_verdict,
    build_session_facts,
    build_session_identity,
    build_verdict_claim,
    build_verdict_claim_identity,
    build_verdict_identity,
)
from tests.fakes import FakeClock

_NOW = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)


def test_store_persists_wal_journal_mode_after_opening(tmp_path: Path) -> None:
    db_path = tmp_path / "agentlens.db"

    with Store(db_path, clock=FakeClock(instant=_NOW)):
        pass

    with sqlite3.connect(db_path) as connection:
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()

    assert journal_mode is not None
    assert journal_mode[0] == STORE_JOURNAL_MODE.lower()


def test_wal_reader_returns_committed_data_while_a_writer_holds_the_store(tmp_path: Path) -> None:
    db_path = tmp_path / "agentlens.db"
    session = build_fact_session(identity=build_session_identity(session_id="session-wal-reader"))
    clock = FakeClock(instant=_NOW)
    with Store(db_path, clock=clock) as store:
        store.upsert_session(build_session_facts(session=session))

    writer = sqlite3.connect(db_path)
    writer.execute("BEGIN IMMEDIATE")
    writer.execute(
        "UPDATE fact_session SET agent_type = ? WHERE session_id = ?",
        ("changed-but-uncommitted", session.identity.session_id),
    )
    try:
        started = monotonic()
        with sqlite3.connect(db_path) as reader:
            reader.row_factory = sqlite3.Row
            row = reader.execute(
                "SELECT agent_type FROM fact_session WHERE session_id = ?",
                (session.identity.session_id,),
            ).fetchone()
        elapsed = monotonic() - started
    finally:
        writer.rollback()
        writer.close()

    assert row is not None
    assert row["agent_type"] == session.agent_type
    assert elapsed < 1.0


def test_second_writer_waits_for_the_configured_busy_timeout(tmp_path: Path) -> None:
    db_path = tmp_path / "agentlens.db"
    clock = FakeClock(instant=_NOW)
    with Store(db_path, clock=clock) as initial:
        initial.upsert_session(
            build_session_facts(
                session=build_fact_session(identity=build_session_identity(session_id="seed"))
            )
        )

    writer = sqlite3.connect(db_path)
    writer.execute("BEGIN IMMEDIATE")
    try:
        with Store(db_path, clock=clock) as contender:
            started = monotonic()
            with pytest.raises(StoreError):
                contender.upsert_session(
                    build_session_facts(
                        session=build_fact_session(
                            identity=build_session_identity(session_id="contender")
                        )
                    )
                )
            elapsed = monotonic() - started
    finally:
        writer.rollback()
        writer.close()

    timeout_s = STORE_BUSY_TIMEOUT_MS / 1_000
    assert elapsed >= timeout_s * 0.9
    assert elapsed < timeout_s + 2.0


def test_store_opens_a_database_created_without_wal(tmp_path: Path) -> None:
    db_path = tmp_path / "agentlens.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE fact_session (session_id TEXT PRIMARY KEY)")

    identity = build_verdict_claim_identity()
    with Store(db_path, clock=FakeClock(instant=_NOW)) as store:
        assert store.read_verdict_claim(identity) is None


def test_open_disposable_clone_reads_a_wal_source_store(tmp_path: Path) -> None:
    source_path = tmp_path / "agentlens.db"
    clock = FakeClock(instant=_NOW)
    source_session = build_session_facts(
        session=build_fact_session(identity=build_session_identity(session_id="source-session"))
    )

    with Store(source_path, clock=clock) as source:
        source.upsert_session(source_session)
        with open_disposable_clone(source_path, clock=clock) as clone:
            cloned = clone.read_session(source_session.session.identity.session_id)

    assert cloned == source_session


def test_acquire_verdict_claim_records_an_unclaimed_identity_without_a_verdict(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "agentlens.db"
    clock = FakeClock(instant=_NOW)
    claim = build_verdict_claim(expires_at=_NOW + timedelta(seconds=30))

    with Store(db_path, clock=clock) as store:
        outcome = store.acquire_verdict_claim(claim)
        stored_claim = store.read_verdict_claim(claim.identity)
        stored_verdict = store.read_verdict(
            build_verdict_identity(
                session_id=claim.identity.session_id,
                judge_input_hash=claim.identity.judge_input_hash,
                rubric_version=claim.identity.rubric_version,
                judge_model=claim.identity.requested_model,
            )
        )

    assert outcome is ClaimOutcome.ACQUIRED
    assert stored_claim == claim
    assert stored_verdict is None


def test_acquire_verdict_claim_allows_exactly_one_of_two_real_connection_racers(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "agentlens.db"
    identity = build_verdict_claim_identity()
    barrier = Barrier(2)
    first_claim = build_verdict_claim(
        identity=identity, owner="owner-one", expires_at=_NOW + timedelta(seconds=30)
    )
    second_claim = build_verdict_claim(
        identity=identity, owner="owner-two", expires_at=_NOW + timedelta(seconds=30)
    )

    with Store(db_path, clock=FakeClock(instant=_NOW)):
        pass

    def acquire(claim_owner: str) -> ClaimOutcome:
        claim = first_claim if claim_owner == first_claim.owner else second_claim
        with Store(db_path, clock=FakeClock(instant=_NOW)) as store:
            barrier.wait()
            return store.acquire_verdict_claim(claim)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(acquire, first_claim.owner)
        second = executor.submit(acquire, second_claim.owner)
        outcomes = (first.result(), second.result())

    with Store(db_path, clock=FakeClock(instant=_NOW)) as store:
        stored = store.read_verdict_claim(identity)

    assert outcomes.count(ClaimOutcome.ACQUIRED) == 1
    assert outcomes.count(ClaimOutcome.HELD_ELSEWHERE) == 1
    assert stored is not None
    assert stored.owner in {first_claim.owner, second_claim.owner}


def test_expired_claim_is_acquirable_at_the_injected_clock_instant(tmp_path: Path) -> None:
    db_path = tmp_path / "agentlens.db"
    clock = FakeClock(instant=_NOW)
    identity = build_verdict_claim_identity()
    first = build_verdict_claim(
        identity=identity, owner="owner-one", expires_at=_NOW + timedelta(seconds=1)
    )
    replacement = build_verdict_claim(
        identity=identity, owner="owner-two", expires_at=_NOW + timedelta(seconds=31)
    )

    with Store(db_path, clock=clock) as store:
        assert store.acquire_verdict_claim(first) is ClaimOutcome.ACQUIRED
        clock.advance(timedelta(seconds=1))
        assert store.acquire_verdict_claim(replacement) is ClaimOutcome.ACQUIRED
        assert store.read_verdict_claim(identity) == replacement


def test_same_owner_reentry_refreshes_a_live_verdict_claim(tmp_path: Path) -> None:
    db_path = tmp_path / "agentlens.db"
    clock = FakeClock(instant=_NOW)
    identity = build_verdict_claim_identity()
    original = build_verdict_claim(
        identity=identity, owner="owner-one", expires_at=_NOW + timedelta(seconds=30)
    )
    refreshed = build_verdict_claim(
        identity=identity, owner="owner-one", expires_at=_NOW + timedelta(seconds=60)
    )

    with Store(db_path, clock=clock) as store:
        assert store.acquire_verdict_claim(original) is ClaimOutcome.ACQUIRED
        assert store.acquire_verdict_claim(refreshed) is ClaimOutcome.ACQUIRED
        assert store.read_verdict_claim(identity) == refreshed


def test_release_verdict_claim_makes_the_identity_immediately_available(tmp_path: Path) -> None:
    db_path = tmp_path / "agentlens.db"
    clock = FakeClock(instant=_NOW)
    identity = build_verdict_claim_identity()
    first = build_verdict_claim(
        identity=identity, owner="owner-one", expires_at=_NOW + timedelta(seconds=30)
    )
    successor = build_verdict_claim(
        identity=identity, owner="owner-two", expires_at=_NOW + timedelta(seconds=30)
    )

    with Store(db_path, clock=clock) as store:
        assert store.acquire_verdict_claim(first) is ClaimOutcome.ACQUIRED
        store.release_verdict_claim(identity)
        assert store.read_verdict_claim(identity) is None
        assert store.acquire_verdict_claim(successor) is ClaimOutcome.ACQUIRED


def test_alias_and_concrete_requests_acquire_distinct_claims_for_one_spawn(tmp_path: Path) -> None:
    db_path = tmp_path / "agentlens.db"
    clock = FakeClock(instant=_NOW)
    alias_identity = build_verdict_claim_identity(requested_model="sonnet")
    concrete_identity = build_verdict_claim_identity(requested_model="claude-sonnet-5")
    alias_claim = build_verdict_claim(
        identity=alias_identity,
        owner="alias-scorer",
        expires_at=_NOW + timedelta(seconds=30),
    )
    concrete_claim = build_verdict_claim(
        identity=concrete_identity,
        owner="concrete-scorer",
        expires_at=_NOW + timedelta(seconds=30),
    )

    with Store(db_path, clock=clock) as store:
        alias_outcome = store.acquire_verdict_claim(alias_claim)
        concrete_outcome = store.acquire_verdict_claim(concrete_claim)
        stored_alias_claim = store.read_verdict_claim(alias_identity)
        stored_concrete_claim = store.read_verdict_claim(concrete_identity)

    assert alias_identity != concrete_identity
    assert alias_outcome is ClaimOutcome.ACQUIRED
    assert concrete_outcome is ClaimOutcome.ACQUIRED
    assert stored_alias_claim == alias_claim
    assert stored_concrete_claim == concrete_claim


def _journal_mode(db_path: Path) -> str:
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute("PRAGMA journal_mode").fetchone()
    return str(row["journal_mode"]).upper()


def test_four_writers_creating_one_store_at_once_all_open_it(tmp_path: Path) -> None:
    """Simultaneous *first* opens of a store that does not exist yet must not raise.

    This is the window that actually broke. Until the file is in WAL it is in
    rollback mode, where the schema-creating writer blocks every other connection;
    and SQLite runs no busy handler for a journal-mode change, so the contended
    switch failed immediately rather than waiting out ``busy_timeout``.
    """
    writers = 6
    # The window is narrow, so it is sampled repeatedly against a store created
    # from scratch each time rather than trusted to appear in a single attempt.
    for attempt in range(12):
        db_path = tmp_path / f"attempt-{attempt}" / "agentlens.db"
        start = Barrier(writers, timeout=30)

        def create_and_write(index: int, *, path: Path, gate: Barrier) -> str:
            gate.wait()
            with Store(path, clock=FakeClock(instant=_NOW)) as store:
                store.upsert_verdict(build_fact_verdict(session_id=f"session-{index}"))
            return "ok"

        with ThreadPoolExecutor(max_workers=writers) as executor:
            results = list(
                executor.map(partial(create_and_write, path=db_path, gate=start), range(writers))
            )

        assert results == ["ok"] * writers
        assert _journal_mode(db_path) == "WAL"
        with Store(db_path, clock=FakeClock(instant=_NOW)) as store:
            stored = [
                len(store.read_verdicts_for_session(f"session-{index}")) for index in range(writers)
            ]
        assert stored == [1] * writers


def test_repeated_concurrent_opens_of_an_existing_store_all_succeed(tmp_path: Path) -> None:
    """Scoring opens the store several times per spawn, so opens must stay cheap and safe."""
    db_path = tmp_path / "agentlens.db"

    def open_repeatedly(_: int) -> str:
        for _attempt in range(25):
            with Store(db_path, clock=FakeClock(instant=_NOW)) as store:
                store.read_verdicts_for_session("session-absent")
        return "ok"

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(open_repeatedly, range(4)))

    assert results == ["ok"] * 4
    assert _journal_mode(db_path) == "WAL"


def test_read_verdict_for_request_hits_a_concrete_model_and_misses_an_alias(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "agentlens.db"
    stored_verdict = build_fact_verdict(
        session_id="session-probe",
        judge_input_hash="e" * 64,
        rubric_version="v1",
        judge_model="claude-sonnet-5",
    )
    concrete_request = build_verdict_claim_identity(
        session_id=stored_verdict.session_id,
        judge_input_hash=stored_verdict.judge_input_hash,
        rubric_version=stored_verdict.rubric_version,
        requested_model="claude-sonnet-5",
    )
    alias_request = build_verdict_claim_identity(
        session_id=stored_verdict.session_id,
        judge_input_hash=stored_verdict.judge_input_hash,
        rubric_version=stored_verdict.rubric_version,
        requested_model="sonnet",
    )

    with Store(db_path, clock=FakeClock(instant=_NOW)) as store:
        store.upsert_verdict(stored_verdict)

        assert store.read_verdict_for_request(concrete_request) == stored_verdict
        assert store.read_verdict_for_request(alias_request) is None


def test_finalize_verdict_rolls_back_if_claim_release_aborts(tmp_path: Path) -> None:
    db_path = tmp_path / "agentlens.db"
    clock = FakeClock(instant=_NOW)
    claim = build_verdict_claim(
        identity=build_verdict_claim_identity(
            session_id="session-finalize",
            judge_input_hash="f" * 64,
            rubric_version="v1",
            requested_model="sonnet",
        ),
        expires_at=_NOW + timedelta(seconds=30),
    )
    verdict = build_fact_verdict(
        session_id=claim.identity.session_id,
        judge_input_hash=claim.identity.judge_input_hash,
        rubric_version=claim.identity.rubric_version,
        judge_model="claude-sonnet-5",
    )

    with Store(db_path, clock=clock) as store:
        assert store.acquire_verdict_claim(claim) is ClaimOutcome.ACQUIRED
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TRIGGER abort_claim_release
            BEFORE DELETE ON verdict_claim
            BEGIN
                SELECT RAISE(ABORT, 'claim release rejected');
            END
            """
        )

    with pytest.raises(StoreError), Store(db_path, clock=clock) as store:
        store.finalize_verdict(verdict, claim_identity=claim.identity)

    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        claim_row = connection.execute(
            """
            SELECT owner
            FROM verdict_claim
            WHERE session_id = ?
              AND judge_input_hash = ?
              AND rubric_version = ?
              AND requested_model = ?
            """,
            (
                claim.identity.session_id,
                claim.identity.judge_input_hash,
                claim.identity.rubric_version,
                claim.identity.requested_model,
            ),
        ).fetchone()
        verdict_count = connection.execute(
            """
            SELECT COUNT(*) AS n_verdicts
            FROM fact_verdict
            WHERE session_id = ?
              AND judge_input_hash = ?
              AND rubric_version = ?
              AND judge_model = ?
            """,
            (
                verdict.session_id,
                verdict.judge_input_hash,
                verdict.rubric_version,
                verdict.judge_model,
            ),
        ).fetchone()

    assert claim_row is not None
    assert claim_row["owner"] == claim.owner
    assert verdict_count is not None
    assert verdict_count["n_verdicts"] == 0


def test_claims_for_different_identities_of_one_spawn_do_not_block_each_other(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "agentlens.db"
    clock = FakeClock(instant=_NOW)
    first_identity = build_verdict_claim_identity(rubric_version="v1")
    second_identity = build_verdict_claim_identity(rubric_version="v2")
    first = build_verdict_claim(
        identity=first_identity, owner="owner-one", expires_at=_NOW + timedelta(seconds=30)
    )
    second = build_verdict_claim(
        identity=second_identity, owner="owner-two", expires_at=_NOW + timedelta(seconds=30)
    )

    with Store(db_path, clock=clock) as store:
        assert store.acquire_verdict_claim(first) is ClaimOutcome.ACQUIRED
        assert store.acquire_verdict_claim(second) is ClaimOutcome.ACQUIRED
        assert store.read_verdict_claim(first_identity) == first
        assert store.read_verdict_claim(second_identity) == second


def test_verdict_claim_table_is_added_to_a_store_that_predates_it(tmp_path: Path) -> None:
    db_path = tmp_path / "agentlens.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE fact_session (session_id TEXT PRIMARY KEY)")

    claim = build_verdict_claim(expires_at=_NOW + timedelta(seconds=30))
    with Store(db_path, clock=FakeClock(instant=_NOW)) as store:
        assert store.acquire_verdict_claim(claim) is ClaimOutcome.ACQUIRED

    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'verdict_claim'"
        ).fetchone()

    assert row is not None
    assert row["name"] == "verdict_claim"


def test_row_to_verdict_claim_reads_by_name_under_a_reordered_projection(tmp_path: Path) -> None:
    db_path = tmp_path / "agentlens.db"
    claim = build_verdict_claim(expires_at=_NOW + timedelta(seconds=30))
    with Store(db_path, clock=FakeClock(instant=_NOW)) as store:
        assert store.acquire_verdict_claim(claim) is ClaimOutcome.ACQUIRED

    reversed_column_list = ", ".join(reversed(VERDICT_CLAIM_COLUMN_NAMES))
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            f"SELECT {reversed_column_list} FROM verdict_claim "  # noqa: S608
            "WHERE session_id = ? AND judge_input_hash = ? AND rubric_version = ? "
            "AND requested_model = ?",
            (
                claim.identity.session_id,
                claim.identity.judge_input_hash,
                claim.identity.rubric_version,
                claim.identity.requested_model,
            ),
        ).fetchone()

    assert row is not None
    assert row_to_verdict_claim(row) == claim


def test_read_verdict_requires_an_exact_natural_key_and_returns_the_full_verdict(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "agentlens.db"
    expected = build_fact_verdict(
        session_id="session-match",
        judge_input_hash="a" * 64,
        rubric_version="v1",
        judge_model="claude-sonnet-5",
        judge_cost_usd=0.123,
        judge_input_tokens=321,
        judge_output_tokens=123,
        scored_at=datetime(2026, 8, 24, 12, 3, 4, 5, tzinfo=UTC),
    )
    identity = VerdictIdentity(
        session_id=expected.session_id,
        judge_input_hash=expected.judge_input_hash,
        rubric_version=expected.rubric_version,
        judge_model=expected.judge_model,
    )
    alternatives = (
        build_fact_verdict(
            session_id=expected.session_id,
            judge_input_hash="b" * 64,
            rubric_version=expected.rubric_version,
            judge_model=expected.judge_model,
        ),
        build_fact_verdict(
            session_id=expected.session_id,
            judge_input_hash=expected.judge_input_hash,
            rubric_version="v2",
            judge_model=expected.judge_model,
        ),
        build_fact_verdict(
            session_id=expected.session_id,
            judge_input_hash=expected.judge_input_hash,
            rubric_version=expected.rubric_version,
            judge_model="claude-opus-5",
        ),
    )

    with Store(db_path, clock=FakeClock(instant=_NOW)) as store:
        store.upsert_verdict(expected)
        for alternative in alternatives:
            store.upsert_verdict(alternative)
        stored = store.read_verdict(identity)
        misses = (
            store.read_verdict(
                build_verdict_identity(
                    session_id="other-session",
                    judge_input_hash=identity.judge_input_hash,
                    rubric_version=identity.rubric_version,
                    judge_model=identity.judge_model,
                )
            ),
            store.read_verdict(
                build_verdict_identity(
                    session_id=identity.session_id,
                    judge_input_hash="c" * 64,
                    rubric_version=identity.rubric_version,
                    judge_model=identity.judge_model,
                )
            ),
            store.read_verdict(
                build_verdict_identity(
                    session_id=identity.session_id,
                    judge_input_hash=identity.judge_input_hash,
                    rubric_version="v3",
                    judge_model=identity.judge_model,
                )
            ),
            store.read_verdict(
                build_verdict_identity(
                    session_id=identity.session_id,
                    judge_input_hash=identity.judge_input_hash,
                    rubric_version=identity.rubric_version,
                    judge_model="claude-haiku-5",
                )
            ),
        )

    assert stored == expected
    assert misses == (None, None, None, None)

    with Store(tmp_path / "empty.db", clock=FakeClock(instant=_NOW)) as empty_store:
        assert empty_store.read_verdict(identity) is None
