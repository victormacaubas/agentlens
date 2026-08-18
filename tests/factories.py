import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

from agentlens.models.facts import FactSession, FactToolEvent
from agentlens.models.identity import NameSource, SessionIdentity, SessionKind, SourceRevision
from agentlens.models.session_facts import SessionFacts

DEFAULT_AGENT_ID = "agent-0000000000000000000000000000000000"
DEFAULT_PARENT_SESSION_ID = "parent-session-1111111111111111111111"
DEFAULT_TIMESTAMP = "2026-01-01T00:00:00.000Z"


def build_session_identity(
    *,
    session_id: str = "session-abc123",
    source_project: str = "project-one",
    session_kind: SessionKind = SessionKind.SUBAGENT,
    raw_session_id: str = "raw-abc123",
) -> SessionIdentity:
    return SessionIdentity(
        session_id=session_id,
        source_project=source_project,
        session_kind=session_kind,
        raw_session_id=raw_session_id,
    )


def build_source_revision(
    *,
    mtime_ns: int = 1_700_000_000_000_000_000,
    size: int = 256,
    content_hash: str = "content-hash-abc123",
) -> SourceRevision:
    return SourceRevision(mtime_ns=mtime_ns, size=size, content_hash=content_hash)


def build_fact_tool_event(
    *,
    session_id: str = "session-abc123",
    ordinal: int = 0,
    tool_name: str = "Read",
    input_fingerprint: str = "input-fingerprint-abc123",
    file_identity: str | None = None,
    timestamp: datetime = datetime(2026, 1, 1, tzinfo=UTC),
    is_error: bool = False,
    denial_kind: str | None = None,
    result_size: int | None = 42,
) -> FactToolEvent:
    return FactToolEvent(
        session_id=session_id,
        ordinal=ordinal,
        tool_name=tool_name,
        input_fingerprint=input_fingerprint,
        file_identity=file_identity,
        timestamp=timestamp,
        is_error=is_error,
        denial_kind=denial_kind,
        result_size=result_size,
    )


def build_fact_session(
    *,
    identity: SessionIdentity | None = None,
    revision: SourceRevision | None = None,
    agent_type: str = "implementer",
    name_source: NameSource = NameSource.META_JSON,
    task_description: str = "Implement the ingest pipeline",
    spawning_tool_use_id: str | None = "toolu_spawn",
    spawn_depth: int = 1,
    n_turns: int = 1,
    n_invocations: int = 1,
    n_reads: int = 0,
    n_edits: int = 0,
    n_writes: int = 0,
    n_bash: int = 0,
    n_distinct_files: int = 0,
    n_errors: int = 0,
    n_denials: int = 0,
    n_repeated_invocations: int = 0,
    duration_ms: int = 1_000,
    input_tokens: int = 100,
    output_tokens: int = 50,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
    unreadable_line_count: int = 0,
) -> FactSession:
    return FactSession(
        identity=identity if identity is not None else build_session_identity(),
        revision=revision if revision is not None else build_source_revision(),
        agent_type=agent_type,
        name_source=name_source,
        task_description=task_description,
        spawning_tool_use_id=spawning_tool_use_id,
        spawn_depth=spawn_depth,
        n_turns=n_turns,
        n_invocations=n_invocations,
        n_reads=n_reads,
        n_edits=n_edits,
        n_writes=n_writes,
        n_bash=n_bash,
        n_distinct_files=n_distinct_files,
        n_errors=n_errors,
        n_denials=n_denials,
        n_repeated_invocations=n_repeated_invocations,
        duration_ms=duration_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_creation_tokens=cache_creation_tokens,
        unreadable_line_count=unreadable_line_count,
    )


def build_session_facts(
    *,
    session: FactSession | None = None,
    tool_events: tuple[FactToolEvent, ...] = (),
) -> SessionFacts:
    return SessionFacts(
        session=session if session is not None else build_fact_session(),
        tool_events=tool_events,
    )


def build_root_fields(
    *,
    record_type: str,
    uuid: str,
    parent_uuid: str | None,
    agent_id: str = DEFAULT_AGENT_ID,
    parent_session_id: str = DEFAULT_PARENT_SESSION_ID,
    timestamp: str = DEFAULT_TIMESTAMP,
) -> dict[str, object]:
    """The record-root keys every transcript line carries, regardless of type."""
    return {
        "type": record_type,
        "uuid": uuid,
        "parentUuid": parent_uuid,
        "sessionId": parent_session_id,
        "agentId": agent_id,
        "timestamp": timestamp,
    }


def build_tool_use_block(
    *,
    tool_use_id: str = "toolu_1",
    name: str = "Read",
    input: Mapping[str, object] | None = None,
) -> dict[str, object]:
    return {
        "type": "tool_use",
        "id": tool_use_id,
        "name": name,
        "input": dict(input) if input is not None else {"file_path": "/workspace/example.txt"},
    }


