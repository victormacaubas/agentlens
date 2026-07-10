from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentlens.store import ToolEventRecord

logger = logging.getLogger(__name__)


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

    return TranscriptFacts(
        tool_events=events,
        attribution_agents=attribution_agents,
        task_subagent_types=task_subagent_types,
    )
