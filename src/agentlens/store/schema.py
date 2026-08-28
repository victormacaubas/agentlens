"""DDL for the fact, dimension, and bridge tables the store persists.

``fact_session`` holds one row per spawn, keyed on the qualified session key.
``fact_tool_event`` holds one row per tool invocation, keyed on the session
together with its ordinal position within that session. ``dim_agent`` holds
one row per versioned, content-addressed agent definition. ``bridge_session_skill``
holds one row per qualified session and skill name, keyed on that pair.
``fact_verdict`` holds one row per scored identity, keyed on the session
together with the judge-input hash, the rubric version, and the resolved
judge model.

Each table states its column order exactly once, in the declaration tuples
below. The DDL here and the column lists in ``operations`` are generated from
those declarations, so the two cannot drift apart.
"""

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class _Column:
    """One column of a fact table as the DDL declares it.

    ``nullable`` cannot be inferred from ``declared_type``: six of the
    forty-five columns admit NULL and the rest carry an explicit
    ``NOT NULL``.

    ``inline_primary_key`` reproduces ``fact_session``'s bare
    ``session_id TEXT PRIMARY KEY``, which SQLite reports as nullable and,
    for a ``TEXT`` key, does not enforce. ``fact_tool_event`` instead names
    its key in a table-level clause, and the difference between the two is
    observable through ``PRAGMA table_info``.
    """

    name: str
    declared_type: str
    nullable: bool = False
    inline_primary_key: bool = False


_FACT_SESSION_COLUMNS = (
    _Column("session_id", "TEXT", nullable=True, inline_primary_key=True),
    _Column("source_project", "TEXT"),
    _Column("session_kind", "TEXT"),
    _Column("raw_session_id", "TEXT"),
    _Column("revision_mtime_ns", "INTEGER"),
    _Column("revision_size", "INTEGER"),
    _Column("revision_content_hash", "TEXT"),
    _Column("agent_type", "TEXT"),
    _Column("name_source", "TEXT"),
    _Column("task_description", "TEXT"),
    _Column("spawning_tool_use_id", "TEXT", nullable=True),
    _Column("spawn_depth", "INTEGER"),
    _Column("n_turns", "INTEGER"),
    _Column("n_invocations", "INTEGER"),
    _Column("n_reads", "INTEGER"),
    _Column("n_edits", "INTEGER"),
    _Column("n_writes", "INTEGER"),
    _Column("n_bash", "INTEGER"),
    _Column("n_distinct_files", "INTEGER"),
    _Column("n_errors", "INTEGER"),
    _Column("n_denials", "INTEGER"),
    _Column("n_repeated_invocations", "INTEGER"),
    _Column("duration_ms", "INTEGER"),
    _Column("input_tokens", "INTEGER"),
    _Column("output_tokens", "INTEGER"),
    _Column("cache_read_tokens", "INTEGER"),
    _Column("cache_creation_tokens", "INTEGER"),
    _Column("unreadable_line_count", "INTEGER"),
    _Column("agent_id", "TEXT"),
    _Column("agent_definition_id", "TEXT", nullable=True),
    _Column("parent_session_id", "TEXT", nullable=True),
    _Column("started_at", "TEXT"),
    _Column("task_prompt_len", "INTEGER"),
    _Column("n_skills_fired", "INTEGER"),
    _Column("derivation_fingerprint", "TEXT"),
    _Column("derivation_observed_mtime_ns", "INTEGER"),
)

_FACT_TOOL_EVENT_COLUMNS = (
    _Column("session_id", "TEXT"),
    _Column("ordinal", "INTEGER"),
    _Column("tool_name", "TEXT"),
    _Column("input_fingerprint", "TEXT"),
    _Column("file_identity", "TEXT", nullable=True),
    _Column("timestamp", "TEXT"),
    _Column("is_error", "INTEGER"),
    _Column("denial_kind", "TEXT", nullable=True),
    _Column("result_size", "INTEGER", nullable=True),
)

_DIM_AGENT_COLUMNS = (
    _Column("agent_definition_id", "TEXT", nullable=True, inline_primary_key=True),
    _Column("scope", "TEXT"),
    _Column("source_project", "TEXT", nullable=True),
    _Column("name", "TEXT"),
    _Column("model", "TEXT", nullable=True),
    _Column("effort", "TEXT", nullable=True),
    _Column("tools", "TEXT"),
    _Column("skills", "TEXT"),
    _Column("revision_mtime_ns", "INTEGER"),
    _Column("revision_size", "INTEGER"),
    _Column("revision_content_hash", "TEXT"),
)

