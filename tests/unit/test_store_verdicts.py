"""``fact_verdict``: grain, natural-key upsert behavior, and name-keyed reads."""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from agentlens.models.judging import RubricDimension
from agentlens.store import Store
from agentlens.store.rows import row_to_fact_verdict
from agentlens.store.schema import FACT_VERDICT_COLUMN_NAMES
from tests.factories import (
    build_dimension_score,
    build_fact_session,
    build_fact_verdict,
    build_session_facts,
    build_suggested_fix,
    build_verdict,
    build_verdict_provenance,
)
from tests.fakes import FakeClock

_CLOCK = FakeClock(instant=datetime(2026, 1, 1, tzinfo=UTC))


def _count_rows(path: Path, table: str) -> int:
    with sqlite3.connect(path) as connection:
        row = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()  # noqa: S608
        return int(row[0])


def test_a_fully_populated_verdict_round_trips_every_field_including_provenance_and_cost(
    tmp_path: Path,
) -> None:
    """Every ``FactVerdict`` field, including nested provenance, survives a store round trip."""
    verdict = build_verdict(
        overall_score=5,
        dimensions={
            RubricDimension.TASK_COMPLETION: build_dimension_score(
                score=5, evidence=("Completed every acceptance criterion.",)
            ),
            RubricDimension.HONESTY: build_dimension_score(
                score=4, evidence=("Disclosed a skipped edge case.",)
            ),
            RubricDimension.EFFICIENCY: build_dimension_score(
                score=3, evidence=("Retried a read five times before succeeding.",)
            ),
            RubricDimension.SCOPE_ADHERENCE: build_dimension_score(
                score=2, evidence=("Touched only the files the task named.",)
            ),
        },
        suggested_fixes=(
            build_suggested_fix(
                dimension=RubricDimension.EFFICIENCY,
                target="the retry loop in ingest/session.py",
                recommendation="Cap the retry count instead of retrying unconditionally.",
                rationale="The transcript shows five retries of the same read.",
            ),
        ),
        provenance=build_verdict_provenance(),
    )
    expected = build_fact_verdict(
        session_id="session-round-trip",
        judge_input_hash="b" * 64,
        rubric_version="v1",
        judge_model="claude-sonnet-5",
        verdict=verdict,
        judge_cost_usd=0.011002,
        judge_input_tokens=675,
        judge_output_tokens=52,
        scored_at=datetime(2026, 8, 24, 12, 30, 45, tzinfo=UTC),
    )

    db_path = tmp_path / "agentlens.db"
    with Store(db_path, clock=_CLOCK) as store:
        store.upsert_verdict(expected)
        stored = store.read_verdicts_for_session(expected.session_id)

    assert stored == (expected,)


def test_row_to_fact_verdict_reads_by_name_under_a_reordered_projection(tmp_path: Path) -> None:
    """A projection that reverses the declared column order must still round-trip.

    Positional reconstruction would silently shift every field into the wrong
    slot under this projection; reading by name must not.
    """
    db_path = tmp_path / "agentlens.db"
    expected = build_fact_verdict()
    with Store(db_path, clock=_CLOCK) as store:
        store.upsert_verdict(expected)

    reversed_column_list = ", ".join(reversed(FACT_VERDICT_COLUMN_NAMES))
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            f"SELECT {reversed_column_list} FROM fact_verdict "  # noqa: S608
            "WHERE session_id = ? AND judge_input_hash = ? AND rubric_version = ? "
            "AND judge_model = ?",
            (
                expected.session_id,
                expected.judge_input_hash,
                expected.rubric_version,
                expected.judge_model,
            ),
        ).fetchone()

    assert row_to_fact_verdict(row) == expected


def test_rescoring_the_same_identity_replaces_the_row_rather_than_duplicating(
    tmp_path: Path,
) -> None:
    """A second write under an unchanged natural key replaces the stored verdict."""
    db_path = tmp_path / "agentlens.db"
    original = build_fact_verdict(judge_cost_usd=0.01, verdict=build_verdict(overall_score=3))
    updated = build_fact_verdict(judge_cost_usd=0.02, verdict=build_verdict(overall_score=5))
    with Store(db_path, clock=_CLOCK) as store:
        store.upsert_verdict(original)
        store.upsert_verdict(updated)
        stored = store.read_verdicts_for_session(original.session_id)

    assert stored == (updated,)
    assert _count_rows(db_path, "fact_verdict") == 1


def test_the_same_spawn_scored_under_two_models_produces_two_rows_neither_replacing_the_other(
    tmp_path: Path,
) -> None:
    """Verdicts from different concrete models are not comparable, so both rows must survive."""
    db_path = tmp_path / "agentlens.db"
    scored_by_sonnet = build_fact_verdict(judge_model="claude-sonnet-5")
    scored_by_opus = build_fact_verdict(judge_model="claude-opus-5")
    with Store(db_path, clock=_CLOCK) as store:
        store.upsert_verdict(scored_by_sonnet)
        store.upsert_verdict(scored_by_opus)
        stored = store.read_verdicts_for_session(scored_by_sonnet.session_id)

    by_model = {fact_verdict.judge_model: fact_verdict for fact_verdict in stored}
    assert set(by_model) == {"claude-sonnet-5", "claude-opus-5"}
    assert by_model["claude-sonnet-5"] == scored_by_sonnet
    assert by_model["claude-opus-5"] == scored_by_opus


def test_a_rubric_version_change_produces_a_separate_row_while_the_earlier_row_remains(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "agentlens.db"
    scored_under_v1 = build_fact_verdict(rubric_version="v1")
    scored_under_v2 = build_fact_verdict(rubric_version="v2")
    with Store(db_path, clock=_CLOCK) as store:
        store.upsert_verdict(scored_under_v1)
        store.upsert_verdict(scored_under_v2)
        stored = store.read_verdicts_for_session(scored_under_v1.session_id)

    by_version = {fact_verdict.rubric_version: fact_verdict for fact_verdict in stored}
    assert set(by_version) == {"v1", "v2"}
    assert by_version["v1"] == scored_under_v1
    assert by_version["v2"] == scored_under_v2


def test_deterministic_tables_are_unchanged_by_a_verdict_write(tmp_path: Path) -> None:
    """Scoring a spawn must never mutate its ``fact_session`` or ``fact_tool_event`` rows."""
    db_path = tmp_path / "agentlens.db"
    session = build_fact_session()
    facts = build_session_facts(session=session)
    with Store(db_path, clock=_CLOCK) as store:
        store.upsert_session(facts)
        store.upsert_verdict(build_fact_verdict(session_id=session.identity.session_id))
        stored_session = store.read_session(session.identity.session_id)

    assert stored_session == facts


def test_fact_verdict_table_is_created_on_first_use_against_a_store_that_predates_it(
    tmp_path: Path,
) -> None:
    """A store file written before ``fact_verdict`` existed gains the table when reopened."""
    db_path = tmp_path / "agentlens.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE fact_session (session_id TEXT PRIMARY KEY)")
        connection.commit()

    with Store(db_path, clock=_CLOCK) as store:
        store.upsert_verdict(build_fact_verdict())

    with sqlite3.connect(db_path) as connection:
        table_names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    assert "fact_verdict" in table_names


def test_read_verdicts_for_session_returns_empty_tuple_for_a_session_with_none(
    tmp_path: Path,
) -> None:
    with Store(tmp_path / "agentlens.db", clock=_CLOCK) as store:
        assert store.read_verdicts_for_session("does-not-exist") == ()
