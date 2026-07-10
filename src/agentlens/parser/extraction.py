from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from agentlens.store import ToolEventRecord

logger = logging.getLogger(__name__)

# Marker set for `final_report_flagged_partial` (D2): a small, conservative,
# documented list of phrases in a final assistant text block that suggest
# incomplete work. This is a raw marker, NOT a completion verdict — every
# observed transcript ends `stop_reason: end_turn` regardless of whether the
# work was actually finished, so `stop_reason` cannot substitute for this.
#
# `_PARTIAL_CHECKBOX_MARKER` is matched as a plain substring (it has no
# word-boundary concept); every other marker is matched with `\b` word
# boundaries so e.g. `partial` does not fire on `partially` and `blocked`
# does not fire on `unblocked` (BUG-01).
_PARTIAL_CHECKBOX_MARKER: Final[str] = "- [ ]"
PARTIAL_MARKERS: Final[tuple[str, ...]] = (
    _PARTIAL_CHECKBOX_MARKER,
    "partial",
    "blocked",
    "couldn't",
    "could not",
    "unable to",
)
_PARTIAL_WORD_MARKERS: Final[tuple[str, ...]] = tuple(
    marker for marker in PARTIAL_MARKERS if marker != _PARTIAL_CHECKBOX_MARKER
)
_PARTIAL_WORD_RE: Final[re.Pattern[str]] = re.compile(
    "|".join(rf"\b{re.escape(marker)}\b" for marker in _PARTIAL_WORD_MARKERS),
    re.IGNORECASE,
)

# Tools whose fired-skill signal comes from a `Skill` tool_use naming a skill
# in its input. The exact input key is not pinned by any known schema, so
# every plausible key is checked defensively.
_SKILL_TOOL_NAME: Final[str] = "Skill"
_SKILL_TOOL_INPUT_KEYS: Final[tuple[str, ...]] = ("name", "skill_name", "skill", "command")

_SKILL_FORMAT_MARKER: Final[str] = "<skill-format>true"
_COMMAND_NAME_RE: Final[re.Pattern[str]] = re.compile(r"<command-name>(.*?)</command-name>")


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


def _message_text(message: dict[str, Any]) -> str:
    """Concatenate every `text` content item in a message, in order.

    Handles both the list-of-content-items shape and a plain string
    `content` (some record types use the latter).
    """
    content = message.get("content")
    if isinstance(content, str):
        return content
    parts = [
        item.get("text")
        for item in _content_items(message)
        if item.get("type") == "text" and isinstance(item.get("text"), str)
    ]
    return "\n".join(text for text in parts if text)


def flags_partial(text: str | None) -> bool:
    """True iff `text` matches a marker in `PARTIAL_MARKERS` (case-insensitive).

    Word-based markers require `\\b` word boundaries on both sides (BUG-01):
    `partial` matches `partially complete` -> False, `The task was
    blocked.` -> True. The checkbox marker matches as a plain substring.

    A raw signal, not a completion verdict (D2) — the judge owns the real
    completion assessment.
    """
    if not text:
        return False
    if _PARTIAL_CHECKBOX_MARKER in text:
        return True
    return _PARTIAL_WORD_RE.search(text) is not None


def _skill_name_from_skill_tool_use(item: dict[str, Any]) -> str | None:
    """Return the skill name from a `Skill` tool_use's input, if present."""
    if item.get("type") != "tool_use" or item.get("name") != _SKILL_TOOL_NAME:
        return None
    tool_input = item.get("input")
    if not isinstance(tool_input, dict):
        return None
    for key in _SKILL_TOOL_INPUT_KEYS:
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _skill_names_from_meta_record(record: dict[str, Any]) -> list[str]:
    """Resolve fired skill names from an `isMeta:true` injection-marker record.

    Only records carrying `<skill-format>true` in their text yield names —
    `SKILL.md` reads and other meta records are ignored (D3, noisy).
    """
    if record.get("isMeta") is not True:
        return []
    message = record.get("message")
    if not isinstance(message, dict):
        return []
    text = _message_text(message)
    if _SKILL_FORMAT_MARKER not in text:
        return []
    return [name.strip() for name in _COMMAND_NAME_RE.findall(text) if name.strip()]


