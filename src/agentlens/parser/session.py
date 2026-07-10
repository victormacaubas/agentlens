from __future__ import annotations

import hashlib
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentlens.parser.extraction import (
    extract_task_subagent_types,
    extract_transcript_facts,
    read_jsonl_records,
)
from agentlens.parser.naming import resolve_name
from agentlens.store import AgentDefRecord, ToolEventRecord

logger = logging.getLogger(__name__)

SESSION_KIND_MAIN = "main"
SESSION_KIND_SUBAGENT = "subagent"

_FRONTMATTER_DELIM = "---"


@dataclass(frozen=True)
class ParsedSession:
    """The parser's output for one session: events plus resolved identity/lineage.

    Only `events` is persisted this change (into `fact_tool_event`); the
    rest is returned for the caller to log/inspect now and will land in
    `fact_session` in Phase 2.
    """

    session_id: str
    session_kind: str
    agent_id: str | None
    name: str | None
    name_source: str | None
    ambiguous: bool
    parent_session_id: str | None
    spawn_tool_use_id: str | None
    events: list[ToolEventRecord]


def parse_main_session(path: Path, *, session_id: str) -> ParsedSession:
    """Parse a top-level session transcript. Main sessions carry no lineage."""
    facts = extract_transcript_facts(read_jsonl_records(path), session_id=session_id)
    return ParsedSession(
        session_id=session_id,
        session_kind=SESSION_KIND_MAIN,
        agent_id=None,
        name=None,
        name_source=None,
        ambiguous=False,
        parent_session_id=None,
        spawn_tool_use_id=None,
        events=facts.tool_events,
    )


def parse_subagent_run(
    jsonl_path: Path,
    *,
    agent_id: str,
    parent_session_id: str,
    meta: dict[str, Any] | None = None,
    parent_records: Iterable[dict[str, Any]] = (),
) -> ParsedSession:
    """Parse a subagent transcript, resolving lineage and name.

    `session_id` is the `agent_id` (the per-spawn identity derived from the
    filename) — this is what keeps `fact_tool_event` rows unique per spawn
    even when multiple spawns of the same parent session share a
    `parent_session_id`.

    Args:
        jsonl_path: Path to `agent-<id>.jsonl`.
        agent_id: The spawn's identity, parsed from the filename.
        parent_session_id: The `<sid>` folder the `subagents/` dir sits under.
        meta: The parsed `.meta.json` sidecar, if present.
        parent_records: The parent transcript's raw records (already read by
            the caller), used only to look up the spawning `Task`'s
            `subagent_type` for the name-resolution fallback chain. Omit if
            the parent transcript is unavailable — resolution still
            proceeds (never drops a session).
    """
    facts = extract_transcript_facts(read_jsonl_records(jsonl_path), session_id=agent_id)

    meta = meta or {}
    meta_agent_type = meta.get("agentType")
    meta_agent_type = meta_agent_type if isinstance(meta_agent_type, str) else None
    spawn_tool_use_id = meta.get("toolUseId")
    spawn_tool_use_id = spawn_tool_use_id if isinstance(spawn_tool_use_id, str) else None

    parent_task_subagent_type: str | None = None
    if spawn_tool_use_id:
        parent_task_subagent_type = extract_task_subagent_types(parent_records).get(
            spawn_tool_use_id
        )

    resolution = resolve_name(
        meta_agent_type=meta_agent_type,
        attribution_agents=facts.attribution_agents,
        parent_task_subagent_type=parent_task_subagent_type,
        agent_id=agent_id,
    )

    return ParsedSession(
        session_id=agent_id,
        session_kind=SESSION_KIND_SUBAGENT,
        agent_id=agent_id,
        name=resolution.name,
        name_source=resolution.name_source,
        ambiguous=resolution.ambiguous,
        parent_session_id=parent_session_id,
        spawn_tool_use_id=spawn_tool_use_id,
        events=facts.tool_events,
    )


def parse_agent_definition(path: Path) -> AgentDefRecord | None:
    """Parse a `.claude/agents/**.md` file's YAML-style frontmatter.

    Returns `None` (skipped, not fatal) if the file has no frontmatter or is
    unreadable.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        logger.debug("Could not read agent definition %s", path)
        return None

    frontmatter = _parse_frontmatter(text)
    if frontmatter is None:
        return None

    name = frontmatter.get("name") or path.stem
    return AgentDefRecord(
        agent_type=name,
        name=name,
        model=frontmatter.get("model"),
        effort=frontmatter.get("effort"),
        declared_tools=_split_list(frontmatter.get("tools")),
        declared_skills=_split_list(frontmatter.get("skills")),
        definition_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


def _parse_frontmatter(text: str) -> dict[str, str] | None:
    """Parse the flat `key: value` YAML frontmatter agent defs use.

    Deliberately not a general YAML parser (no new dependency, D per
    standard-library-first) — every observed agent def frontmatter is a flat
    key/value block, so a line-oriented split is sufficient.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != _FRONTMATTER_DELIM:
        return None
    fields: dict[str, str] = {}
    for line in lines[1:]:
        stripped = line.strip()
        if stripped == _FRONTMATTER_DELIM:
            return fields
        if ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        fields[key.strip()] = value.strip()
    logger.debug("Unterminated frontmatter (no closing '---') in agent definition")
    return None


def _split_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]