def build_tool_result_block(
    *,
    tool_use_id: str = "toolu_1",
    content: str | list[dict[str, object]] = "ok",
    is_error: bool = False,
) -> dict[str, object]:
    """A tool result content block.

    ``is_error`` is present only when true, matching the observed transcript
    shape where the key is omitted on success rather than written as ``false``.
    """
    block: dict[str, object] = {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": content,
    }
    if is_error:
        block["is_error"] = True
    return block


def build_assistant_record(
    *,
    uuid: str = "uuid-assistant-1",
    parent_uuid: str | None = "uuid-user-0",
    message_id: str = "msg_1",
    content: Sequence[Mapping[str, object]] = (),
    stop_reason: str | None = None,
    usage: Mapping[str, object] | None = None,
    agent_id: str = DEFAULT_AGENT_ID,
    parent_session_id: str = DEFAULT_PARENT_SESSION_ID,
    timestamp: str = DEFAULT_TIMESTAMP,
) -> dict[str, object]:
    record: dict[str, object] = build_root_fields(
        record_type="assistant",
        uuid=uuid,
        parent_uuid=parent_uuid,
        agent_id=agent_id,
        parent_session_id=parent_session_id,
        timestamp=timestamp,
    )
    record["message"] = {
        "id": message_id,
        "role": "assistant",
        "content": [dict(block) for block in content],
        "stop_reason": stop_reason,
        "usage": dict(usage) if usage is not None else {"input_tokens": 10, "output_tokens": 5},
    }
    return record


def build_user_record(
    *,
    uuid: str = "uuid-user-1",
    parent_uuid: str | None = "uuid-assistant-1",
    content: Sequence[Mapping[str, object]] = (),
    agent_id: str = DEFAULT_AGENT_ID,
    parent_session_id: str = DEFAULT_PARENT_SESSION_ID,
    timestamp: str = DEFAULT_TIMESTAMP,
    tool_denial_kind: str | None = None,
) -> dict[str, object]:
    record: dict[str, object] = build_root_fields(
        record_type="user",
        uuid=uuid,
        parent_uuid=parent_uuid,
        agent_id=agent_id,
        parent_session_id=parent_session_id,
        timestamp=timestamp,
    )
    record["message"] = {"role": "user", "content": [dict(block) for block in content]}
    if tool_denial_kind is not None:
        record["toolDenialKind"] = tool_denial_kind
    return record


def build_tool_invocation_pair(
    *,
    tool_use_id: str = "toolu_1",
    tool_name: str = "Read",
    tool_input: Mapping[str, object] | None = None,
    result_content: str | list[dict[str, object]] = "file contents",
    is_error: bool = False,
    assistant_uuid: str = "uuid-assistant-1",
    parent_uuid: str | None = "uuid-user-0",
    result_uuid: str = "uuid-user-1",
    message_id: str = "msg_1",
) -> list[dict[str, object]]:
    """A tool invocation followed by its matching result, as two records.

    ``result_content`` accepts either shape observed in the wild: a plain
    string, or a list of ``{"type": "text", "text": ...}`` blocks.
    """
    assistant = build_assistant_record(
        uuid=assistant_uuid,
        parent_uuid=parent_uuid,
        message_id=message_id,
        content=[build_tool_use_block(tool_use_id=tool_use_id, name=tool_name, input=tool_input)],
        stop_reason="tool_use",
    )
    result = build_user_record(
        uuid=result_uuid,
        parent_uuid=assistant_uuid,
        content=[
            build_tool_result_block(
                tool_use_id=tool_use_id, content=result_content, is_error=is_error
            )
        ],
    )
    return [assistant, result]


def build_denied_invocation(
    *,
    tool_use_id: str = "toolu_denied",
    tool_name: str = "Bash",
    denial_kind: str = "permission-rule",
    assistant_uuid: str = "uuid-denied-assistant",
    parent_uuid: str | None = "uuid-user-0",
    result_uuid: str = "uuid-denied-result",
    message_id: str = "msg_denied",
) -> list[dict[str, object]]:
    """An invocation whose result was a permission denial, ``toolDenialKind`` and all.

    ``toolDenialKind`` sits at the result record's root, a sibling of
    ``message``, not inside the ``tool_result`` content item.
    """
    assistant = build_assistant_record(
        uuid=assistant_uuid,
        parent_uuid=parent_uuid,
        message_id=message_id,
        content=[build_tool_use_block(tool_use_id=tool_use_id, name=tool_name)],
        stop_reason="tool_use",
    )
    result = build_user_record(
        uuid=result_uuid,
        parent_uuid=assistant_uuid,
        content=[
            build_tool_result_block(
                tool_use_id=tool_use_id, content="Permission denied", is_error=True
            )
        ],
        tool_denial_kind=denial_kind,
    )
    return [assistant, result]


