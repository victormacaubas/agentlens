"""Deterministic parser core: JSONL transcripts -> `fact_tool_event` rows and
`dim_agent` rows, plus the guarded name-resolution fallback chain.

No LLM calls here — this is Phase 1's raw-events-land-correctly layer.
Reads are defensive throughout: malformed lines, unknown record types, and
unpaired tool_use/tool_result blocks are skipped rather than aborting the
whole session (per design's JSONL-schema-drift risk acceptance).
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentlens.store import AgentDefRecord, ToolEventRecord

logger = logging.getLogger(__name__)

SESSION_KIND_MAIN = "main"
SESSION_KIND_SUBAGENT = "subagent"

NAME_SOURCE_META = "meta_agent_type"
NAME_SOURCE_ATTRIBUTION = "attribution_agent"
NAME_SOURCE_PARENT_TASK = "parent_task_subagent_type"
NAME_SOURCE_AGENT_ID_HASH = "agent_id_hash"

_FRONTMATTER_DELIM = "---"


# --------------------------------------------------------------------------
# JSONL transcript reading
# --------------------------------------------------------------------------


def read_jsonl_records(path: Path) -> list[dict[str, Any]]:
    """Read a JSONL transcript defensively.

    Malformed lines and non-object records are skipped and logged; the read
    never raises for content issues (only for the file being unreadable).
    """
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line_no, raw_line in enumerate(f, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                logger.debug("Skipping malformed JSONL line %d in %s", line_no, path)
                continue
            if not isinstance(record, dict):
                logger.debug("Skipping non-object JSONL line %d in %s", line_no, path)
                continue
            records.append(record)
    return records


def _content_items(message: dict[str, Any]) -> list[dict[str, Any]]:
    content = message.get("content")
    if isinstance(content, list):
        return [item for item in content if isinstance(item, dict)]
    return []


def _hash_input(tool_input: Any) -> str:
    try:
        payload = json.dumps(tool_input, sort_keys=True, default=str)
    except TypeError:
        payload = str(tool_input)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _estimate_output_bytes(content: Any) -> int:
    if isinstance(content, str):
        return len(content.encode("utf-8"))
    try:
        return len(json.dumps(content, default=str).encode("utf-8"))
    except TypeError:
        return len(str(content).encode("utf-8"))


@dataclass(frozen=True)
class TranscriptFacts:
    """Facts extracted from one transcript's records, in file order."""

    tool_events: list[ToolEventRecord]
    attribution_agents: list[str]
    task_subagent_types: dict[str, str]  # tool_use_id -> Task's subagent_type


def _task_subagent_type_from_item(item: dict[str, Any]) -> tuple[str, str] | None:
    """Return `(tool_use_id, subagent_type)` if `item` is a `Task` tool_use
    with a string `subagent_type`, else `None`."""
    if item.get("type") != "tool_use" or item.get("name") != "Task":
        return None
    tool_use_id = item.get("id")
    tool_input = item.get("input")
    if not isinstance(tool_use_id, str) or not isinstance(tool_input, dict):
        return None
    subagent_type = tool_input.get("subagent_type")
    if not isinstance(subagent_type, str):
        return None
    return tool_use_id, subagent_type


def extract_task_subagent_types(records: Iterable[dict[str, Any]]) -> dict[str, str]:
    """Collect `{tool_use_id: subagent_type}` for every `Task` tool_use in
    assistant records.

    Lightweight counterpart to `extract_transcript_facts` for callers that
    only need the Task -> subagent_type map (e.g. subagent name resolution
    looking up the parent's spawning `Task`). Iterates only assistant
    records and allocates no `ToolEventRecord`s, hashes no inputs, and pairs
    no `tool_result`s — this is the cheap path for the parent transcript,
    which bulk ingestion (Phase 2) would otherwise re-parse in full once per
    sibling subagent (see ARCH-01).
    """
    task_subagent_types: dict[str, str] = {}
    for record in records:
        if record.get("type") != "assistant":
            continue
        message = record.get("message")
        if not isinstance(message, dict):
            continue
        for item in _content_items(message):
            task_signal = _task_subagent_type_from_item(item)
            if task_signal is not None:
                task_subagent_types[task_signal[0]] = task_signal[1]
    return task_subagent_types


