"""Firing evidence: a ``Skill`` tool invocation, or an ``attributionSkill`` marker.

Covers each supported marker shape, namespaced names normalizing to their bare
form, and a repeated fire counting once. An ordinary ``SKILL.md`` read, an
unrecognized marker shape, and no records at all each leave the set of fired
skills empty rather than guessing.
"""

from collections.abc import Sequence

import pytest

from agentlens.ingest.skill_firing import resolve_fired_skill_names
from tests.factories import build_assistant_record, build_tool_use_block


def _skill_invocation(
    skill: str, *, uuid: str = "uuid-fire-1", message_id: str = "msg-fire-1"
) -> dict[str, object]:
    """An assistant turn that invokes the ``Skill`` tool on ``skill``."""
    return build_assistant_record(
        uuid=uuid,
        message_id=message_id,
        content=[
            build_tool_use_block(tool_use_id=f"toolu-{uuid}", name="Skill", input={"skill": skill})
        ],
        stop_reason="tool_use",
    )


def _tool_use(name: str, tool_input: dict[str, object]) -> dict[str, object]:
    """An assistant turn that invokes some tool other than ``Skill``."""
    return build_assistant_record(
        content=[build_tool_use_block(tool_use_id="toolu-other", name=name, input=tool_input)],
        stop_reason="tool_use",
    )


def _unrecognized_marker() -> dict[str, object]:
    """A record carrying a marker key the parser does not support."""
    record = build_assistant_record()
    record["skillInvoked"] = "tdd"
    return record


@pytest.mark.parametrize(
    ("records", "expected"),
    [
        ([_skill_invocation("python-engineering-standards")], {"python-engineering-standards"}),
        ([_skill_invocation("opsx:propose")], {"propose"}),
        ([build_assistant_record(attribution_skill="skill-creator")], {"skill-creator"}),
        (
            [build_assistant_record(attribution_skill="craft:python-engineering-standards")],
            {"python-engineering-standards"},
        ),
        ([_tool_use("Read", {"file_path": "/skills/tdd/SKILL.md"})], set()),
        ([_unrecognized_marker()], set()),
        ([], set()),
    ],
    ids=[
        "skill_tool_invocation",
        "namespaced_skill_tool_invocation",
        "attribution_skill_marker",
        "namespaced_attribution_skill_marker",
        "skill_md_read_alone_is_not_firing",
        "unrecognized_marker_shape",
        "no_records",
    ],
)
def test_firing_evidence_resolves_to_the_expected_skill_names(
    records: Sequence[dict[str, object]], expected: set[str]
) -> None:
    assert resolve_fired_skill_names(records) == frozenset(expected)


def test_the_same_skill_firing_twice_counts_as_one_name() -> None:
    """``n_skills_fired`` is a distinct count, so a repeat fire must not inflate it."""
    first = _skill_invocation("tdd", uuid="uuid-fire-1", message_id="msg-fire-1")
    second = _skill_invocation("tdd", uuid="uuid-fire-2", message_id="msg-fire-2")

    assert resolve_fired_skill_names([first, second]) == frozenset({"tdd"})
