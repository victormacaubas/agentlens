"""The derivation fingerprint: covers every shaping input, not just the transcript."""

import json
import os
from pathlib import Path

import pytest

from agentlens.ingest.derivation import (
    derive_session_derivation,
    name_resolution_derivation_input,
    skill_inventory_derivation_input,
)
from agentlens.ingest.name_resolution import NameResolution
from agentlens.ingest.session import build_fact_session
from agentlens.ingest.skill_inventory import SkillInventoryEntry
from agentlens.ingest.transcript import parse_transcript
from agentlens.models.identity import NameSource
from tests.factories import (
    build_agent_tool_use_block,
    build_assistant_record,
    build_context_cache,
    build_main_session_path,
    build_session_identity,
    build_sidecar,
    build_source_revision,
    build_subagent_source_bundle,
    build_tool_invocation_pair,
    build_tool_use_block,
    build_transcript_path,
    build_transcript_text,
    build_user_record,
)

_FIVE_SECONDS_IN_NS = 5_000_000_000


def _write_transcript(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_transcript_text(build_tool_invocation_pair()))


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_derive_session_derivation_rejects_an_empty_input_list() -> None:
    with pytest.raises(ValueError, match="shaping input"):
        derive_session_derivation([])


def test_fingerprint_changes_when_the_sidecar_changes_but_the_transcript_does_not(
    tmp_path: Path,
) -> None:
    path = build_transcript_path(tmp_path)
    _write_transcript(path)
    sidecar_path = path.with_suffix(".meta.json")
    sidecar_path.write_text(json.dumps(build_sidecar(description="First description")))

    before = parse_transcript(
        build_subagent_source_bundle(transcript_path=path), context_cache=build_context_cache()
    )

    sidecar_path.write_text(json.dumps(build_sidecar(description="Second description")))
    after = parse_transcript(
        build_subagent_source_bundle(transcript_path=path), context_cache=build_context_cache()
    )

    assert before.session.revision.content_hash == after.session.revision.content_hash
    assert before.session.derivation_fingerprint != after.session.derivation_fingerprint


def test_repeated_parses_of_unchanged_inputs_produce_the_same_derivation(tmp_path: Path) -> None:
    path = build_transcript_path(tmp_path)
    _write_transcript(path)
    sidecar_path = path.with_suffix(".meta.json")
    sidecar_path.write_text(json.dumps(build_sidecar()))

    first = parse_transcript(
        build_subagent_source_bundle(transcript_path=path), context_cache=build_context_cache()
    )
    second = parse_transcript(
        build_subagent_source_bundle(transcript_path=path), context_cache=build_context_cache()
    )

    assert first.session.derivation_fingerprint == second.session.derivation_fingerprint
    assert first.session.derivation_observed_mtime_ns == second.session.derivation_observed_mtime_ns


def test_derivation_observed_mtime_ns_is_the_newer_of_the_transcript_and_sidecar_mtimes(
    tmp_path: Path,
) -> None:
    path = build_transcript_path(tmp_path)
    _write_transcript(path)
    sidecar_path = path.with_suffix(".meta.json")
    sidecar_path.write_text(json.dumps(build_sidecar()))

    transcript_mtime_ns = path.stat().st_mtime_ns
    older_sidecar_mtime_ns = transcript_mtime_ns - _FIVE_SECONDS_IN_NS
    os.utime(sidecar_path, ns=(older_sidecar_mtime_ns, older_sidecar_mtime_ns))

    facts = parse_transcript(
        build_subagent_source_bundle(transcript_path=path), context_cache=build_context_cache()
    )

    assert facts.session.derivation_observed_mtime_ns == transcript_mtime_ns


def test_derivation_fingerprint_is_unaffected_by_a_content_preserving_mtime_touch(
    tmp_path: Path,
) -> None:
    """Touching a file's mtime without changing its bytes must not look like a
    content change, or an unrelated filesystem touch would force every
    dependent row to be treated as new.
    """
    path = build_transcript_path(tmp_path)
    _write_transcript(path)

    before = parse_transcript(
        build_subagent_source_bundle(transcript_path=path), context_cache=build_context_cache()
    )

    touched_mtime_ns = path.stat().st_mtime_ns + _FIVE_SECONDS_IN_NS
    os.utime(path, ns=(touched_mtime_ns, touched_mtime_ns))
    after = parse_transcript(
        build_subagent_source_bundle(transcript_path=path), context_cache=build_context_cache()
    )

    assert before.session.derivation_fingerprint == after.session.derivation_fingerprint
    assert after.session.derivation_observed_mtime_ns == touched_mtime_ns


