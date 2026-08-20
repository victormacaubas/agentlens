"""Building the derivation fingerprint and newest observed shaping-input time.

A session row is shaped by more than its transcript: a sidecar, a bound agent
definition, a discovered skill inventory, and in later changes a parent
name-resolution record, can each change the row without the transcript itself
changing. Every such input contributes one :class:`DerivationInput` to the
list this module hashes, so a later change adds an input rather than
redefining how hashing works.

This is deliberately kept separate from a transcript's own
:class:`~agentlens.models.identity.SourceRevision`, which continues to mean
only "the transcript file's content hash."
"""

from collections.abc import Sequence
from dataclasses import dataclass

from agentlens.ingest.name_resolution import NameResolution
from agentlens.ingest.sidecar import Sidecar
from agentlens.ingest.skill_inventory import SkillInventoryEntry
from agentlens.models.agent_definitions import AgentDefinition
from agentlens.models.identity import SourceRevision
from agentlens.utils.hashing import canonical_json_fingerprint

_NO_BOUND_DEFINITION_MTIME_NS = 0
_NO_SKILL_INVENTORY_MTIME_NS = 0
_NO_PARENT_EVIDENCE_MTIME_NS = 0
_NO_PARENT_EVIDENCE_MARKER = ("name_resolution_parent", None)


@dataclass(frozen=True, slots=True, kw_only=True)
class DerivationInput:
    """One file or fact that shaped a derived row.

    ``fingerprint_value`` must be JSON-serializable and carry every
    deterministic value that input contributed, not only its file revision, so
    two inputs that read the same bytes but parsed to different values are
    never mistaken for identical.
    """

    fingerprint_value: object
    observed_mtime_ns: int


def transcript_derivation_input(revision: SourceRevision) -> DerivationInput:
    """Return the transcript's own revision as a derivation input."""
    return DerivationInput(
        fingerprint_value=("transcript", revision.size, revision.content_hash),
        observed_mtime_ns=revision.mtime_ns,
    )


def sidecar_derivation_input(sidecar: Sidecar) -> DerivationInput:
    """Return the sidecar's revision and parsed fields as a derivation input."""
    return DerivationInput(
        fingerprint_value=(
            "sidecar",
            sidecar.revision.size,
            sidecar.revision.content_hash,
            sidecar.agent_type,
            sidecar.description,
            sidecar.tool_use_id,
            sidecar.spawn_depth,
            sidecar.parent_agent_id,
            sidecar.model,
        ),
        observed_mtime_ns=sidecar.revision.mtime_ns,
    )


def agent_definition_derivation_input(agent_definition: AgentDefinition | None) -> DerivationInput:
    """Return the spawn's bound agent definition, or its absence, as a derivation input.

    An unbound definition (``None``) still contributes a fixed fingerprint
    value distinct from any real identity, so a spawn that later becomes
    bindable is detected as changed. It contributes no real observed time,
    since there is no sound file behind an unresolved binding.
    """
    if agent_definition is None:
        return DerivationInput(
            fingerprint_value=("agent_definition", None),
            observed_mtime_ns=_NO_BOUND_DEFINITION_MTIME_NS,
        )
    return DerivationInput(
        fingerprint_value=("agent_definition", agent_definition.agent_definition_id),
        observed_mtime_ns=agent_definition.revision.mtime_ns,
    )


def skill_inventory_derivation_input(
    entries: Sequence[SkillInventoryEntry], *, skill_names: frozenset[str]
) -> DerivationInput:
    """Return the discovered skill inventory as a derivation input, scoped to ``skill_names``.

    Only entries whose ``skill_name`` is in ``skill_names`` contribute, so a
    spawn's derivation identity depends on the skills its own bridge rows
    name — declared or fired — and not on the rest of the machine's
    installed skills. Editing, adding, or removing an entry outside that set
    leaves the fingerprint unchanged.
    """
    filtered = [entry for entry in entries if entry.skill_name in skill_names]
    ordered = sorted(
        (entry.skill_name, entry.revision.size, entry.revision.content_hash) for entry in filtered
    )
    observed_mtime_ns = (
        max(entry.revision.mtime_ns for entry in filtered)
        if filtered
        else _NO_SKILL_INVENTORY_MTIME_NS
    )
    return DerivationInput(
        fingerprint_value=("skill_inventory", ordered), observed_mtime_ns=observed_mtime_ns
    )


def name_resolution_derivation_input(
    name_resolution: NameResolution,
    *,
    attribution_agent_types: frozenset[str],
    parent_revision: SourceRevision | None,
) -> DerivationInput:
    """Return the resolved name and its evidence as a derivation input.

    ``parent_revision`` is the parent transcript's own revision, present only
    when the ordered name-resolution chain actually opened it. When no parent
    was read, a fixed marker distinct from any real revision is contributed
    instead — the same technique :func:`agent_definition_derivation_input`
    uses for an unbound definition — so a spawn that later starts (or stops)
    consulting a parent is detected as changed even though nothing about the
    spawn's own transcript or sidecar did.
    """
    if parent_revision is None:
        parent_component: tuple[object, ...] = _NO_PARENT_EVIDENCE_MARKER
        observed_mtime_ns = _NO_PARENT_EVIDENCE_MTIME_NS
    else:
        parent_component = (
            "name_resolution_parent",
            parent_revision.size,
            parent_revision.content_hash,
        )
        observed_mtime_ns = parent_revision.mtime_ns
    return DerivationInput(
        fingerprint_value=(
            "name_resolution",
            name_resolution.agent_type,
            name_resolution.name_source.value,
            sorted(attribution_agent_types),
            parent_component,
        ),
        observed_mtime_ns=observed_mtime_ns,
    )


def derive_session_derivation(inputs: Sequence[DerivationInput]) -> tuple[str, int]:
    """Return the derivation fingerprint and newest observed mtime across ``inputs``.

    Raises:
        ValueError: ``inputs`` is empty. A session always has at least its
            transcript as a shaping input, so an empty list is a caller bug.
    """
    if not inputs:
        raise ValueError("derivation requires at least one shaping input")
    fingerprint = canonical_json_fingerprint([item.fingerprint_value for item in inputs])
    observed_mtime_ns = max(item.observed_mtime_ns for item in inputs)
    return fingerprint, observed_mtime_ns
