"""Build the bounded transcript view supplied to the judge."""

from __future__ import annotations

import json
import re
from collections import OrderedDict, deque
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from agentlens.aggregation.derivation import count_duplicate_tool_calls
from agentlens.parser.extraction import consume_jsonl_records
from agentlens.parser.session import ParsedSession

TASK_DESCRIPTION_MAX_CHARS: Final[int] = 2000
ERROR_EXCERPT_MAX_CHARS: Final[int] = 300
BASH_COMMAND_MAX_CHARS: Final[int] = 120
TRUNCATION_MARKER: Final[str] = "... [truncated]"
NO_FINAL_REPORT: Final[str] = "(no final report)"
NO_TASK_DESCRIPTION: Final[str] = "(no task description)"
UNKNOWN_PATH: Final[str] = "?"
TOKENS_PER_K: Final[int] = 1000
VIEW_MAX_BYTES: Final[int] = 20_480
TOOL_SEQUENCE_HEAD: Final[int] = 40
TOOL_SEQUENCE_TAIL: Final[int] = 10
ERROR_SAMPLE_HEAD: Final[int] = 5
ERROR_SAMPLE_TAIL: Final[int] = 5
MAX_PENDING_TOOL_USES: Final[int] = 4096
TOOL_LINE_MAX_BYTES: Final[int] = 500
ERROR_LINE_MAX_BYTES: Final[int] = 400

_TASK_SECTION_MAX_BYTES: Final[int] = 2400
_IDENTITY_SECTION_MAX_BYTES: Final[int] = 1500
_FACTS_SECTION_MAX_BYTES: Final[int] = 1500
_TOOL_SECTION_MAX_BYTES: Final[int] = 5800
_ERRORS_SECTION_MAX_BYTES: Final[int] = 4500
_FINAL_SECTION_MAX_BYTES: Final[int] = 4770
_SECTION_SEPARATOR: Final[str] = "\n\n"
_NUM_SECTIONS: Final[int] = 6

_EXIT_CODE_RE: Final[re.Pattern[str]] = re.compile(r"^Exit code (\d+)")


@dataclass(frozen=True)
class _PendingToolUse:
    tool_name: str
    summary: str


@dataclass(frozen=True)
class _ResolvedToolCall:
    step: int
    tool_name: str
    summary: str
    is_error: bool
    denial_kind: str | None
    error_excerpt: str


@dataclass(frozen=True)
class _ViewSections:
    task: str
    tool_sequence: str
    errors: str
    final_report: str


@dataclass(frozen=True)
class _Section:
    title: str
    body: str

    def render(self) -> str:
        return f"## {self.title}\n{self.body}"


