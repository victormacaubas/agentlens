"""Discovering which skills are provably available to a spawn.

Skills live under three location kinds: a user's own skill directory, a
project's skill directory, and Claude's plugin cache. The first two always
hold a skill one level down (``<root>/<skill>/SKILL.md``); the plugin cache
nests that same leaf directory at one of three different depths, so a caller
scanning it walks the whole subtree instead of assuming a fixed depth.

Skill directories are the same shape agent definitions are on this machine:
plain directories sitting alongside directory symlinks with relative targets.
Each ``SKILL.md`` is read with the same stat-read-stat soundness rule
:mod:`agentlens.ingest.agent_definitions` uses, so a symlinked skill's
revision reflects its target's content, and a file that changes mid-read is
detected rather than trusted.
"""

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from agentlens.errors import MalformedSourceError, SourceChangedError
from agentlens.ingest.frontmatter import parse_frontmatter, scalar_field
from agentlens.models.identity import SourceRevision
from agentlens.models.skill_signals import KnownState
from agentlens.utils.hashing import hash_text

_SCOPED_SKILL_GLOB = "*/SKILL.md"
_SKILL_MD_FILENAME = "SKILL.md"
_SKILL_NAME_NAMESPACE_SEPARATOR = ":"
_NANOSECONDS_PER_SECOND = 1_000_000_000


@dataclass(frozen=True, slots=True, kw_only=True)
class SkillInventoryEntry:
    """One ``SKILL.md`` found on disk, identified by its own ``name:`` frontmatter."""

    skill_name: str
    revision: SourceRevision


def normalize_skill_name(name: str) -> str:
    """Return a skill's bare identity, stripping any ``owner:`` namespace prefix.

    Declaration and firing evidence can carry a namespaced form
    (``craft:python-engineering-standards``); an installed skill's own
    directory name never does. Matching either side against an inventory
    entry requires the same bare form on both, so every caller that resolves
    a name against the inventory normalizes through this function first.
    """
    return name.rsplit(_SKILL_NAME_NAMESPACE_SEPARATOR, 1)[-1]


def discover_skill_inventory(
    directory: Path, *, recursive: bool
) -> tuple[SkillInventoryEntry, ...]:
    """Scan ``directory`` for ``SKILL.md`` files and return one entry per skill found.

    ``recursive`` selects the plugin-cache shape, whose skill leaf directory
    sits at one of three different depths, over the user- and project-scope
    shape, where it always sits exactly one level down. A dangling symlink
    never matches a glob pattern, so it is tolerated without special-casing;
    a symlink loop surfaces as ``OSError`` during the scan and is translated
    like any other unreadable source.

    Returns an empty tuple when ``directory`` does not exist. Results are
    ordered by skill name for a deterministic inventory.

    Raises:
        MalformedSourceError: A candidate could not be statted or read for a
            reason other than absence, including a symlink loop.
        SourceChangedError: A candidate changed between the stat taken before
            its read and the one taken immediately after it.
    """
    if not directory.is_dir():
        return ()
    try:
        candidates = (
            _walk_skill_md_paths(directory)
            if recursive
            else list(directory.glob(_SCOPED_SKILL_GLOB))
        )
    except OSError as exc:
        raise MalformedSourceError(f"could not scan skills under {directory}") from exc

    entries = [entry for entry in (_read_skill_entry(path) for path in candidates) if entry]
    return tuple(sorted(entries, key=lambda entry: entry.skill_name))


def _walk_skill_md_paths(directory: Path) -> list[Path]:
    """Return every ``SKILL.md`` reachable under ``directory`` at any depth.

    Uses ``os.walk`` rather than ``Path.glob(..., recurse_symlinks=True)``:
    that parameter needs Python 3.13, and this project declares a 3.12 floor.
    A directory already visited by its device and inode is not descended
    into again, which keeps a directory symlink cycle from recursing forever.
    """
    matches: list[Path] = []
    visited: set[tuple[int, int]] = set()
    for dirpath, dirnames, filenames in os.walk(directory, followlinks=True):
        stat_result = os.stat(dirpath)
        identity = (stat_result.st_dev, stat_result.st_ino)
        if identity in visited:
            dirnames[:] = []
            continue
        visited.add(identity)
        if _SKILL_MD_FILENAME in filenames:
            matches.append(Path(dirpath) / _SKILL_MD_FILENAME)
    return matches


def _read_skill_entry(path: Path) -> SkillInventoryEntry | None:
    try:
        stat_before = path.stat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise MalformedSourceError(f"could not stat {path}") from exc

    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise MalformedSourceError(f"could not read {path}") from exc

    try:
        stat_after = path.stat()
    except OSError as exc:
        raise MalformedSourceError(f"could not stat {path}") from exc
    if (stat_before.st_mtime_ns, stat_before.st_size) != (
        stat_after.st_mtime_ns,
        stat_after.st_size,
    ):
        raise SourceChangedError(f"{path} changed while being read")

    return SkillInventoryEntry(
        skill_name=_resolve_skill_name(path, text),
        revision=SourceRevision(
            mtime_ns=stat_after.st_mtime_ns, size=stat_after.st_size, content_hash=hash_text(text)
        ),
    )


def _resolve_skill_name(path: Path, text: str) -> str:
    """Return the skill's name from its own frontmatter, falling back to its leaf directory.

    The leaf directory name matches the skill in the user- and project-scope
    shape, and in two of the three plugin-cache depth shapes, but the
    ``<marketplace>/<skill>/<hash>/SKILL.md`` shape's leaf is a content hash,
    not the skill name. ``SKILL.md``'s own ``name:`` field is reliable across
    every shape, so it is preferred whenever present.
    """
    try:
        frontmatter = parse_frontmatter(text, source_path_label=str(path))
        name = scalar_field(frontmatter, "name", source_path_label=str(path))
    except MalformedSourceError:
        return path.parent.name
    return name if name is not None else path.parent.name


def merge_skill_inventory(
    entries: Sequence[SkillInventoryEntry],
) -> Mapping[str, SourceRevision]:
    """Merge inventory entries by skill name, keeping the oldest revision on a collision.

    The same skill name can appear under more than one scope or plugin
    location. The oldest observed revision is kept because it is the
    stronger proof that the skill predates any given spawn; a newer copy
    elsewhere does not make an older one less true.
    """
    merged: dict[str, SourceRevision] = {}
    for entry in entries:
        existing = merged.get(entry.skill_name)
        if existing is None or entry.revision.mtime_ns < existing.mtime_ns:
            merged[entry.skill_name] = entry.revision
    return merged


def resolve_skill_availability(
    inventory: Mapping[str, SourceRevision], *, skill_name: str, started_at: datetime
) -> KnownState:
    """Return whether ``skill_name`` is provably available as of ``started_at``.

    Presence with a revision no later than ``started_at`` proves availability.
    Absence, or presence with a revision newer than the spawn, cannot rule
    availability out: a skill's current absence does not prove it was
    unavailable when the spawn ran, and a newer revision only proves the
    skill exists now, not that it already existed then. Both resolve to
    ``unknown`` rather than ``false``.
    """
    revision = inventory.get(skill_name)
    if revision is None:
        return KnownState.UNKNOWN
    spawn_epoch_ns = int(started_at.timestamp() * _NANOSECONDS_PER_SECOND)
    if revision.mtime_ns <= spawn_epoch_ns:
        return KnownState.TRUE
    return KnownState.UNKNOWN