def build_unmatched_invocation(
    *,
    tool_use_id: str = "toolu_unmatched",
    tool_name: str = "Bash",
    uuid: str = "uuid-unmatched",
    parent_uuid: str | None = None,
    message_id: str = "msg_unmatched",
) -> dict[str, object]:
    """A tool invocation that never receives a result, as at end of file."""
    return build_assistant_record(
        uuid=uuid,
        parent_uuid=parent_uuid,
        message_id=message_id,
        content=[build_tool_use_block(tool_use_id=tool_use_id, name=tool_name)],
        stop_reason="tool_use",
    )


def build_fragmented_turn(
    *,
    message_id: str = "msg_fragmented",
    tool_use_id: str = "toolu_fragment",
    tool_name: str = "Read",
    parent_uuid: str | None = "uuid-user-0",
    interior_usage: Mapping[str, object] | None = None,
    final_usage: Mapping[str, object] | None = None,
) -> list[dict[str, object]]:
    """One turn written as several assistant records sharing one ``message.id``.

    Models the observed shape where a response with thinking plus a tool call
    is written as one record per content block. Interior fragments carry
    ``stop_reason: null`` and re-emit ``usage`` cumulatively; the same
    ``output_tokens`` figure repeats across them, then jumps on the trailing
    fragment, which alone carries the resolved ``stop_reason``. A caller that
    counts turns per assistant record, or sums ``usage`` across fragments,
    overcounts both: this fixture is one turn, and its true token totals are
    ``final_usage``, not the sum of all three records' figures.
    """
    interior = (
        dict(interior_usage)
        if interior_usage is not None
        else {"input_tokens": 500, "output_tokens": 40, "cache_read_input_tokens": 300}
    )
    final = (
        dict(final_usage)
        if final_usage is not None
        else {"input_tokens": 500, "output_tokens": 120, "cache_read_input_tokens": 300}
    )
    thinking_fragment = build_assistant_record(
        uuid="uuid-fragment-thinking",
        parent_uuid=parent_uuid,
        message_id=message_id,
        content=[{"type": "thinking", "thinking": "Considering the request."}],
        stop_reason=None,
        usage=interior,
    )
    text_fragment = build_assistant_record(
        uuid="uuid-fragment-text",
        parent_uuid="uuid-fragment-thinking",
        message_id=message_id,
        content=[{"type": "text", "text": "I will read the file."}],
        stop_reason=None,
        usage=interior,
    )
    tool_use_fragment = build_assistant_record(
        uuid="uuid-fragment-tool-use",
        parent_uuid="uuid-fragment-text",
        message_id=message_id,
        content=[build_tool_use_block(tool_use_id=tool_use_id, name=tool_name)],
        stop_reason="tool_use",
        usage=final,
    )
    return [thinking_fragment, text_fragment, tool_use_fragment]


def build_unparseable_line() -> str:
    """A transcript line that fails JSON parsing outright."""
    return "{not valid json"


def build_sidecar(
    *,
    agent_type: str = "implementer",
    description: str = "Implement the ingest pipeline",
    tool_use_id: str = "toolu_spawn",
    spawn_depth: int = 1,
    parent_agent_id: str | None = None,
    model: str | None = None,
) -> dict[str, object]:
    """A ``.meta.json`` sidecar. ``parentAgentId`` and ``model`` are optional keys.

    Both are omitted entirely when not supplied, matching the observed shape
    where an optional key is absent rather than present with a null value.
    """
    sidecar: dict[str, object] = {
        "agentType": agent_type,
        "description": description,
        "toolUseId": tool_use_id,
        "spawnDepth": spawn_depth,
    }
    if parent_agent_id is not None:
        sidecar["parentAgentId"] = parent_agent_id
    if model is not None:
        sidecar["model"] = model
    return sidecar


def build_transcript_path(
    base: Path,
    *,
    project: str = "project-one",
    parent_session_id: str = DEFAULT_PARENT_SESSION_ID,
    raw_session_id: str = "0000000000000000000000000000000000",
) -> Path:
    """Return where a subagent transcript would sit under ``base``.

    Models ``.claude/projects/<project>/<parent-session-uuid>/subagents/
    agent-<agentId>.jsonl``. Does not create anything; callers write the
    transcript and any sidecar themselves at the returned path.
    """
    return (
        base
        / ".claude"
        / "projects"
        / project
        / parent_session_id
        / "subagents"
        / f"agent-{raw_session_id}.jsonl"
    )


def build_transcript_text(records: Sequence[Mapping[str, object]]) -> str:
    """Serialize records as newline-delimited JSON, one record per line."""
    lines = [json.dumps(record) for record in records]
    return "\n".join(lines) + "\n" if lines else ""
