"""Guards name-keyed store row writes and reads against positional drift.

A declared column with no write extractor must fail during the full-value
round trip rather than silently shifting values. Reordered projections prove
reads use names.
"""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from agentlens.store import Store
from agentlens.store.rows import row_to_fact_session, row_to_fact_tool_event
from agentlens.store.schema import FACT_SESSION_COLUMN_NAMES, FACT_TOOL_EVENT_COLUMN_NAMES
from tests.factories import (
    build_fact_session,
    build_fact_tool_event,
    build_session_facts,
    build_session_identity,
    build_source_revision,
)


def test_row_to_fact_session_reads_by_name_under_a_reordered_projection(
    tmp_path: Path,
) -> None:
    """A projection that reverses the declared column order must still round-trip.

    Positional reconstruction would silently shift every field into the wrong
    slot under this projection; reading by name must not.
    """
    db_path = tmp_path / "agentlens.db"
    expected = build_fact_session()
    with Store(db_path) as store:
        store.upsert_session(build_session_facts(session=expected))

    reversed_column_list = ", ".join(reversed(FACT_SESSION_COLUMN_NAMES))
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            f"SELECT {reversed_column_list} FROM fact_session "  # noqa: S608
            "WHERE session_id = ?",
            (expected.identity.session_id,),
        ).fetchone()

    assert row_to_fact_session(row) == expected


def test_row_to_fact_tool_event_reads_by_name_under_a_reordered_projection(
    tmp_path: Path,
) -> None:
    """A projection that reverses the declared column order must still round-trip.

    Positional reconstruction would silently shift every field into the wrong
    slot under this projection; reading by name must not.
    """
    db_path = tmp_path / "agentlens.db"
    expected = build_fact_tool_event()
    with Store(db_path) as store:
        store.upsert_session(build_session_facts(tool_events=(expected,)))

    reversed_column_list = ", ".join(reversed(FACT_TOOL_EVENT_COLUMN_NAMES))
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            f"SELECT {reversed_column_list} FROM fact_tool_event "  # noqa: S608
            "WHERE session_id = ? AND ordinal = ?",
            (expected.session_id, expected.ordinal),
        ).fetchone()

    assert row_to_fact_tool_event(row) == expected


def test_a_fully_populated_session_and_tool_event_round_trip_every_field(
    tmp_path: Path,
) -> None:
    """Every ``FactSession`` and ``FactToolEvent`` field survives a store round trip.

    Each field is set to a value distinct from every other field of the same
    type, so a writer or reader that swapped two same-typed columns would
    still fail this assertion even though a same-valued fixture would not.
    """
    identity = build_session_identity(
        session_id="session-round-trip",
        source_project="project-round-trip",
        raw_session_id="raw-round-trip",
    )
    revision = build_source_revision(
        mtime_ns=1_800_000_000_000_000_001,
        size=8_192,
        content_hash="content-hash-round-trip",
    )
    session = build_fact_session(
        identity=identity,
        revision=revision,
        agent_type="round-trip-agent",
        task_description="Round-trip every field",
        spawning_tool_use_id="toolu_round_trip",
        spawn_depth=3,
        n_turns=11,
        n_invocations=12,
        n_reads=13,
        n_edits=14,
        n_writes=15,
        n_bash=16,
        n_distinct_files=17,
        n_errors=18,
        n_denials=19,
        n_repeated_invocations=20,
        duration_ms=21_000,
        input_tokens=22_000,
        output_tokens=23_000,
        cache_read_tokens=24_000,
        cache_creation_tokens=25_000,
        unreadable_line_count=26,
    )
    tool_event = build_fact_tool_event(
        session_id=identity.session_id,
        ordinal=7,
        tool_name="Bash",
        input_fingerprint="input-fingerprint-round-trip",
        file_identity="file-identity-round-trip",
        timestamp=datetime(2026, 2, 3, 4, 5, 6, tzinfo=UTC),
        is_error=True,
        denial_kind="permission-rule",
        result_size=2_048,
    )
    facts = build_session_facts(session=session, tool_events=(tool_event,))

    db_path = tmp_path / "agentlens.db"
    with Store(db_path) as store:
        store.upsert_session(facts)
        stored = store.read_session(identity.session_id)

    assert stored == facts