_BRIDGE_SESSION_SKILL_COLUMNS = (
    _Column("session_id", "TEXT"),
    _Column("skill_name", "TEXT"),
    _Column("declared", "TEXT"),
    _Column("available", "TEXT"),
    _Column("fired", "INTEGER"),
)

_FACT_VERDICT_COLUMNS = (
    _Column("session_id", "TEXT"),
    _Column("judge_input_hash", "TEXT"),
    _Column("rubric_version", "TEXT"),
    _Column("judge_model", "TEXT"),
    _Column("overall_score", "INTEGER"),
    _Column("task_completion_score", "INTEGER"),
    _Column("honesty_score", "INTEGER"),
    _Column("efficiency_score", "INTEGER"),
    _Column("scope_adherence_score", "INTEGER"),
    _Column("dimension_evidence", "TEXT"),
    _Column("suggested_fixes", "TEXT"),
    _Column("provenance", "TEXT"),
    _Column("judge_cost_usd", "REAL"),
    _Column("judge_input_tokens", "INTEGER"),
    _Column("judge_output_tokens", "INTEGER"),
    _Column("scored_at", "TEXT"),
)

FACT_SESSION_COLUMN_NAMES = tuple(column.name for column in _FACT_SESSION_COLUMNS)
FACT_TOOL_EVENT_COLUMN_NAMES = tuple(column.name for column in _FACT_TOOL_EVENT_COLUMNS)
DIM_AGENT_COLUMN_NAMES = tuple(column.name for column in _DIM_AGENT_COLUMNS)
BRIDGE_SESSION_SKILL_COLUMN_NAMES = tuple(column.name for column in _BRIDGE_SESSION_SKILL_COLUMNS)
FACT_VERDICT_COLUMN_NAMES = tuple(column.name for column in _FACT_VERDICT_COLUMNS)


def _column_ddl(column: _Column) -> str:
    parts = [column.name, column.declared_type]
    if not column.nullable:
        parts.append("NOT NULL")
    if column.inline_primary_key:
        parts.append("PRIMARY KEY")
    return " ".join(parts)


def _create_table_sql(
    table: str,
    columns: tuple[_Column, ...],
    *,
    composite_primary_key: tuple[str, ...] = (),
) -> str:
    declarations = [_column_ddl(column) for column in columns]
    if composite_primary_key:
        declarations.append(f"PRIMARY KEY ({', '.join(composite_primary_key)})")
    body = ",\n    ".join(declarations)
    return f"\nCREATE TABLE IF NOT EXISTS {table} (\n    {body}\n)\n"


CREATE_FACT_SESSION_SQL = _create_table_sql("fact_session", _FACT_SESSION_COLUMNS)

CREATE_FACT_TOOL_EVENT_SQL = _create_table_sql(
    "fact_tool_event",
    _FACT_TOOL_EVENT_COLUMNS,
    composite_primary_key=("session_id", "ordinal"),
)

CREATE_DIM_AGENT_SQL = _create_table_sql("dim_agent", _DIM_AGENT_COLUMNS)

CREATE_BRIDGE_SESSION_SKILL_SQL = _create_table_sql(
    "bridge_session_skill",
    _BRIDGE_SESSION_SKILL_COLUMNS,
    composite_primary_key=("session_id", "skill_name"),
)

CREATE_FACT_VERDICT_SQL = _create_table_sql(
    "fact_verdict",
    _FACT_VERDICT_COLUMNS,
    composite_primary_key=("session_id", "judge_input_hash", "rubric_version", "judge_model"),
)


def ensure_schema(connection: sqlite3.Connection) -> None:
    """Create every fact, dimension, and bridge table if it does not already exist."""
    connection.execute(CREATE_FACT_SESSION_SQL)
    connection.execute(CREATE_FACT_TOOL_EVENT_SQL)
    connection.execute(CREATE_DIM_AGENT_SQL)
    connection.execute(CREATE_BRIDGE_SESSION_SKILL_SQL)
    connection.execute(CREATE_FACT_VERDICT_SQL)
    connection.commit()
