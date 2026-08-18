"""The JSON report document: versioned, unscored, one row per qualified spawn."""

from datetime import UTC, datetime

from agentlens.render.document import build_session_document
from tests.factories import build_fact_session, build_session_facts
from tests.fakes import FakeClock

FORBIDDEN_KEYS = {"score", "verdict", "fix"}


def _assert_no_scoring_keys(value: object) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            assert key not in FORBIDDEN_KEYS, f"forbidden key {key!r} found in document"
            _assert_no_scoring_keys(nested)
    elif isinstance(value, list):
        for item in value:
            _assert_no_scoring_keys(item)


def test_document_carries_schema_version_and_unscored_marker() -> None:
    facts = build_session_facts()
    clock = FakeClock(instant=datetime(2026, 1, 1, tzinfo=UTC))

    document = build_session_document(facts, clock=clock)

    assert document["schema_version"] == 1
    assert document["scoring_status"] == "unscored"


def test_document_has_one_row_for_the_qualified_spawn() -> None:
    facts = build_session_facts()
    clock = FakeClock(instant=datetime(2026, 1, 1, tzinfo=UTC))

    document = build_session_document(facts, clock=clock)

    spawns = document["spawns"]
    assert isinstance(spawns, list)
    assert len(spawns) == 1
    row = spawns[0]
    assert row["session_id"] == facts.session.identity.session_id
    assert row["source_project"] == facts.session.identity.source_project
    assert row["raw_session_id"] == facts.session.identity.raw_session_id


def test_document_generated_at_is_timezone_aware_utc_from_the_injected_clock() -> None:
    facts = build_session_facts()
    fixed = datetime(2026, 3, 4, 12, 30, tzinfo=UTC)
    clock = FakeClock(instant=fixed)

    document = build_session_document(facts, clock=clock)

    generated_at = datetime.fromisoformat(str(document["generated_at"]))
    assert generated_at == fixed
    assert generated_at.tzinfo is not None
    assert generated_at.utcoffset() == fixed.utcoffset()


def test_ingested_but_unscored_spawn_row_carries_its_deterministic_fields() -> None:
    session = build_fact_session(
        n_turns=3,
        n_invocations=5,
        n_reads=2,
        n_edits=1,
        n_writes=1,
        n_bash=1,
        n_distinct_files=2,
        n_errors=1,
        duration_ms=4200,
        input_tokens=100,
        output_tokens=50,
        cache_read_tokens=30,
        cache_creation_tokens=10,
        unreadable_line_count=2,
    )
    facts = build_session_facts(session=session)
    clock = FakeClock(instant=datetime(2026, 1, 1, tzinfo=UTC))

    document = build_session_document(facts, clock=clock)

    row = document["spawns"][0]  # type: ignore[index]
    assert row["n_turns"] == 3
    assert row["n_invocations"] == 5
    assert row["n_errors"] == 1
    assert row["duration_ms"] == 4200
    assert row["cache_read_tokens"] == 30
    assert row["unreadable_line_count"] == 2


def test_no_score_verdict_or_fix_key_appears_anywhere_in_the_document() -> None:
    facts = build_session_facts()
    clock = FakeClock(instant=datetime(2026, 1, 1, tzinfo=UTC))

    document = build_session_document(facts, clock=clock)

    _assert_no_scoring_keys(document)
