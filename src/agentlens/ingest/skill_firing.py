"""Isolating what counts as evidence that a skill fired.

Two transcript shapes are recognized, each pinned by a synthetic fixture: a
``Skill`` tool invocation naming a skill in its ``skill`` input field, and an
assistant record's own ``attributionSkill`` field. Both are observed on real
transcripts. Any other shape, including an ordinary ``SKILL.md`` file read,
contributes nothing: agents and users read a skill's documentation without
running its workflow, so a read alone would overcount fires.
"""

from collections.abc import Mapping, Sequence

from agentlens.ingest.records import JsonRecord, content_blocks
from agentlens.ingest.skill_inventory import normalize_skill_name

_ASSISTANT_RECORD_TYPE = "assistant"
_SKILL_TOOL_NAME = "Skill"
_SKILL_TOOL_INPUT_KEY = "skill"
_ATTRIBUTION_SKILL_RECORD_KEY = "attributionSkill"


def resolve_fired_skill_names(records: Sequence[JsonRecord]) -> frozenset[str]:
    """Return the normalized names of every skill the transcript proves fired."""
    fired: set[str] = set()
    for record in records:
        if record.get("type") != _ASSISTANT_RECORD_TYPE:
            continue
        fired.update(_skill_tool_names(record))
        attribution = _attribution_skill_name(record)
        if attribution is not None:
            fired.add(attribution)
    return frozenset(fired)


def _skill_tool_names(record: Mapping[str, object]) -> frozenset[str]:
    names: set[str] = set()
    for block in content_blocks(record.get("message")):
        if block.get("type") != "tool_use" or block.get("name") != _SKILL_TOOL_NAME:
            continue
        tool_input = block.get("input")
        if not isinstance(tool_input, Mapping):
            continue
        skill_name = tool_input.get(_SKILL_TOOL_INPUT_KEY)
        if isinstance(skill_name, str) and skill_name:
            names.add(normalize_skill_name(skill_name))
    return frozenset(names)


def _attribution_skill_name(record: Mapping[str, object]) -> str | None:
    value = record.get(_ATTRIBUTION_SKILL_RECORD_KEY)
    if isinstance(value, str) and value:
        return normalize_skill_name(value)
    return None
