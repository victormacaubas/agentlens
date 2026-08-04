from __future__ import annotations

import hashlib
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from agentlens.parser.extraction import (
    extract_task_subagent_types,
    extract_transcript_facts,
    read_jsonl_records,
)
from agentlens.parser.name_resolution import resolve_name
from agentlens.store.models import AgentDefRecord, ToolEventRecord

logger = logging.getLogger(__name__)

SESSION_KIND_MAIN: Final[str] = "main"
SESSION_KIND_SUBAGENT: Final[str] = "subagent"

_FRONTMATTER_DELIM: Final[str] = "---"


@dataclass(frozen=True)
class ParsedSession:
    """The parser's output for one session: events, identity/lineage, and
    the transcript-read fields aggregation combines into `fact_session` —
    usage/turns/duration are turn-level facts absent from
    `fact_tool_event`, so the parser returns them directly.
    """

    session_id: str
    session_kind: str
    agent_id: str | None
    name: str | None
    name_source: str | None
    ambiguous: bool
    parent_session_id: str | None
    spawn_tool_use_id: str | None
    task_description: str | None
    spawn_depth: int | None
    events: list[ToolEventRecord]
    n_turns: int
    duration_sec: float
    first_ts: str | None
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    fired_skills: list[str]
    final_report_flagged_partial: bool


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
        task_description=None,
        spawn_depth=None,
        events=facts.tool_events,
        n_turns=facts.n_turns,
        duration_sec=facts.duration_sec,
        first_ts=facts.first_ts,
        input_tokens=facts.input_tokens,
        output_tokens=facts.output_tokens,
        cache_read_tokens=facts.cache_read_tokens,
        cache_creation_tokens=facts.cache_creation_tokens,
        fired_skills=facts.fired_skills,
        final_report_flagged_partial=facts.final_report_flagged_partial,
    )


def parse_subagent_run(
    jsonl_path: Path,
    *,
    agent_id: str,
    parent_session_id: str,
    meta: dict[str, Any] | None = None,
    parent_records: Iterable[dict[str, Any]] = (),
    parent_task_subagent_types: dict[str, str] | None = None,
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
            proceeds (never drops a session). Ignored when
            `parent_task_subagent_types` is given.
        parent_task_subagent_types: A precomputed `{tool_use_id:
            subagent_type}` map for the parent transcript. Bulk ingest
            passes this once per parent and reuses it across sibling
            spawns instead of re-deriving it from `parent_records` per
            spawn (see `agentlens.ingest.ingest_all`).
    """
    facts = extract_transcript_facts(read_jsonl_records(jsonl_path), session_id=agent_id)

    meta = meta or {}
    meta_agent_type = meta.get("agentType")
    meta_agent_type = meta_agent_type if isinstance(meta_agent_type, str) else None
    spawn_tool_use_id = meta.get("toolUseId")
    spawn_tool_use_id = spawn_tool_use_id if isinstance(spawn_tool_use_id, str) else None
    task_description = meta.get("description")
    task_description = task_description if isinstance(task_description, str) else None
    spawn_depth = meta.get("spawnDepth")
    spawn_depth = spawn_depth if isinstance(spawn_depth, int) else None

    task_map = (
        parent_task_subagent_types
        if parent_task_subagent_types is not None
        else extract_task_subagent_types(parent_records)
    )
    parent_task_subagent_type: str | None = (
        task_map.get(spawn_tool_use_id) if spawn_tool_use_id else None
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
        task_description=task_description,
        spawn_depth=spawn_depth,
        events=facts.tool_events,
        n_turns=facts.n_turns,
        duration_sec=facts.duration_sec,
        first_ts=facts.first_ts,
        input_tokens=facts.input_tokens,
        output_tokens=facts.output_tokens,
        cache_read_tokens=facts.cache_read_tokens,
        cache_creation_tokens=facts.cache_creation_tokens,
        fired_skills=facts.fired_skills,
        final_report_flagged_partial=facts.final_report_flagged_partial,
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
