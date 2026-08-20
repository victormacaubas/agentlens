"""Reading the optional ``.meta.json`` sidecar a discovered source bundle names.

The relationship between a transcript and its sidecar's path is resolved
once, by :func:`agentlens.ingest.identity.build_subagent_source_bundle`; this
module only reads whatever concrete path the caller hands it.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from agentlens.errors import MalformedSourceError, SourceChangedError
from agentlens.models.identity import SourceRevision
from agentlens.utils.hashing import hash_text


@dataclass(frozen=True, slots=True, kw_only=True)
class Sidecar:
    """The fields a ``.meta.json`` sidecar carries about its spawn.

    ``parent_agent_id`` and ``model`` are ``None`` when the sidecar omits
    them, which it does whenever they do not apply rather than writing a
    literal null.

    ``revision`` is the sidecar file's own observed state, distinct from the
    transcript's, so a sidecar edit can be detected even when the transcript
    itself did not change.
    """

    agent_type: str
    description: str
    tool_use_id: str
    spawn_depth: int
    parent_agent_id: str | None
    model: str | None
    revision: SourceRevision


def read_sidecar(sidecar_path: Path | None) -> Sidecar | None:
    """Return the sidecar at ``sidecar_path``, or ``None`` when there is none to read.

    ``sidecar_path`` is ``None`` when a discovered bundle found no sidecar
    next to its transcript; that case returns ``None`` immediately without
    touching the filesystem. A concrete path that no longer exists (deleted
    between discovery and this read) is treated the same way.

    Raises:
        MalformedSourceError: The sidecar could not be statted or read for a
            reason other than its absence, or exists but is not valid JSON,
            is not a JSON object, is missing one of its required keys, or has
            a field of the wrong type.
        SourceChangedError: The sidecar changed between the stat taken before
            its read and the one taken immediately after it.
    """
    if sidecar_path is None:
        return None
    try:
        stat_before = sidecar_path.stat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise MalformedSourceError(f"could not stat {sidecar_path}") from exc

    try:
        text = sidecar_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise MalformedSourceError(f"could not read {sidecar_path}") from exc

    try:
        stat_after = sidecar_path.stat()
    except OSError as exc:
        raise MalformedSourceError(f"could not stat {sidecar_path}") from exc
    if (stat_before.st_mtime_ns, stat_before.st_size) != (
        stat_after.st_mtime_ns,
        stat_after.st_size,
    ):
        raise SourceChangedError(f"{sidecar_path} changed while being read")
    revision = SourceRevision(
        mtime_ns=stat_after.st_mtime_ns, size=stat_after.st_size, content_hash=hash_text(text)
    )

    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise MalformedSourceError(f"sidecar {sidecar_path} is not valid JSON") from exc
    if not isinstance(raw, dict):
        raise MalformedSourceError(f"sidecar {sidecar_path} is not a JSON object")

    try:
        agent_type = raw["agentType"]
        description = raw["description"]
        tool_use_id = raw["toolUseId"]
        spawn_depth = raw["spawnDepth"]
    except KeyError as exc:
        raise MalformedSourceError(f"sidecar {sidecar_path} is missing {exc}") from exc
    if not (
        isinstance(agent_type, str)
        and isinstance(description, str)
        and isinstance(tool_use_id, str)
        and isinstance(spawn_depth, int)
    ):
        raise MalformedSourceError(f"sidecar {sidecar_path} has a field of the wrong type")

    parent_agent_id = raw.get("parentAgentId")
    model = raw.get("model")
    return Sidecar(
        agent_type=agent_type,
        description=description,
        tool_use_id=tool_use_id,
        spawn_depth=spawn_depth,
        parent_agent_id=parent_agent_id if isinstance(parent_agent_id, str) else None,
        model=model if isinstance(model, str) else None,
        revision=revision,
    )
