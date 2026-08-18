from datetime import datetime
from typing import cast

from agentlens.models.facts import FactSession, FactToolEvent
from agentlens.models.identity import NameSource, SessionIdentity, SessionKind, SourceRevision

SqliteRow = tuple[object, ...]


def fact_session_to_row(session: FactSession) -> SqliteRow:
    """Return ``session``'s field values in the column order of ``fact_session``."""
    return (
        session.identity.session_id,
        session.identity.source_project,
        session.identity.session_kind,
        session.identity.raw_session_id,
        session.revision.mtime_ns,
        session.revision.size,
        session.revision.content_hash,
        session.agent_type,
        session.name_source,
        session.task_description,
        session.spawning_tool_use_id,
        session.spawn_depth,
        session.n_turns,
        session.n_invocations,
        session.n_reads,
        session.n_edits,
        session.n_writes,
        session.n_bash,
        session.n_distinct_files,
        session.n_errors,
        session.n_denials,
        session.n_repeated_invocations,
        session.duration_ms,
        session.input_tokens,
        session.output_tokens,
        session.cache_read_tokens,
        session.cache_creation_tokens,
        session.unreadable_line_count,
    )


def row_to_fact_session(row: SqliteRow) -> FactSession:
    """Rebuild a ``FactSession`` from a row selected in ``fact_session``'s column order."""
    (
        session_id,
        source_project,
        session_kind,
        raw_session_id,
        revision_mtime_ns,
        revision_size,
        revision_content_hash,
        agent_type,
        name_source,
        task_description,
        spawning_tool_use_id,
        spawn_depth,
        n_turns,
        n_invocations,
        n_reads,
        n_edits,
        n_writes,
        n_bash,
        n_distinct_files,
        n_errors,
        n_denials,
        n_repeated_invocations,
        duration_ms,
        input_tokens,
        output_tokens,
        cache_read_tokens,
        cache_creation_tokens,
        unreadable_line_count,
    ) = row
    return FactSession(
        identity=SessionIdentity(
            session_id=cast(str, session_id),
            source_project=cast(str, source_project),
            session_kind=SessionKind(cast(str, session_kind)),
            raw_session_id=cast(str, raw_session_id),
        ),
        revision=SourceRevision(
            mtime_ns=cast(int, revision_mtime_ns),
            size=cast(int, revision_size),
            content_hash=cast(str, revision_content_hash),
        ),
        agent_type=cast(str, agent_type),
        name_source=NameSource(cast(str, name_source)),
        task_description=cast(str, task_description),
        spawning_tool_use_id=cast(str | None, spawning_tool_use_id),
        spawn_depth=cast(int, spawn_depth),
        n_turns=cast(int, n_turns),
        n_invocations=cast(int, n_invocations),
        n_reads=cast(int, n_reads),
        n_edits=cast(int, n_edits),
        n_writes=cast(int, n_writes),
        n_bash=cast(int, n_bash),
        n_distinct_files=cast(int, n_distinct_files),
        n_errors=cast(int, n_errors),
        n_denials=cast(int, n_denials),
        n_repeated_invocations=cast(int, n_repeated_invocations),
        duration_ms=cast(int, duration_ms),
        input_tokens=cast(int, input_tokens),
        output_tokens=cast(int, output_tokens),
        cache_read_tokens=cast(int, cache_read_tokens),
        cache_creation_tokens=cast(int, cache_creation_tokens),
        unreadable_line_count=cast(int, unreadable_line_count),
    )


def fact_tool_event_to_row(event: FactToolEvent) -> SqliteRow:
    """Return ``event``'s field values in the column order of ``fact_tool_event``."""
    return (
        event.session_id,
        event.ordinal,
        event.tool_name,
        event.input_fingerprint,
        event.file_identity,
        event.timestamp.isoformat(),
        int(event.is_error),
        event.denial_kind,
        event.result_size,
    )


def row_to_fact_tool_event(row: SqliteRow) -> FactToolEvent:
    """Rebuild a ``FactToolEvent`` from a row selected in ``fact_tool_event``'s column order."""
    (
        session_id,
        ordinal,
        tool_name,
        input_fingerprint,
        file_identity,
        timestamp,
        is_error,
        denial_kind,
        result_size,
    ) = row
    return FactToolEvent(
        session_id=cast(str, session_id),
        ordinal=cast(int, ordinal),
        tool_name=cast(str, tool_name),
        input_fingerprint=cast(str, input_fingerprint),
        file_identity=cast("str | None", file_identity),
        timestamp=datetime.fromisoformat(cast(str, timestamp)),
        is_error=bool(is_error),
        denial_kind=cast("str | None", denial_kind),
        result_size=cast("int | None", result_size),
    )
