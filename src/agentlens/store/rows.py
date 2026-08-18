import json
import sqlite3
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import cast

from agentlens.models.agent_definitions import (
    AgentDefinition,
    AgentDefinitionConfig,
    DefinitionScope,
)
from agentlens.models.facts import FactSession, FactToolEvent
from agentlens.models.identity import NameSource, SessionIdentity, SessionKind, SourceRevision
from agentlens.models.skill_signals import KnownState, SessionSkillSignal
from agentlens.store.schema import (
    BRIDGE_SESSION_SKILL_COLUMN_NAMES,
    DIM_AGENT_COLUMN_NAMES,
    FACT_SESSION_COLUMN_NAMES,
    FACT_TOOL_EVENT_COLUMN_NAMES,
)

SqliteRow = tuple[object, ...]

_FACT_SESSION_VALUE_EXTRACTORS: Mapping[str, Callable[[FactSession], object]] = {
    "session_id": lambda session: session.identity.session_id,
    "source_project": lambda session: session.identity.source_project,
    "session_kind": lambda session: session.identity.session_kind.value,
    "raw_session_id": lambda session: session.identity.raw_session_id,
    "revision_mtime_ns": lambda session: session.revision.mtime_ns,
    "revision_size": lambda session: session.revision.size,
    "revision_content_hash": lambda session: session.revision.content_hash,
    "agent_type": lambda session: session.agent_type,
    "name_source": lambda session: session.name_source.value,
    "task_description": lambda session: session.task_description,
    "spawning_tool_use_id": lambda session: session.spawning_tool_use_id,
    "spawn_depth": lambda session: session.spawn_depth,
    "n_turns": lambda session: session.n_turns,
    "n_invocations": lambda session: session.n_invocations,
    "n_reads": lambda session: session.n_reads,
    "n_edits": lambda session: session.n_edits,
    "n_writes": lambda session: session.n_writes,
    "n_bash": lambda session: session.n_bash,
    "n_distinct_files": lambda session: session.n_distinct_files,
    "n_errors": lambda session: session.n_errors,
    "n_denials": lambda session: session.n_denials,
    "n_repeated_invocations": lambda session: session.n_repeated_invocations,
    "duration_ms": lambda session: session.duration_ms,
    "input_tokens": lambda session: session.input_tokens,
    "output_tokens": lambda session: session.output_tokens,
    "cache_read_tokens": lambda session: session.cache_read_tokens,
    "cache_creation_tokens": lambda session: session.cache_creation_tokens,
    "unreadable_line_count": lambda session: session.unreadable_line_count,
    "agent_id": lambda session: session.agent_id,
    "agent_definition_id": lambda session: session.agent_definition_id,
    "parent_session_id": lambda session: session.parent_session_id,
    "started_at": lambda session: session.started_at.isoformat(),
    "task_prompt_len": lambda session: session.task_prompt_len,
    "n_skills_fired": lambda session: session.n_skills_fired,
    "derivation_fingerprint": lambda session: session.derivation_fingerprint,
    "derivation_observed_mtime_ns": lambda session: session.derivation_observed_mtime_ns,
}

_FACT_TOOL_EVENT_VALUE_EXTRACTORS: Mapping[str, Callable[[FactToolEvent], object]] = {
    "session_id": lambda event: event.session_id,
    "ordinal": lambda event: event.ordinal,
    "tool_name": lambda event: event.tool_name,
    "input_fingerprint": lambda event: event.input_fingerprint,
    "file_identity": lambda event: event.file_identity,
    "timestamp": lambda event: event.timestamp.isoformat(),
    "is_error": lambda event: int(event.is_error),
    "denial_kind": lambda event: event.denial_kind,
    "result_size": lambda event: event.result_size,
}


def fact_session_to_row(session: FactSession) -> SqliteRow:
    """Return ``session``'s field values in the column order of ``fact_session``."""
    return tuple(
        _FACT_SESSION_VALUE_EXTRACTORS[name](session) for name in FACT_SESSION_COLUMN_NAMES
    )


def row_to_fact_session(row: sqlite3.Row) -> FactSession:
    """Rebuild a ``FactSession`` from a ``fact_session`` row, read by column name."""
    return FactSession(
        identity=SessionIdentity(
            session_id=cast(str, row["session_id"]),
            source_project=cast(str, row["source_project"]),
            session_kind=SessionKind(cast(str, row["session_kind"])),
            raw_session_id=cast(str, row["raw_session_id"]),
        ),
        revision=SourceRevision(
            mtime_ns=cast(int, row["revision_mtime_ns"]),
            size=cast(int, row["revision_size"]),
            content_hash=cast(str, row["revision_content_hash"]),
        ),
        agent_type=cast(str, row["agent_type"]),
        name_source=NameSource(cast(str, row["name_source"])),
        task_description=cast(str, row["task_description"]),
        spawning_tool_use_id=cast("str | None", row["spawning_tool_use_id"]),
        spawn_depth=cast(int, row["spawn_depth"]),
        n_turns=cast(int, row["n_turns"]),
        n_invocations=cast(int, row["n_invocations"]),
        n_reads=cast(int, row["n_reads"]),
        n_edits=cast(int, row["n_edits"]),
        n_writes=cast(int, row["n_writes"]),
        n_bash=cast(int, row["n_bash"]),
        n_distinct_files=cast(int, row["n_distinct_files"]),
        n_errors=cast(int, row["n_errors"]),
        n_denials=cast(int, row["n_denials"]),
        n_repeated_invocations=cast(int, row["n_repeated_invocations"]),
        duration_ms=cast(int, row["duration_ms"]),
        input_tokens=cast(int, row["input_tokens"]),
        output_tokens=cast(int, row["output_tokens"]),
        cache_read_tokens=cast(int, row["cache_read_tokens"]),
        cache_creation_tokens=cast(int, row["cache_creation_tokens"]),
        unreadable_line_count=cast(int, row["unreadable_line_count"]),
        agent_id=cast(str, row["agent_id"]),
        agent_definition_id=cast("str | None", row["agent_definition_id"]),
        parent_session_id=cast("str | None", row["parent_session_id"]),
        started_at=datetime.fromisoformat(cast(str, row["started_at"])),
        task_prompt_len=cast(int, row["task_prompt_len"]),
        n_skills_fired=cast(int, row["n_skills_fired"]),
        derivation_fingerprint=cast(str, row["derivation_fingerprint"]),
        derivation_observed_mtime_ns=cast(int, row["derivation_observed_mtime_ns"]),
    )


