"""The session-skill bridge: the union of declaration, availability, and firing.

Each state is resolved independently, so every combination the design calls
out — a declared skill that never fires, an available undeclared skill that
fires, and a fired skill whose availability the files cannot support — is
exercised on its own rather than assumed to follow from the others.
"""

from datetime import UTC, datetime, timedelta

from agentlens.ingest.name_resolution import NameResolution
from agentlens.ingest.session import build_fact_session
from agentlens.ingest.skill_bridge import derive_skill_signals
from agentlens.ingest.skill_inventory import SkillInventoryEntry
from agentlens.models.facts import FactSession
from agentlens.models.identity import NameSource
from agentlens.models.skill_signals import KnownState, SessionSkillSignal
from tests.factories import (
    build_agent_definition,
    build_agent_definition_config,
    build_assistant_record,
    build_session_identity,
    build_source_revision,
    build_tool_use_block,
)

_STARTED_AT = datetime(2026, 1, 10, tzinfo=UTC)
_SESSION_ID = "session-skill-bridge"


def _epoch_ns(moment: datetime) -> int:
    return int(moment.timestamp() * 1_000_000_000)


def _skill_fire_record(
    skill_name: str, *, uuid: str = "uuid-fire", message_id: str = "msg-fire"
) -> dict[str, object]:
    return build_assistant_record(
        uuid=uuid,
        message_id=message_id,
        content=[
            build_tool_use_block(
                tool_use_id=f"toolu-{uuid}", name="Skill", input={"skill": skill_name}
            )
        ],
        stop_reason="tool_use",
    )


def test_declared_skill_absent_from_inventory_with_no_fire() -> None:
    definition = build_agent_definition(config=build_agent_definition_config(skills=("tdd",)))

    signals = derive_skill_signals(
        session_id=_SESSION_ID,
        agent_definition=definition,
        skill_inventory=(),
        records=[],
        started_at=_STARTED_AT,
    )

    assert signals == (
        _signal("tdd", declared=KnownState.TRUE, available=KnownState.UNKNOWN, fired=False),
    )


def test_available_undeclared_skill_that_does_not_fire() -> None:
    revision = build_source_revision(mtime_ns=_epoch_ns(_STARTED_AT - timedelta(days=1)))

    signals = derive_skill_signals(
        session_id=_SESSION_ID,
        agent_definition=None,
        skill_inventory=(SkillInventoryEntry(skill_name="tdd", revision=revision),),
        records=[],
        started_at=_STARTED_AT,
    )

    assert signals == (
        _signal("tdd", declared=KnownState.UNKNOWN, available=KnownState.TRUE, fired=False),
    )


def test_fired_skill_with_no_declaration_or_inventory_entry() -> None:
    signals = derive_skill_signals(
        session_id=_SESSION_ID,
        agent_definition=None,
        skill_inventory=(),
        records=[_skill_fire_record("tdd")],
        started_at=_STARTED_AT,
    )

    assert signals == (
        _signal("tdd", declared=KnownState.UNKNOWN, available=KnownState.UNKNOWN, fired=True),
    )


def test_skill_installed_after_the_spawn_is_unknown_not_false() -> None:
    revision = build_source_revision(mtime_ns=_epoch_ns(_STARTED_AT + timedelta(days=1)))

    signals = derive_skill_signals(
        session_id=_SESSION_ID,
        agent_definition=None,
        skill_inventory=(SkillInventoryEntry(skill_name="tdd", revision=revision),),
        records=[],
        started_at=_STARTED_AT,
    )

    assert signals[0].available == KnownState.UNKNOWN


def test_fired_skill_with_unprovable_availability_is_not_backfilled_to_true() -> None:
    """A fire never upgrades ``available``: the three states stay independent
    even when transcript evidence and the files disagree about what they can
    prove.
    """
    too_new_revision = build_source_revision(mtime_ns=_epoch_ns(_STARTED_AT + timedelta(days=1)))

    signals = derive_skill_signals(
        session_id=_SESSION_ID,
        agent_definition=None,
        skill_inventory=(SkillInventoryEntry(skill_name="tdd", revision=too_new_revision),),
        records=[_skill_fire_record("tdd")],
        started_at=_STARTED_AT,
    )

    assert signals == (
        _signal("tdd", declared=KnownState.UNKNOWN, available=KnownState.UNKNOWN, fired=True),
    )


