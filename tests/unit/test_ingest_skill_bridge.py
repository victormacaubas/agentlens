"""The session-skill bridge: declared skills union fired skills, availability resolved per row.

A name earns a row by being declared by the spawn's bound agent definition or
by being proven to have fired in the transcript. Availability is resolved
independently on every row that earns membership by either path, so a fire
never backfills an availability claim the files cannot support, and a
declared skill's proven availability never depends on whether it also fired.
"""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import pytest

from agentlens.ingest.name_resolution import NameResolution
from agentlens.ingest.session import build_fact_session
from agentlens.ingest.skill_bridge import derive_skill_signals
from agentlens.ingest.skill_inventory import SkillInventoryEntry
from agentlens.models.agent_definitions import AgentDefinition
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


def _old_revision() -> SkillInventoryEntry:
    return SkillInventoryEntry(
        skill_name="tdd",
        revision=build_source_revision(mtime_ns=_epoch_ns(_STARTED_AT - timedelta(days=1))),
    )


def _new_revision() -> SkillInventoryEntry:
    return SkillInventoryEntry(
        skill_name="tdd",
        revision=build_source_revision(mtime_ns=_epoch_ns(_STARTED_AT + timedelta(days=1))),
    )


@pytest.mark.parametrize(
    ("agent_definition", "skill_inventory", "records", "expected"),
    [
        pytest.param(
            build_agent_definition(config=build_agent_definition_config(skills=("tdd",))),
            (),
            (),
            _signal("tdd", declared=KnownState.TRUE, available=KnownState.UNKNOWN, fired=False),
            id="declared_with_unproven_availability",
        ),
        pytest.param(
            build_agent_definition(config=build_agent_definition_config(skills=("tdd",))),
            (_old_revision(),),
            (),
            _signal("tdd", declared=KnownState.TRUE, available=KnownState.TRUE, fired=False),
            id="declared_and_available",
        ),
        pytest.param(
            None,
            (),
            (_skill_fire_record("tdd"),),
            _signal("tdd", declared=KnownState.UNKNOWN, available=KnownState.UNKNOWN, fired=True),
            id="fired_only_unbound_definition_is_unknown",
        ),
        pytest.param(
            build_agent_definition(config=build_agent_definition_config(skills=())),
            (_old_revision(),),
            (_skill_fire_record("tdd"),),
            _signal("tdd", declared=KnownState.FALSE, available=KnownState.TRUE, fired=True),
            id="fired_and_available_but_undeclared_by_a_bound_definition",
        ),
        pytest.param(
            None,
            (_new_revision(),),
            (_skill_fire_record("tdd"),),
            _signal("tdd", declared=KnownState.UNKNOWN, available=KnownState.UNKNOWN, fired=True),
            id="fire_does_not_backfill_unprovable_availability",
        ),
    ],
)
def test_derive_skill_signals_resolves_each_state_independently(
    agent_definition: AgentDefinition | None,
    skill_inventory: Sequence[SkillInventoryEntry],
    records: Sequence[dict[str, object]],
    expected: SessionSkillSignal,
) -> None:
    signals = derive_skill_signals(
        session_id=_SESSION_ID,
        agent_definition=agent_definition,
        skill_inventory=skill_inventory,
        records=list(records),
        started_at=_STARTED_AT,
    )

    assert signals == (expected,)


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


def test_installed_skill_that_is_neither_declared_nor_fired_produces_no_row() -> None:
    definition = build_agent_definition(config=build_agent_definition_config(skills=("tdd",)))
    other_entry = SkillInventoryEntry(skill_name="graphify", revision=_old_revision().revision)

    signals = derive_skill_signals(
        session_id=_SESSION_ID,
        agent_definition=definition,
        skill_inventory=(_old_revision(), other_entry),
        records=[],
        started_at=_STARTED_AT,
    )

    assert [signal.skill_name for signal in signals] == ["tdd"]


def test_reingest_with_changed_firing_evidence_adds_a_bridge_row() -> None:
    without_fire = derive_skill_signals(
        session_id=_SESSION_ID,
        agent_definition=None,
        skill_inventory=(),
        records=[],
        started_at=_STARTED_AT,
    )
    with_fire = derive_skill_signals(
        session_id=_SESSION_ID,
        agent_definition=None,
        skill_inventory=(),
        records=[_skill_fire_record("tdd")],
        started_at=_STARTED_AT,
    )

    assert without_fire == ()
    assert with_fire == (
        _signal("tdd", declared=KnownState.UNKNOWN, available=KnownState.UNKNOWN, fired=True),
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
        attribution_agent_types=frozenset(),
        parent_evidence_revision=None,
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
