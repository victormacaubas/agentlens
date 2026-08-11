from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Final

from agentlens.discovery.models import qualify_session_id
from agentlens.parser.extraction import (
    ParseHealth,
    consume_jsonl_records,
    extract_task_subagent_types,
    extract_transcript_facts,
)
from agentlens.parser.name_resolution import resolve_name
from agentlens.store.models import AgentDefRecord, SourceRevision, ToolEventRecord

logger = logging.getLogger(__name__)

SESSION_KIND_MAIN: Final[str] = "main"
SESSION_KIND_SUBAGENT: Final[str] = "subagent"

_FRONTMATTER_DELIM: Final[str] = "---"
_EMPTY_CONTENT_HASH: Final[str] = hashlib.sha256(b"").hexdigest()


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
    raw_session_id: str = ""
    source_project: str = ""
    source_revision: SourceRevision = SourceRevision(0, 0, _EMPTY_CONTENT_HASH)
    parse_health: ParseHealth = ParseHealth(0, 0, 0, False, False)
    source_path: Path | None = None
    judge_input_hash: str | None = None
    agent_definition_id: str | None = None
    project_root: Path | None = None


def parse_main_session(
    path: Path,
    *,
    session_id: str,
    source_project: str = "",
) -> ParsedSession:
    """Parse a top-level session transcript. Main sessions carry no lineage."""
    qualified_id = qualify_session_id(
        source_project=source_project,
        session_kind=SESSION_KIND_MAIN,
        raw_session_id=session_id,
    )
    consumed = consume_jsonl_records(
        path,
        lambda records: extract_transcript_facts(records, session_id=qualified_id),
    )
    facts = consumed.value
    health = replace(
        consumed.health,
        pending_tool_use_overflow_count=facts.pending_tool_use_overflow_count,
    )
    return ParsedSession(
        session_id=qualified_id,
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
        raw_session_id=session_id,
        source_project=source_project,
        source_revision=consumed.source_revision,
        parse_health=health,
        source_path=path,
        project_root=(
            Path(facts.working_directory) if facts.working_directory is not None else None
        ),
    )


def parse_subagent_run(
    jsonl_path: Path,
    *,
    agent_id: str,
    parent_session_id: str,
    source_project: str = "",
    meta: dict[str, Any] | None = None,
    parent_records: Iterable[dict[str, Any]] = (),
    parent_task_subagent_types: dict[str, str] | None = None,
) -> ParsedSession:
    """Parse a subagent transcript, resolving lineage and name.

    `session_id` is qualified by source project, session kind, and the raw
    filename-derived `agent_id`, so distinct source inputs cannot collide.

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
    qualified_id = qualify_session_id(
        source_project=source_project,
        session_kind=SESSION_KIND_SUBAGENT,
        raw_session_id=agent_id,
    )
    qualified_parent_id = qualify_session_id(
        source_project=source_project,
        session_kind=SESSION_KIND_MAIN,
        raw_session_id=parent_session_id,
    )
    consumed = consume_jsonl_records(
        jsonl_path,
        lambda records: extract_transcript_facts(records, session_id=qualified_id),
    )
    facts = consumed.value
    health = replace(
        consumed.health,
        pending_tool_use_overflow_count=facts.pending_tool_use_overflow_count,
    )

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
        session_id=qualified_id,
        session_kind=SESSION_KIND_SUBAGENT,
        agent_id=agent_id,
        name=resolution.name,
        name_source=resolution.name_source,
        ambiguous=resolution.ambiguous,
        parent_session_id=qualified_parent_id,
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
        raw_session_id=agent_id,
        source_project=source_project,
        source_revision=consumed.source_revision,
        parse_health=health,
        source_path=jsonl_path,
        project_root=(
            Path(facts.working_directory) if facts.working_directory is not None else None
        ),
    )


def parse_agent_definition(
    path: Path,
    *,
    scope: str = "user",
    source_project: str | None = None,
    on_error: Callable[[Path, OSError], None] | None = None,
) -> AgentDefRecord | None:
    """Parse a `.claude/agents/**.md` file's YAML-style frontmatter.

    Returns `None` (skipped, not fatal) if the file has no frontmatter or is
    unreadable.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        logger.debug("Could not read agent definition %s", path)
        if on_error is not None:
            on_error(path, error)
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
        scope=scope,
        source_project=source_project,
    )


def _parse_frontmatter(text: str) -> dict[str, str] | None:
    """Parse the flat `key: value` YAML frontmatter agent defs use.

    Deliberately not a general YAML parser, which would mean a new
    dependency: every observed agent def frontmatter is a flat key/value
    block, so a line-oriented split is sufficient.
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