def test_skill_inventory_derivation_input_ignores_entries_outside_skill_names() -> None:
    relevant = SkillInventoryEntry(skill_name="tdd", revision=build_source_revision())
    irrelevant = SkillInventoryEntry(
        skill_name="graphify", revision=build_source_revision(content_hash="graphify-hash")
    )
    baseline = skill_inventory_derivation_input(
        (relevant, irrelevant), skill_names=frozenset({"tdd"})
    )

    edited_irrelevant = SkillInventoryEntry(
        skill_name="graphify", revision=build_source_revision(content_hash="graphify-hash-edited")
    )
    after_irrelevant_edit = skill_inventory_derivation_input(
        (relevant, edited_irrelevant), skill_names=frozenset({"tdd"})
    )

    edited_relevant = SkillInventoryEntry(
        skill_name="tdd", revision=build_source_revision(content_hash="tdd-hash-edited")
    )
    after_relevant_edit = skill_inventory_derivation_input(
        (edited_relevant, irrelevant), skill_names=frozenset({"tdd"})
    )

    assert baseline.fingerprint_value == after_irrelevant_edit.fingerprint_value
    assert baseline.fingerprint_value != after_relevant_edit.fingerprint_value


def test_skill_inventory_derivation_input_observed_mtime_ignores_entries_outside_skill_names() -> (
    None
):
    older = SkillInventoryEntry(skill_name="tdd", revision=build_source_revision(mtime_ns=100))
    newer_but_irrelevant = SkillInventoryEntry(
        skill_name="graphify", revision=build_source_revision(mtime_ns=200)
    )

    result = skill_inventory_derivation_input(
        (older, newer_but_irrelevant), skill_names=frozenset({"tdd"})
    )

    assert result.observed_mtime_ns == 100


def test_skill_inventory_derivation_input_falls_back_when_no_entry_matches_skill_names() -> None:
    unrelated = SkillInventoryEntry(skill_name="graphify", revision=build_source_revision())

    result = skill_inventory_derivation_input((unrelated,), skill_names=frozenset({"tdd"}))

    assert result.fingerprint_value == ("skill_inventory", [])
    assert result.observed_mtime_ns == 0


def _skill_fire_record(skill_name: str) -> dict[str, object]:
    return build_assistant_record(
        uuid="uuid-fire",
        message_id="msg-fire",
        content=[
            build_tool_use_block(
                tool_use_id="toolu-fire", name="Skill", input={"skill": skill_name}
            )
        ],
        stop_reason="tool_use",
    )


def _session_with_skill_inventory(skill_inventory: tuple[SkillInventoryEntry, ...]) -> str:
    session, _ = build_fact_session(
        identity=build_session_identity(),
        revision=build_source_revision(),
        agent_id="agent-fingerprint-scope",
        agent_definition=None,
        parent_session_id=None,
        records=[
            build_assistant_record(timestamp="2026-01-10T00:00:00.000Z"),
            _skill_fire_record("tdd"),
        ],
        tool_events=(),
        sidecar=None,
        name_resolution=NameResolution(agent_type="implementer", name_source=NameSource.META_JSON),
        attribution_agent_types=frozenset(),
        parent_evidence_revision=None,
        unreadable_line_count=0,
        skill_inventory=skill_inventory,
    )
    return session.derivation_fingerprint


def test_build_fact_session_fingerprint_ignores_a_skill_the_spawn_neither_declared_nor_fired() -> (
    None
):
    """A spawn's derivation identity depends only on the skills its own bridge
    names, not on the rest of the machine's installed skills: an unrelated
    skill being edited must not force a sound row to be treated as new.
    """
    unrelated = SkillInventoryEntry(
        skill_name="graphify", revision=build_source_revision(content_hash="graphify-hash")
    )
    edited_unrelated = SkillInventoryEntry(
        skill_name="graphify", revision=build_source_revision(content_hash="graphify-hash-edited")
    )

    baseline = _session_with_skill_inventory((unrelated,))
    after_unrelated_edit = _session_with_skill_inventory((edited_unrelated,))

    assert baseline == after_unrelated_edit


def test_build_fact_session_fingerprint_changes_when_a_fired_skill_is_edited() -> None:
    fired_skill = SkillInventoryEntry(
        skill_name="tdd", revision=build_source_revision(content_hash="tdd-hash")
    )
    edited_fired_skill = SkillInventoryEntry(
        skill_name="tdd", revision=build_source_revision(content_hash="tdd-hash-edited")
    )

    baseline = _session_with_skill_inventory((fired_skill,))
    after_fired_skill_edit = _session_with_skill_inventory((edited_fired_skill,))

    assert baseline != after_fired_skill_edit


def test_name_resolution_derivation_input_uses_a_fixed_marker_when_no_parent_was_read() -> None:
    result = name_resolution_derivation_input(
        NameResolution(agent_type="implementer", name_source=NameSource.META_JSON),
        attribution_agent_types=frozenset(),
        parent_revision=None,
    )

    assert result.observed_mtime_ns == 0