def test_available_undeclared_skill_that_fires() -> None:
    definition = build_agent_definition(config=build_agent_definition_config(skills=()))
    revision = build_source_revision(mtime_ns=_epoch_ns(_STARTED_AT - timedelta(days=1)))

    signals = derive_skill_signals(
        session_id=_SESSION_ID,
        agent_definition=definition,
        skill_inventory=(SkillInventoryEntry(skill_name="tdd", revision=revision),),
        records=[_skill_fire_record("tdd")],
        started_at=_STARTED_AT,
    )

    assert signals == (
        _signal("tdd", declared=KnownState.FALSE, available=KnownState.TRUE, fired=True),
    )


def test_repeated_firing_of_the_same_skill_yields_one_row() -> None:
    records = [
        _skill_fire_record("tdd", uuid="uuid-fire-1", message_id="msg-fire-1"),
        _skill_fire_record("tdd", uuid="uuid-fire-2", message_id="msg-fire-2"),
    ]

    signals = derive_skill_signals(
        session_id=_SESSION_ID,
        agent_definition=None,
        skill_inventory=(),
        records=records,
        started_at=_STARTED_AT,
    )

    assert len(signals) == 1
    assert signals[0].fired is True


def test_signals_are_ordered_by_skill_name() -> None:
    definition = build_agent_definition(
        config=build_agent_definition_config(skills=("zeta", "alpha"))
    )

    signals = derive_skill_signals(
        session_id=_SESSION_ID,
        agent_definition=definition,
        skill_inventory=(),
        records=[],
        started_at=_STARTED_AT,
    )

    assert [signal.skill_name for signal in signals] == ["alpha", "zeta"]


def _signal(
    skill_name: str, *, declared: KnownState, available: KnownState, fired: bool
) -> SessionSkillSignal:
    return SessionSkillSignal(
        session_id=_SESSION_ID,
        skill_name=skill_name,
        declared=declared,
        available=available,
        fired=fired,
    )


def _build_fact_session_with_skills(
    *, records: list[dict[str, object]], skill_inventory: tuple[SkillInventoryEntry, ...] = ()
) -> tuple[FactSession, tuple[SessionSkillSignal, ...]]:
    return build_fact_session(
        identity=build_session_identity(),
        revision=build_source_revision(),
        agent_id="agent-skill-bridge",
        agent_definition=None,
        parent_session_id=None,
        records=records,
        tool_events=(),
        sidecar=None,
        name_resolution=NameResolution(agent_type="implementer", name_source=NameSource.META_JSON),
        unreadable_line_count=0,
        skill_inventory=skill_inventory,
    )


def test_build_fact_session_counts_distinct_fired_skills() -> None:
    records = [
        build_assistant_record(timestamp="2026-01-10T00:00:00.000Z"),
        _skill_fire_record("tdd", uuid="uuid-fire-1", message_id="msg-fire-1"),
        _skill_fire_record("tdd", uuid="uuid-fire-2", message_id="msg-fire-2"),
        _skill_fire_record("orchestrate", uuid="uuid-fire-3", message_id="msg-fire-3"),
    ]

    session, skill_signals = _build_fact_session_with_skills(records=records)

    assert session.n_skills_fired == 2
    assert {signal.skill_name for signal in skill_signals if signal.fired} == {"tdd", "orchestrate"}


def test_build_fact_session_derivation_fingerprint_changes_with_the_skill_inventory() -> None:
    records = [build_assistant_record(timestamp="2026-01-10T00:00:00.000Z")]
    without_inventory, _ = _build_fact_session_with_skills(records=records, skill_inventory=())
    with_inventory, _ = _build_fact_session_with_skills(
        records=records,
        skill_inventory=(SkillInventoryEntry(skill_name="tdd", revision=build_source_revision()),),
    )

    assert without_inventory.derivation_fingerprint != with_inventory.derivation_fingerprint
