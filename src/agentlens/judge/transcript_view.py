"""Build the judge's prepared transcript view (design D1): a condensed text
document derived from a `ParsedSession` and its raw JSONL transcript, sized
for a single judge call rather than the full raw transcript.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from agentlens.aggregation.derivation import count_duplicate_tool_calls
from agentlens.parser.extraction import read_jsonl_records
from agentlens.parser.session import ParsedSession

TASK_DESCRIPTION_MAX_CHARS: Final[int] = 2000
ERROR_EXCERPT_MAX_CHARS: Final[int] = 300
BASH_COMMAND_MAX_CHARS: Final[int] = 120
TRUNCATION_MARKER: Final[str] = "... [truncated]"
NO_FINAL_REPORT: Final[str] = "(no final report)"
NO_TASK_DESCRIPTION: Final[str] = "(no task description)"
UNKNOWN_PATH: Final[str] = "?"
TOKENS_PER_K: Final[int] = 1000

# PERF-01: the view must stay under a hard byte ceiling regardless of how
# large the raw transcript is. Fixed sections (Task, Identity, Facts,
# Errors & Denials) are already bounded; the remaining budget is split
# between the two unbounded sections (Final Report, Tool Sequence).
VIEW_MAX_BYTES: Final[int] = 20_480
TOOL_SEQUENCE_HEAD: Final[int] = 40
TOOL_SEQUENCE_TAIL: Final[int] = 10
_REPORT_BUDGET_FRACTION: Final[float] = 0.6
_SECTION_SEPARATOR: Final[str] = "\n\n"
_NUM_SECTIONS: Final[int] = 6

_EXIT_CODE_RE: Final[re.Pattern[str]] = re.compile(r"^Exit code (\d+)")


@dataclass(frozen=True)
class _ToolCall:
    """One resolved tool_use/tool_result pair, kept in file order."""

    tool_name: str
    tool_input: Any
    is_error: bool
    denial_kind: str | None
    result_content: Any


def build_transcript_view(parsed: ParsedSession, jsonl_path: Path) -> str:
    """Build the structured text document a judge scores instead of the raw
    JSONL transcript (design D1/D3): Task, Agent Identity, Deterministic
    Facts, Tool Sequence, Errors & Denials, Final Report.

    The result is byte-budgeted to `VIEW_MAX_BYTES`: the four fixed sections
    (Task, Identity, Facts, Errors & Denials) are already bounded, and the
    remaining budget is split between the two unbounded sections (Final
    Report, Tool Sequence), reallocating any unused share from one to the
    other before truncating whichever still exceeds its budget.
    """
    records = read_jsonl_records(jsonl_path)
    tool_calls = _extract_tool_calls(records)

    task_section = _build_task_section(_extract_task_description(records, parsed))
    identity_section = _build_identity_section(parsed)
    facts_section = _build_facts_section(parsed)
    errors_section = _build_errors_section(tool_calls)

    fixed_bytes = sum(
        len(section.encode("utf-8"))
        for section in (task_section, identity_section, facts_section, errors_section)
    )
    separator_overhead = len(_SECTION_SEPARATOR.encode("utf-8")) * (_NUM_SECTIONS - 1)
    remaining_budget = max(VIEW_MAX_BYTES - fixed_bytes - separator_overhead, 0)

    report_section_full = f"## Final Report\n{_extract_final_report(records)}"
    tool_section_full = f"## Tool Sequence\n{_build_tool_sequence_body(tool_calls)}"

    report_budget = int(remaining_budget * _REPORT_BUDGET_FRACTION)
    tool_budget = remaining_budget - report_budget

    report_natural_bytes = len(report_section_full.encode("utf-8"))
    tool_natural_bytes = len(tool_section_full.encode("utf-8"))

    if report_natural_bytes <= report_budget:
        tool_budget += report_budget - report_natural_bytes
        report_budget = report_natural_bytes
    elif tool_natural_bytes <= tool_budget:
        report_budget += tool_budget - tool_natural_bytes
        tool_budget = tool_natural_bytes

    final_report_section = _truncate_bytes(report_section_full, max_bytes=report_budget)
    tool_sequence_section = _truncate_bytes(tool_section_full, max_bytes=tool_budget)

    sections = [
        task_section,
        identity_section,
        facts_section,
        tool_sequence_section,
        errors_section,
        final_report_section,
    ]
    return _SECTION_SEPARATOR.join(sections)


def _display(value: object) -> str:
    return str(value) if value is not None else "(none)"


def _truncate(text: str, *, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + TRUNCATION_MARKER


def _truncate_bytes(text: str, *, max_bytes: int, marker: str = TRUNCATION_MARKER) -> str:
    """Truncate `text` to at most `max_bytes` UTF-8 bytes, appending `marker`
    when truncation occurs. Never returns text whose encoded length exceeds
    `max_bytes` under normal (non-degenerate) budgets.
    """
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    marker_bytes = marker.encode("utf-8")
    if max_bytes <= len(marker_bytes):
        return marker.encode("utf-8")[: max(max_bytes, 0)].decode("utf-8", errors="ignore")
    budget = max_bytes - len(marker_bytes)
    truncated = encoded[:budget].decode("utf-8", errors="ignore")
    return truncated + marker


def _format_tokens_k(tokens: int) -> str:
    return f"{round(tokens / TOKENS_PER_K)}K"


def _content_items(message: dict[str, Any]) -> list[dict[str, Any]]:
    content = message.get("content")
    if isinstance(content, list):
        return [item for item in content if isinstance(item, dict)]
    return []


def _reconstruct_user_text(content: Any) -> str:
    """Reconstruct a user record's text, handling both a plain string, a
    list of content blocks (`{"type": "text", ...}`), and the streaming
    char-by-char shape some transcripts use (a list of single-character
    strings with no block structure)."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list) or not content:
        return ""
    if all(isinstance(item, str) for item in content):
        return "".join(content)
    parts = [
        item.get("text")
        for item in content
        if isinstance(item, dict)
        and item.get("type") == "text"
        and isinstance(item.get("text"), str)
    ]
    return "\n".join(text for text in parts if text)