class _TranscriptViewReducer:
    def __init__(self, parsed: ParsedSession) -> None:
        self._parsed = parsed
        self._task: str | None = None
        self._final_report: str | None = None
        self._pending: OrderedDict[str, _PendingToolUse] = OrderedDict()
        self._pending_overflow_count = 0
        self._tool_count = 0
        self._tool_head: list[str] = []
        self._tool_tail: deque[str] = deque(maxlen=TOOL_SEQUENCE_TAIL)
        self._error_count = 0
        self._error_head: list[str] = []
        self._error_tail: deque[str] = deque(maxlen=ERROR_SAMPLE_TAIL)

    def consume(self, records: Iterable[dict[str, Any]]) -> _ViewSections:
        for record in records:
            self._consume_record(record)

        task = self._task
        if task is None and self._parsed.task_description:
            task = _truncate(
                self._parsed.task_description,
                max_chars=TASK_DESCRIPTION_MAX_CHARS,
            )
        return _ViewSections(
            task=task or NO_TASK_DESCRIPTION,
            tool_sequence=self._tool_sequence_body(),
            errors=self._errors_body(),
            final_report=self._final_report or NO_FINAL_REPORT,
        )

    def _consume_record(self, record: dict[str, Any]) -> None:
        message = record.get("message")
        if not isinstance(message, dict):
            return
        record_type = record.get("type")
        if record_type == "assistant":
            self._consume_assistant(message)
        elif record_type == "user":
            self._consume_user(record, message)

    def _consume_assistant(self, message: dict[str, Any]) -> None:
        text = _message_text_prefix(
            message,
            max_chars=_FINAL_SECTION_MAX_BYTES,
        )
        if text:
            self._final_report = _truncate_bytes(
                text,
                max_bytes=_FINAL_SECTION_MAX_BYTES,
            )

        for item in _content_items(message):
            if item.get("type") != "tool_use":
                continue
            tool_use_id = item.get("id")
            tool_name = item.get("name")
            if not isinstance(tool_use_id, str) or not isinstance(tool_name, str):
                continue
            self._pending[tool_use_id] = _PendingToolUse(
                tool_name=tool_name,
                summary=_summarize_tool_use(tool_name, item.get("input")),
            )
            self._pending.move_to_end(tool_use_id)
            if len(self._pending) > MAX_PENDING_TOOL_USES:
                self._pending.popitem(last=False)
                self._pending_overflow_count += 1

    def _consume_user(
        self,
        record: dict[str, Any],
        message: dict[str, Any],
    ) -> None:
        if self._task is None:
            task = _message_text_prefix(
                message,
                max_chars=TASK_DESCRIPTION_MAX_CHARS + 1,
            ).strip()
            if task:
                self._task = _truncate(task, max_chars=TASK_DESCRIPTION_MAX_CHARS)

        denial_kind = record.get("toolDenialKind")
        denial = denial_kind if isinstance(denial_kind, str) else None
        for item in _content_items(message):
            if item.get("type") != "tool_result":
                continue
            tool_use_id = item.get("tool_use_id")
            if not isinstance(tool_use_id, str):
                continue
            pending = self._pending.pop(tool_use_id, None)
            if pending is None:
                continue
            self._record_tool_call(
                pending,
                is_error=bool(item.get("is_error", False)),
                denial_kind=denial,
                result_content=item.get("content"),
            )

    def _record_tool_call(
        self,
        pending: _PendingToolUse,
        *,
        is_error: bool,
        denial_kind: str | None,
        result_content: Any,
    ) -> None:
        self._tool_count += 1
        call = _ResolvedToolCall(
            step=self._tool_count,
            tool_name=pending.tool_name,
            summary=_resolve_tool_summary(
                pending,
                is_error=is_error,
                result_content=result_content,
            ),
            is_error=is_error,
            denial_kind=denial_kind,
            error_excerpt=_error_excerpt(result_content),
        )
        tool_line = _truncate_bytes(
            f"{call.step}. {call.summary}",
            max_bytes=TOOL_LINE_MAX_BYTES,
        )
        if call.step <= TOOL_SEQUENCE_HEAD:
            self._tool_head.append(tool_line)
        else:
            self._tool_tail.append(tool_line)

        if not call.is_error and call.denial_kind is None:
            return
        self._error_count += 1
        kind = "denial" if call.denial_kind is not None else "error"
        error_line = _truncate_bytes(
            f"- [step {call.step}] {call.tool_name} {kind}: {call.error_excerpt}",
            max_bytes=ERROR_LINE_MAX_BYTES,
        )
        if self._error_count <= ERROR_SAMPLE_HEAD:
            self._error_head.append(error_line)
        else:
            self._error_tail.append(error_line)

    def _tool_sequence_body(self) -> str:
        lines = [f"Total tool calls: {self._tool_count}"]
        if self._pending_overflow_count:
            lines.append(f"Pending tool pairs evicted: {self._pending_overflow_count}")
        if self._tool_count == 0:
            lines.append("(no tool calls)")
            return "\n".join(lines)
        if self._tool_count <= TOOL_SEQUENCE_HEAD + TOOL_SEQUENCE_TAIL:
            lines.extend(self._tool_head)
            lines.extend(self._tool_tail)
            return "\n".join(lines)

        omitted = self._tool_count - TOOL_SEQUENCE_HEAD - TOOL_SEQUENCE_TAIL
        lines.extend(self._tool_head)
        lines.append(f"{TRUNCATION_MARKER} ({omitted} tool calls omitted)")
        lines.extend(self._tool_tail)
        return "\n".join(lines)

    def _errors_body(self) -> str:
        lines = [f"Total errors/denials: {self._error_count}"]
        if self._error_count == 0:
            lines.append("(none)")
            return "\n".join(lines)
        if self._error_count <= ERROR_SAMPLE_HEAD + ERROR_SAMPLE_TAIL:
            lines.extend(self._error_head)
            lines.extend(self._error_tail)
            return "\n".join(lines)

        omitted = self._error_count - ERROR_SAMPLE_HEAD - ERROR_SAMPLE_TAIL
        lines.extend(self._error_head)
        lines.append(f"{TRUNCATION_MARKER} ({omitted} errors/denials omitted)")
        lines.extend(self._error_tail)
        return "\n".join(lines)