def _parse_timestamp(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


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
    """Facts extracted from one transcript's records, in file order.

    `n_turns`/usage/`duration_sec` are read directly from the transcript
    (D1) — they are turn-level facts, not tool-event facts, so they cannot
    be derived from `tool_events` alone.
    """

    tool_events: list[ToolEventRecord]
    attribution_agents: list[str]
    task_subagent_types: dict[str, str]  # tool_use_id -> Task's subagent_type
    n_turns: int
    duration_sec: float
    first_ts: str | None
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    fired_skills: list[str]
    final_report_flagged_partial: bool


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


def _usage_int(usage: dict[str, Any], key: str) -> int:
    """Read an integer usage field defensively; anything else, or a
    negative value (BUG-02: a corrupted JSONL can carry one), contributes 0."""
    value = usage.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(0, value)


def _duration_seconds(first_ts: str | None, last_ts: str | None) -> float:
    if first_ts is None or last_ts is None:
        return 0.0
    first_dt = _parse_timestamp(first_ts)
    last_dt = _parse_timestamp(last_ts)
    if first_dt is None or last_dt is None:
        return 0.0
    return max((last_dt - first_dt).total_seconds(), 0.0)


def extract_transcript_facts(
    records: Iterable[dict[str, Any]],
    *,
    session_id: str,
) -> TranscriptFacts:
    """Pair `tool_use` -> `tool_result` and collect name-resolution,
    usage/turn/duration, and skill-fire signals.

    `tool_result` items are matched to their `tool_use` by
    `tool_result.tool_use_id` (confirmed present in real logs). Unknown
    record types, and `tool_result`s with no matching `tool_use`, are
    skipped rather than treated as errors. Usage (`message.usage`) is read
    defensively per assistant turn (D1): a missing object or field
    contributes zero and never aborts parsing.
    """
    tool_uses: dict[str, tuple[str, Any]] = {}
    task_subagent_types: dict[str, str] = {}
    attribution_agents: list[str] = []
    fired_skills: list[str] = []
    events: list[ToolEventRecord] = []
    seq = 0
    n_turns = 0
    first_ts: str | None = None
    last_ts: str | None = None
    input_tokens = 0
    output_tokens = 0
    cache_read_tokens = 0
    cache_creation_tokens = 0
    final_assistant_text: str | None = None

    for record in records:
        record_ts = record.get("timestamp")
        if isinstance(record_ts, str):
            first_ts = first_ts if first_ts is not None else record_ts
            last_ts = record_ts

        for skill_name in _skill_names_from_meta_record(record):
            if skill_name not in fired_skills:
                fired_skills.append(skill_name)

        message = record.get("message")
        if not isinstance(message, dict):
            continue
        record_type = record.get("type")

        if record_type == "assistant":
            n_turns += 1
            attribution_agent = record.get("attributionAgent")
            if isinstance(attribution_agent, str) and attribution_agent not in attribution_agents:
                attribution_agents.append(attribution_agent)

            text = _message_text(message)
            if text:
                final_assistant_text = text

            usage = message.get("usage")
            if isinstance(usage, dict):
                input_tokens += _usage_int(usage, "input_tokens")
                output_tokens += _usage_int(usage, "output_tokens")
                cache_read_tokens += _usage_int(usage, "cache_read_input_tokens")
                cache_creation_tokens += _usage_int(usage, "cache_creation_input_tokens")

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
                skill_tool_use_name = _skill_name_from_skill_tool_use(item)
                if skill_tool_use_name is not None and skill_tool_use_name not in fired_skills:
                    fired_skills.append(skill_tool_use_name)

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
        n_turns=n_turns,
        duration_sec=_duration_seconds(first_ts, last_ts),
        first_ts=first_ts,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_creation_tokens=cache_creation_tokens,
        fired_skills=fired_skills,
        final_report_flagged_partial=flags_partial(final_assistant_text),
    )