def _extract_task_description(records: list[dict[str, Any]], parsed: ParsedSession) -> str:
    for record in records:
        if record.get("type") != "user":
            continue
        message = record.get("message")
        if not isinstance(message, dict):
            continue
        text = _reconstruct_user_text(message.get("content")).strip()
        if text:
            return _truncate(text, max_chars=TASK_DESCRIPTION_MAX_CHARS)
        break
    if parsed.task_description:
        return _truncate(parsed.task_description, max_chars=TASK_DESCRIPTION_MAX_CHARS)
    return NO_TASK_DESCRIPTION


def _extract_tool_calls(records: list[dict[str, Any]]) -> list[_ToolCall]:
    """Re-pair `tool_use`/`tool_result` from the raw transcript, keeping the
    full (unhashed) input and result content the view needs to summarize —
    `ParsedSession.events` only carries hashes and byte counts.
    """
    tool_uses: dict[str, tuple[str, Any]] = {}
    calls: list[_ToolCall] = []
    for record in records:
        message = record.get("message")
        if not isinstance(message, dict):
            continue
        record_type = record.get("type")
        if record_type == "assistant":
            for item in _content_items(message):
                if item.get("type") != "tool_use":
                    continue
                tool_use_id = item.get("id")
                tool_name = item.get("name")
                if isinstance(tool_use_id, str) and isinstance(tool_name, str):
                    tool_uses[tool_use_id] = (tool_name, item.get("input"))
        elif record_type == "user":
            denial_kind = record.get("toolDenialKind")
            for item in _content_items(message):
                if item.get("type") != "tool_result":
                    continue
                tool_use_id = item.get("tool_use_id")
                if not isinstance(tool_use_id, str) or tool_use_id not in tool_uses:
                    continue
                tool_name, tool_input = tool_uses[tool_use_id]
                calls.append(
                    _ToolCall(
                        tool_name=tool_name,
                        tool_input=tool_input,
                        is_error=bool(item.get("is_error", False)),
                        denial_kind=denial_kind if isinstance(denial_kind, str) else None,
                        result_content=item.get("content"),
                    )
                )
    return calls


def _extract_exit_code(call: _ToolCall) -> int:
    """Bash tool_results carry `Exit code N` as a content prefix only on
    failure; a clean run has no such marker, so success implies exit 0."""
    if not call.is_error:
        return 0
    content = call.result_content
    text = content if isinstance(content, str) else ""
    match = _EXIT_CODE_RE.match(text)
    return int(match.group(1)) if match else 1


def _summarize_tool_call(call: _ToolCall) -> str:
    tool_input = call.tool_input if isinstance(call.tool_input, dict) else {}
    if call.tool_name == "Read":
        return f"Read {tool_input.get('file_path', UNKNOWN_PATH)}"
    if call.tool_name == "Write":
        path = tool_input.get("file_path", UNKNOWN_PATH)
        content = tool_input.get("content")
        size = len(content.encode("utf-8")) if isinstance(content, str) else 0
        return f"Write {path} ({size} bytes)"
    if call.tool_name == "Edit":
        return f"Edit {tool_input.get('file_path', UNKNOWN_PATH)}"
    if call.tool_name == "Bash":
        command = tool_input.get("command", "")
        command = command if isinstance(command, str) else ""
        return f"Bash: {command[:BASH_COMMAND_MAX_CHARS]} → exit {_extract_exit_code(call)}"
    return call.tool_name


