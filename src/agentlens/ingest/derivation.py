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

from agentlens.ingest.sidecar import Sidecar
from agentlens.ingest.skill_inventory import SkillInventoryEntry
from agentlens.models.agent_definitions import AgentDefinition
from agentlens.models.identity import SourceRevision
from agentlens.utils.hashing import canonical_json_fingerprint

_NO_BOUND_DEFINITION_MTIME_NS = 0
_NO_SKILL_INVENTORY_MTIME_NS = 0


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


def skill_inventory_derivation_input(entries: Sequence[SkillInventoryEntry]) -> DerivationInput:
    """Return the discovered skill inventory as a derivation input.

    Every entry can affect the bridge for any name it happens to share, so
    the whole scanned inventory contributes to the fingerprint, not only the
    names a spawn declares or fires.
    """
    ordered = sorted(
        (entry.skill_name, entry.revision.size, entry.revision.content_hash) for entry in entries
    )
    observed_mtime_ns = (
        max(entry.revision.mtime_ns for entry in entries)
        if entries
        else _NO_SKILL_INVENTORY_MTIME_NS
    )
    return DerivationInput(
        fingerprint_value=("skill_inventory", ordered), observed_mtime_ns=observed_mtime_ns
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
