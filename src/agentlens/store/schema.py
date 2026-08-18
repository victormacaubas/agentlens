"""DDL for the two fact tables the store persists.

``fact_session`` holds one row per spawn, keyed on the qualified session key.
``fact_tool_event`` holds one row per tool invocation, keyed on the session
together with its ordinal position within that session.

Each table states its column order exactly once, in the declaration tuples
below. The DDL here and the column lists in ``operations`` are generated from
those declarations, so the two cannot drift apart.
"""

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class _Column:
    """One column of a fact table as the DDL declares it.

    ``nullable`` cannot be inferred from ``declared_type``: four of the
    thirty-seven columns admit NULL and the rest carry an explicit
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

FACT_SESSION_COLUMN_NAMES = tuple(column.name for column in _FACT_SESSION_COLUMNS)
FACT_TOOL_EVENT_COLUMN_NAMES = tuple(column.name for column in _FACT_TOOL_EVENT_COLUMNS)


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


def ensure_schema(connection: sqlite3.Connection) -> None:
    """Create both fact tables if they do not already exist."""
    connection.execute(CREATE_FACT_SESSION_SQL)
    connection.execute(CREATE_FACT_TOOL_EVENT_SQL)
    connection.commit()