def test_name_resolution_derivation_input_contributes_the_parents_revision_when_read() -> None:
    result = name_resolution_derivation_input(
        NameResolution(agent_type="pathfinder", name_source=NameSource.PARENT_TASK),
        attribution_agent_types=frozenset(),
        parent_revision=build_source_revision(mtime_ns=555, content_hash="parent-hash"),
    )

    assert result.observed_mtime_ns == 555


def test_name_resolution_derivation_input_ignores_the_parents_mtime_in_the_fingerprint() -> None:
    """The pinned invariant applies to every shaping input, not only the transcript's own:
    a content-preserving touch on the parent transcript must not move the fingerprint.
    """
    resolution = NameResolution(agent_type="pathfinder", name_source=NameSource.PARENT_TASK)
    revision = build_source_revision(mtime_ns=100, content_hash="same-hash")
    touched_revision = build_source_revision(mtime_ns=999, content_hash="same-hash")

    before = name_resolution_derivation_input(
        resolution, attribution_agent_types=frozenset(), parent_revision=revision
    )
    after = name_resolution_derivation_input(
        resolution, attribution_agent_types=frozenset(), parent_revision=touched_revision
    )

    assert before.fingerprint_value == after.fingerprint_value
    assert after.observed_mtime_ns == 999


def test_name_resolution_derivation_input_changes_with_the_resolved_agent_type() -> None:
    baseline = name_resolution_derivation_input(
        NameResolution(agent_type="implementer", name_source=NameSource.META_JSON),
        attribution_agent_types=frozenset(),
        parent_revision=None,
    )
    changed = name_resolution_derivation_input(
        NameResolution(agent_type="pathfinder", name_source=NameSource.META_JSON),
        attribution_agent_types=frozenset(),
        parent_revision=None,
    )

    assert baseline.fingerprint_value != changed.fingerprint_value


def test_name_resolution_derivation_input_changes_with_distinct_attribution_values() -> None:
    resolution = NameResolution(agent_type="implementer", name_source=NameSource.META_JSON)

    baseline = name_resolution_derivation_input(
        resolution, attribution_agent_types=frozenset({"implementer"}), parent_revision=None
    )
    changed = name_resolution_derivation_input(
        resolution,
        attribution_agent_types=frozenset({"implementer", "pathfinder"}),
        parent_revision=None,
    )

    assert baseline.fingerprint_value != changed.fingerprint_value


def test_fingerprint_changes_when_the_consulted_parent_transcript_content_changes(
    tmp_path: Path,
) -> None:
    """A changed parent transcript refreshes the derivation fingerprint even though
    neither the subagent's own transcript nor its sidecar changed at all.
    """
    path = build_transcript_path(tmp_path)
    _write_transcript(path)
    sidecar_path = path.with_suffix(".meta.json")
    sidecar_path.write_text(json.dumps(build_sidecar(agent_type="", tool_use_id="toolu_spawn")))
    parent_path = build_main_session_path(tmp_path)
    parent_record = build_assistant_record(
        content=[build_agent_tool_use_block(tool_use_id="toolu_spawn", subagent_type="pathfinder")],
        stop_reason="tool_use",
    )
    _write(parent_path, build_transcript_text([parent_record]))

    before = parse_transcript(
        build_subagent_source_bundle(transcript_path=path), context_cache=build_context_cache()
    )

    extra_record = build_user_record(content=[])
    _write(parent_path, build_transcript_text([parent_record, extra_record]))
    after = parse_transcript(
        build_subagent_source_bundle(transcript_path=path), context_cache=build_context_cache()
    )

    assert before.session.agent_type == after.session.agent_type == "pathfinder"
    assert before.session.derivation_fingerprint != after.session.derivation_fingerprint


def test_fingerprint_is_unaffected_by_a_content_preserving_touch_on_the_consulted_parent(
    tmp_path: Path,
) -> None:
    path = build_transcript_path(tmp_path)
    _write_transcript(path)
    sidecar_path = path.with_suffix(".meta.json")
    sidecar_path.write_text(json.dumps(build_sidecar(agent_type="", tool_use_id="toolu_spawn")))
    parent_path = build_main_session_path(tmp_path)
    parent_record = build_assistant_record(
        content=[build_agent_tool_use_block(tool_use_id="toolu_spawn", subagent_type="pathfinder")],
        stop_reason="tool_use",
    )
    _write(parent_path, build_transcript_text([parent_record]))

    before = parse_transcript(
        build_subagent_source_bundle(transcript_path=path), context_cache=build_context_cache()
    )

    touched_mtime_ns = parent_path.stat().st_mtime_ns + _FIVE_SECONDS_IN_NS
    os.utime(parent_path, ns=(touched_mtime_ns, touched_mtime_ns))
    after = parse_transcript(
        build_subagent_source_bundle(transcript_path=path), context_cache=build_context_cache()
    )

    assert before.session.derivation_fingerprint == after.session.derivation_fingerprint