def build_transcript_view(parsed: ParsedSession, jsonl_path: Path) -> str:
    """Build a six-section view while retaining bounded streaming state."""
    reducer = _TranscriptViewReducer(parsed)
    extracted = consume_jsonl_records(
        jsonl_path,
        reducer.consume,
        raise_on_unicode_error=True,
    ).value
    sections = [
        _bounded_section("Task", extracted.task, _TASK_SECTION_MAX_BYTES),
        _bounded_section(
            "Agent Identity",
            _build_identity_body(parsed),
            _IDENTITY_SECTION_MAX_BYTES,
        ),
        _bounded_section(
            "Deterministic Facts",
            _build_facts_body(parsed),
            _FACTS_SECTION_MAX_BYTES,
        ),
        _bounded_section(
            "Tool Sequence",
            extracted.tool_sequence,
            _TOOL_SECTION_MAX_BYTES,
        ),
        _bounded_section(
            "Errors & Denials",
            extracted.errors,
            _ERRORS_SECTION_MAX_BYTES,
        ),
        _bounded_section(
            "Final Report",
            extracted.final_report,
            _FINAL_SECTION_MAX_BYTES,
        ),
    ]
    return _enforce_view_byte_gate(sections)


def _display(value: object) -> str:
    return str(value) if value is not None else "(none)"