def _error_excerpt(content: Any) -> str:
    text = content if isinstance(content, str) else json.dumps(content, default=str)
    return _truncate(text, max_chars=ERROR_EXCERPT_MAX_CHARS)


def _build_task_section(task_description: str) -> str:
    return f"## Task\n{task_description}"


def _build_identity_section(parsed: ParsedSession) -> str:
    lines = [
        f"- type: {_display(parsed.name)}",
        f"- spawn_depth: {_display(parsed.spawn_depth)}",
        f"- parent_session: {_display(parsed.parent_session_id)}",
    ]
    return "## Agent Identity\n" + "\n".join(lines)


def _build_facts_section(parsed: ParsedSession) -> str:
    events = parsed.events
    n_errors = sum(1 for event in events if event.is_error)
    n_permission_denials = sum(1 for event in events if event.denial_kind is not None)
    lines = [
        f"- turns: {parsed.n_turns}, tool_calls: {len(events)}, "
        f"duration: {round(parsed.duration_sec)}s",
        f"- errors: {n_errors}, permission_denials: {n_permission_denials}, "
        f"duplicate_calls: {count_duplicate_tool_calls(events)}",
        f"- tokens: input={_format_tokens_k(parsed.input_tokens)}, "
        f"output={_format_tokens_k(parsed.output_tokens)}, "
        f"cache_read={_format_tokens_k(parsed.cache_read_tokens)}",
        f"- final_report_flagged_partial: {str(parsed.final_report_flagged_partial).lower()}",
    ]
    return "## Deterministic Facts\n" + "\n".join(lines)


def _build_tool_sequence_body(tool_calls: list[_ToolCall]) -> str:
    """Render the Tool Sequence body, sampling to head/tail when there are
    more calls than `TOOL_SEQUENCE_HEAD + TOOL_SEQUENCE_TAIL`. Every
    error/denial entry that would otherwise fall in the omitted middle range
    is preserved so critical facts survive the sampling.
    """
    total = len(tool_calls)
    if total == 0:
        return "(no tool calls)"
    if total <= TOOL_SEQUENCE_HEAD + TOOL_SEQUENCE_TAIL:
        return "\n".join(
            f"{i}. {_summarize_tool_call(call)}" for i, call in enumerate(tool_calls, start=1)
        )

    head = tool_calls[:TOOL_SEQUENCE_HEAD]
    tail_start_index = total - TOOL_SEQUENCE_TAIL
    tail = tool_calls[tail_start_index:]
    middle = tool_calls[TOOL_SEQUENCE_HEAD:tail_start_index]

    lines = [f"{i}. {_summarize_tool_call(call)}" for i, call in enumerate(head, start=1)]

    omitted = total - TOOL_SEQUENCE_HEAD - TOOL_SEQUENCE_TAIL
    lines.append(f"... [{omitted} calls omitted] ...")

    preserved = [
        (idx, call)
        for idx, call in enumerate(middle, start=TOOL_SEQUENCE_HEAD + 1)
        if call.is_error or call.denial_kind is not None
    ]
    if preserved:
        lines.append("Preserved errors/denials from omitted range:")
        lines.extend(f"{idx}. {_summarize_tool_call(call)}" for idx, call in preserved)

    lines.extend(
        f"{i}. {_summarize_tool_call(call)}"
        for i, call in enumerate(tail, start=tail_start_index + 1)
    )
    return "\n".join(lines)


def _build_errors_section(tool_calls: list[_ToolCall]) -> str:
    lines = []
    for i, call in enumerate(tool_calls, start=1):
        if not call.is_error and call.denial_kind is None:
            continue
        kind = "denial" if call.denial_kind is not None else "error"
        lines.append(f"- [step {i}] {call.tool_name} {kind}: {_error_excerpt(call.result_content)}")
    body = "\n".join(lines) if lines else "(none)"
    return f"## Errors & Denials\n{body}"


def _extract_final_report(records: list[dict[str, Any]]) -> str:
    last_text_parts: list[str] | None = None
    for record in records:
        if record.get("type") != "assistant":
            continue
        message = record.get("message")
        if not isinstance(message, dict):
            continue
        last_text_parts = [
            text
            for item in _content_items(message)
            if item.get("type") == "text" and isinstance(text := item.get("text"), str)
        ]
    if not last_text_parts:
        return NO_FINAL_REPORT
    text = "\n".join(part for part in last_text_parts if part)
    return text if text else NO_FINAL_REPORT