def fact_tool_event_to_row(event: FactToolEvent) -> SqliteRow:
    """Return ``event``'s field values in the column order of ``fact_tool_event``."""
    return tuple(
        _FACT_TOOL_EVENT_VALUE_EXTRACTORS[name](event) for name in FACT_TOOL_EVENT_COLUMN_NAMES
    )


_DIM_AGENT_VALUE_EXTRACTORS: Mapping[str, Callable[[AgentDefinition], object]] = {
    "agent_definition_id": lambda definition: definition.agent_definition_id,
    "scope": lambda definition: definition.scope.value,
    "source_project": lambda definition: definition.source_project,
    "name": lambda definition: definition.config.name,
    "model": lambda definition: definition.config.model,
    "effort": lambda definition: definition.config.effort,
    "tools": lambda definition: json.dumps(list(definition.config.tools)),
    "skills": lambda definition: json.dumps(list(definition.config.skills)),
    "revision_mtime_ns": lambda definition: definition.revision.mtime_ns,
    "revision_size": lambda definition: definition.revision.size,
    "revision_content_hash": lambda definition: definition.revision.content_hash,
}


def agent_definition_to_row(definition: AgentDefinition) -> SqliteRow:
    """Return ``definition``'s field values in the column order of ``dim_agent``."""
    return tuple(_DIM_AGENT_VALUE_EXTRACTORS[name](definition) for name in DIM_AGENT_COLUMN_NAMES)


def row_to_agent_definition(row: sqlite3.Row) -> AgentDefinition:
    """Rebuild an ``AgentDefinition`` from a ``dim_agent`` row, read by column name."""
    return AgentDefinition(
        agent_definition_id=cast(str, row["agent_definition_id"]),
        scope=DefinitionScope(cast(str, row["scope"])),
        source_project=cast("str | None", row["source_project"]),
        config=AgentDefinitionConfig(
            name=cast(str, row["name"]),
            model=cast("str | None", row["model"]),
            effort=cast("str | None", row["effort"]),
            tools=tuple(json.loads(cast(str, row["tools"]))),
            skills=tuple(json.loads(cast(str, row["skills"]))),
        ),
        revision=SourceRevision(
            mtime_ns=cast(int, row["revision_mtime_ns"]),
            size=cast(int, row["revision_size"]),
            content_hash=cast(str, row["revision_content_hash"]),
        ),
    )


_BRIDGE_SESSION_SKILL_VALUE_EXTRACTORS: Mapping[str, Callable[[SessionSkillSignal], object]] = {
    "session_id": lambda signal: signal.session_id,
    "skill_name": lambda signal: signal.skill_name,
    "declared": lambda signal: signal.declared.value,
    "available": lambda signal: signal.available.value,
    "fired": lambda signal: int(signal.fired),
}


def session_skill_signal_to_row(signal: SessionSkillSignal) -> SqliteRow:
    """Return ``signal``'s field values in the column order of ``bridge_session_skill``."""
    return tuple(
        _BRIDGE_SESSION_SKILL_VALUE_EXTRACTORS[name](signal)
        for name in BRIDGE_SESSION_SKILL_COLUMN_NAMES
    )


def row_to_session_skill_signal(row: sqlite3.Row) -> SessionSkillSignal:
    """Rebuild a ``SessionSkillSignal`` from a ``bridge_session_skill`` row, read by column name."""
    return SessionSkillSignal(
        session_id=cast(str, row["session_id"]),
        skill_name=cast(str, row["skill_name"]),
        declared=KnownState(cast(str, row["declared"])),
        available=KnownState(cast(str, row["available"])),
        fired=bool(row["fired"]),
    )


def row_to_fact_tool_event(row: sqlite3.Row) -> FactToolEvent:
    """Rebuild a ``FactToolEvent`` from a ``fact_tool_event`` row, read by column name."""
    return FactToolEvent(
        session_id=cast(str, row["session_id"]),
        ordinal=cast(int, row["ordinal"]),
        tool_name=cast(str, row["tool_name"]),
        input_fingerprint=cast(str, row["input_fingerprint"]),
        file_identity=cast("str | None", row["file_identity"]),
        timestamp=datetime.fromisoformat(cast(str, row["timestamp"])),
        is_error=bool(row["is_error"]),
        denial_kind=cast("str | None", row["denial_kind"]),
        result_size=cast("int | None", row["result_size"]),
    )