def _truncate(text: str, *, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + TRUNCATION_MARKER


def _truncate_bytes(text: str, *, max_bytes: int, marker: str = TRUNCATION_MARKER) -> str:
    """Return a UTF-8-safe bounded prefix without encoding an unbounded string."""
    if max_bytes <= 0:
        return ""
    if len(text) <= max_bytes:
        encoded = text.encode("utf-8")
        if len(encoded) <= max_bytes:
            return text
    else:
        encoded = text[:max_bytes].encode("utf-8")
    marker_bytes = marker.encode("utf-8")
    if max_bytes <= len(marker_bytes):
        return marker_bytes[:max_bytes].decode("utf-8", errors="ignore")
    budget = max_bytes - len(marker_bytes)
    truncated = encoded[:budget].decode("utf-8", errors="ignore")
    return truncated + marker


def _format_tokens_k(tokens: int) -> str:
    return f"{round(tokens / TOKENS_PER_K)}K"


def _content_items(message: dict[str, Any]) -> Iterator[dict[str, Any]]:
    content = message.get("content")
    if isinstance(content, list):
        yield from (item for item in content if isinstance(item, dict))


def _message_text_parts(message: dict[str, Any]) -> Iterator[str]:
    content = message.get("content")
    if isinstance(content, str):
        yield content
        return
    if not isinstance(content, list):
        return
    if all(isinstance(item, str) for item in content):
        for item in content:
            yield item
        return
    for item in content:
        if not isinstance(item, dict) or item.get("type") != "text":
            continue
        text = item.get("text")
        if isinstance(text, str) and text:
            yield text


def _message_text_prefix(message: dict[str, Any], *, max_chars: int) -> str:
    parts: list[str] = []
    retained_chars = 0
    has_structured_parts = isinstance(message.get("content"), list) and any(
        isinstance(item, dict) and item.get("type") == "text"
        for item in message.get("content", [])
    )
    separator = "\n" if has_structured_parts else ""
    for text in _message_text_parts(message):
        if parts and separator:
            if retained_chars >= max_chars:
                break
            parts.append(separator)
            retained_chars += 1
        remaining = max_chars - retained_chars
        if remaining <= 0:
            break
        retained = text[:remaining]
        parts.append(retained)
        retained_chars += len(retained)
        if len(retained) < len(text):
            break
    return "".join(parts)


def _extract_exit_code(*, is_error: bool, result_content: Any) -> int:
    if not is_error:
        return 0
    text = result_content if isinstance(result_content, str) else ""
    match = _EXIT_CODE_RE.match(text)
    return int(match.group(1)) if match else 1


def _bounded_field(value: object, *, max_bytes: int = TOOL_LINE_MAX_BYTES // 2) -> str:
    return _truncate_bytes(str(value), max_bytes=max_bytes)


def _summarize_tool_use(tool_name: str, tool_input_value: Any) -> str:
    tool_input = tool_input_value if isinstance(tool_input_value, dict) else {}
    if tool_name == "Read":
        return f"Read {_bounded_field(tool_input.get('file_path', UNKNOWN_PATH))}"
    if tool_name == "Write":
        path = tool_input.get("file_path", UNKNOWN_PATH)
        content = tool_input.get("content")
        size = len(content.encode("utf-8")) if isinstance(content, str) else 0
        return f"Write {_bounded_field(path)} ({size} bytes)"
    if tool_name == "Edit":
        return f"Edit {_bounded_field(tool_input.get('file_path', UNKNOWN_PATH))}"
    if tool_name == "Bash":
        command = tool_input.get("command", "")
        command = command if isinstance(command, str) else ""
        return f"Bash: {command[:BASH_COMMAND_MAX_CHARS]}"
    return _bounded_field(tool_name)


def _resolve_tool_summary(
    pending: _PendingToolUse,
    *,
    is_error: bool,
    result_content: Any,
) -> str:
    if pending.tool_name != "Bash":
        return pending.summary
    exit_code = _extract_exit_code(
        is_error=is_error,
        result_content=result_content,
    )
    return f"{pending.summary} → exit {exit_code}"


def _error_excerpt(content: Any) -> str:
    if isinstance(content, str):
        text = content[: ERROR_EXCERPT_MAX_CHARS + 1]
    else:
        encoder = json.JSONEncoder(default=str)
        parts: list[str] = []
        retained = 0
        for part in encoder.iterencode(content):
            remaining = ERROR_EXCERPT_MAX_CHARS + 1 - retained
            if remaining <= 0:
                break
            excerpt = part[:remaining]
            parts.append(excerpt)
            retained += len(excerpt)
            if len(excerpt) < len(part):
                break
        text = "".join(parts)
    return _truncate(text, max_chars=ERROR_EXCERPT_MAX_CHARS)


def _build_identity_body(parsed: ParsedSession) -> str:
    lines = [
        f"- type: {_bounded_identity_value(parsed.name)}",
        f"- spawn_depth: {_bounded_identity_value(parsed.spawn_depth)}",
        f"- parent_session: {_bounded_identity_value(parsed.parent_session_id)}",
    ]
    return "\n".join(lines)


def _bounded_identity_value(value: object) -> str:
    return _truncate_bytes(
        _display(value),
        max_bytes=_IDENTITY_SECTION_MAX_BYTES // 2,
    )


def _build_facts_body(parsed: ParsedSession) -> str:
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
    return "\n".join(lines)


def _bounded_section(title: str, body: str, max_bytes: int) -> _Section:
    header_bytes = len(f"## {title}\n".encode())
    return _Section(
        title=title,
        body=_truncate_bytes(body, max_bytes=max(max_bytes - header_bytes, 0)),
    )


def _enforce_view_byte_gate(sections: list[_Section]) -> str:
    """Apply the final byte gate while preserving every section header."""
    if len(sections) != _NUM_SECTIONS:
        raise ValueError(f"expected {_NUM_SECTIONS} transcript-view sections")

    bounded = sections
    rendered = _SECTION_SEPARATOR.join(section.render() for section in bounded)
    while len(rendered.encode("utf-8")) > VIEW_MAX_BYTES:
        overage = len(rendered.encode("utf-8")) - VIEW_MAX_BYTES
        index = max(
            range(len(bounded)),
            key=lambda candidate: len(bounded[candidate].body.encode("utf-8")),
        )
        section = bounded[index]
        current_body_bytes = len(section.body.encode("utf-8"))
        target_body_bytes = max(current_body_bytes - overage, 0)
        bounded = [
            (
                _Section(
                    title=item.title,
                    body=_truncate_bytes(item.body, max_bytes=target_body_bytes),
                )
                if position == index
                else item
            )
            for position, item in enumerate(bounded)
        ]
        rendered = _SECTION_SEPARATOR.join(section.render() for section in bounded)

    assert len(rendered.encode("utf-8")) <= VIEW_MAX_BYTES
    return rendered
