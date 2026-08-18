"""Reading the optional ``.meta.json`` sidecar next to a subagent transcript."""

import json
from dataclasses import dataclass
from pathlib import Path

from agentlens.errors import MalformedSourceError
from agentlens.models.identity import SourceRevision
from agentlens.utils.hashing import hash_text

_SIDECAR_SUFFIX = ".meta.json"


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


def read_sidecar(transcript_path: Path) -> Sidecar | None:
    """Return the sidecar next to ``transcript_path``, or ``None`` if absent.

    Raises:
        MalformedSourceError: The sidecar exists but is not valid JSON, is
            not a JSON object, or is missing one of its required keys.
        OSError: The sidecar exists but could not be read for a reason other
            than its absence.
    """
    sidecar_path = transcript_path.with_suffix(_SIDECAR_SUFFIX)
    try:
        stat = sidecar_path.stat()
        text = sidecar_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    revision = SourceRevision(
        mtime_ns=stat.st_mtime_ns, size=stat.st_size, content_hash=hash_text(text)
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
