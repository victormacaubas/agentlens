"""Firing evidence: a ``Skill`` tool invocation, or an ``attributionSkill`` marker.

Each supported marker shape is pinned by its own fixture. An ordinary
``SKILL.md`` read and an unrecognized marker shape both leave the set of
fired skills empty, rather than guessing.
"""

from agentlens.ingest.skill_firing import resolve_fired_skill_names
from tests.factories import build_assistant_record, build_tool_use_block

_TOOL_USE_ID = "toolu_skill_1"


def test_a_skill_tool_invocation_names_a_fired_skill() -> None:
    record = build_assistant_record(
        content=[
            build_tool_use_block(
                tool_use_id=_TOOL_USE_ID,
                name="Skill",
                input={"skill": "python-engineering-standards"},
            )
        ],
        stop_reason="tool_use",
    )

    assert resolve_fired_skill_names([record]) == frozenset({"python-engineering-standards"})


def test_a_skill_tool_invocation_with_a_namespaced_name_is_normalized() -> None:
    record = build_assistant_record(
        content=[
            build_tool_use_block(
                tool_use_id=_TOOL_USE_ID, name="Skill", input={"skill": "opsx:propose"}
            )
        ],
        stop_reason="tool_use",
    )

    assert resolve_fired_skill_names([record]) == frozenset({"propose"})


def test_an_attribution_skill_marker_names_a_fired_skill() -> None:
    record = build_assistant_record(attribution_skill="skill-creator")

    assert resolve_fired_skill_names([record]) == frozenset({"skill-creator"})


def test_a_namespaced_attribution_skill_marker_is_normalized() -> None:
    record = build_assistant_record(attribution_skill="craft:python-engineering-standards")

    assert resolve_fired_skill_names([record]) == frozenset({"python-engineering-standards"})


def test_the_same_skill_firing_twice_counts_as_one_name() -> None:
    first = build_assistant_record(
        uuid="uuid-fire-1",
        message_id="msg-fire-1",
        content=[build_tool_use_block(tool_use_id="toolu_1", name="Skill", input={"skill": "tdd"})],
        stop_reason="tool_use",
    )
    second = build_assistant_record(
        uuid="uuid-fire-2",
        message_id="msg-fire-2",
        content=[build_tool_use_block(tool_use_id="toolu_2", name="Skill", input={"skill": "tdd"})],
        stop_reason="tool_use",
    )

    assert resolve_fired_skill_names([first, second]) == frozenset({"tdd"})


def test_a_skill_md_read_alone_is_not_firing_evidence() -> None:
    record = build_assistant_record(
        content=[
            build_tool_use_block(
                tool_use_id=_TOOL_USE_ID, name="Read", input={"file_path": "/skills/tdd/SKILL.md"}
            )
        ],
        stop_reason="tool_use",
    )

    assert resolve_fired_skill_names([record]) == frozenset()


def test_an_unrecognized_marker_shape_contributes_no_fired_skill() -> None:
    record = build_assistant_record()
    record["skillInvoked"] = "tdd"

    assert resolve_fired_skill_names([record]) == frozenset()


def test_empty_records_yield_no_fired_skills() -> None:
    assert resolve_fired_skill_names([]) == frozenset()