def extract_transcript_facts(
    records: Iterable[dict[str, Any]],
    *,
    session_id: str,
) -> TranscriptFacts:
    """Pair `tool_use` -> `tool_result` and collect name-resolution signals.

    `tool_result` items are matched to their `tool_use` by
    `tool_result.tool_use_id` (confirmed present in real logs). Unknown
    record types, and `tool_result`s with no matching `tool_use`, are
    skipped rather than treated as errors.
    """
    tool_uses: dict[str, tuple[str, Any]] = {}
    task_subagent_types: dict[str, str] = {}
    attribution_agents: list[str] = []
    events: list[ToolEventRecord] = []
    seq = 0

    for record in records:
        message = record.get("message")
        if not isinstance(message, dict):
            continue
        record_type = record.get("type")

        if record_type == "assistant":
            attribution_agent = record.get("attributionAgent")
            if isinstance(attribution_agent, str) and attribution_agent not in attribution_agents:
                attribution_agents.append(attribution_agent)
            for item in _content_items(message):
                if item.get("type") != "tool_use":
                    continue
                tool_use_id = item.get("id")
                tool_name = item.get("name")
                if not isinstance(tool_use_id, str) or not isinstance(tool_name, str):
                    continue
                tool_input = item.get("input")
                tool_uses[tool_use_id] = (tool_name, tool_input)
                task_signal = _task_subagent_type_from_item(item)
                if task_signal is not None:
                    task_subagent_types[task_signal[0]] = task_signal[1]

        elif record_type == "user":
            # `toolDenialKind` is a record-level field in Claude Code's
            # protocol (set on the enclosing "user" record, not per
            # tool_result item) — it is applied to every tool_result paired
            # in this message. Deliberate grain assumption; revisit if a
            # transcript is observed with multiple tool_results carrying
            # different denial kinds in one user record.
            denial_kind = record.get("toolDenialKind")
            ts = record.get("timestamp")
            for item in _content_items(message):
                if item.get("type") != "tool_result":
                    continue
                tool_use_id = item.get("tool_use_id")
                if not isinstance(tool_use_id, str) or tool_use_id not in tool_uses:
                    continue
                tool_name, tool_input = tool_uses[tool_use_id]
                seq += 1
                events.append(
                    ToolEventRecord(
                        session_id=session_id,
                        seq=seq,
                        tool_name=tool_name,
                        is_error=bool(item.get("is_error", False)),
                        denial_kind=denial_kind if isinstance(denial_kind, str) else None,
                        ts=ts if isinstance(ts, str) else None,
                        input_hash=_hash_input(tool_input),
                        output_bytes=_estimate_output_bytes(item.get("content")),
                    )
                )
        # Unknown record types (mode, permission-mode, file-history-snapshot,
        # attachment, ...) fall through untouched.

    return TranscriptFacts(
        tool_events=events,
        attribution_agents=attribution_agents,
        task_subagent_types=task_subagent_types,
    )


# --------------------------------------------------------------------------
# Name resolution (D4)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class NameResolution:
    name: str
    name_source: str
    ambiguous: bool


def resolve_name(
    *,
    meta_agent_type: str | None,
    attribution_agents: Iterable[str] = (),
    parent_task_subagent_type: str | None,
    agent_id: str,
) -> NameResolution:
    """Resolve a subagent session's name via the guarded fallback chain.

    Order, authoritative first: (1) `.meta.json` `agentType`, (2) distinct
    `attributionAgent` values from the session's own assistant records,
    (3) the parent's `Task` `subagent_type`, (4) the `agent_id` hash — never
    dropping a session. Conflicting distinct signals across the whole chain
    are flagged `ambiguous`, even though one source still wins per priority.
    """
    distinct_attribution = sorted({a for a in attribution_agents if a})
    all_signals = (meta_agent_type, *distinct_attribution, parent_task_subagent_type)
    candidates = [c for c in all_signals if c]
    ambiguous = len(set(candidates)) > 1

    if meta_agent_type:
        return NameResolution(
            name=meta_agent_type, name_source=NAME_SOURCE_META, ambiguous=ambiguous
        )
    if distinct_attribution:
        return NameResolution(
            name=distinct_attribution[0], name_source=NAME_SOURCE_ATTRIBUTION, ambiguous=ambiguous
        )
    if parent_task_subagent_type:
        return NameResolution(
            name=parent_task_subagent_type,
            name_source=NAME_SOURCE_PARENT_TASK,
            ambiguous=ambiguous,
        )
    return NameResolution(name=agent_id, name_source=NAME_SOURCE_AGENT_ID_HASH, ambiguous=False)


# --------------------------------------------------------------------------
# Session-level parsing
# --------------------------------------------------------------------------


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


# --------------------------------------------------------------------------
# Agent definitions (-> dim_agent)
# --------------------------------------------------------------------------


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
