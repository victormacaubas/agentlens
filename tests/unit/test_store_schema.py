"""The emitted schema, pinned column by column as SQLite reports it.

These assertions are transcribed from the DDL as it stands, so that a later
change to how the DDL is expressed is compared against the schema that shipped
rather than against itself. A diff here means the on-disk schema moved.
"""

import sqlite3
from pathlib import Path
from typing import NamedTuple

from agentlens.store.schema import ensure_schema

_TABLE_INFO_SQL = """
SELECT name, type, "notnull", pk
FROM pragma_table_info(?)
ORDER BY cid
"""


class PinnedColumn(NamedTuple):
    """One column as ``PRAGMA table_info`` reports it.

    ``primary_key_position`` is 1-based over the key's columns and 0 for a
    column outside the key, so a composite key reports 1 then 2 rather than a
    pair of booleans.

    ``notnull`` reflects only an explicit ``NOT NULL`` in the DDL. SQLite
    reports 0 for a bare ``TEXT PRIMARY KEY``, which it also declines to
    enforce.
    """

    name: str
    declared_type: str
    notnull: int
    primary_key_position: int


_FACT_SESSION_COLUMNS = (
    PinnedColumn("session_id", "TEXT", notnull=0, primary_key_position=1),
    PinnedColumn("source_project", "TEXT", notnull=1, primary_key_position=0),
    PinnedColumn("session_kind", "TEXT", notnull=1, primary_key_position=0),
    PinnedColumn("raw_session_id", "TEXT", notnull=1, primary_key_position=0),
    PinnedColumn("revision_mtime_ns", "INTEGER", notnull=1, primary_key_position=0),
    PinnedColumn("revision_size", "INTEGER", notnull=1, primary_key_position=0),
    PinnedColumn("revision_content_hash", "TEXT", notnull=1, primary_key_position=0),
    PinnedColumn("agent_type", "TEXT", notnull=1, primary_key_position=0),
    PinnedColumn("name_source", "TEXT", notnull=1, primary_key_position=0),
    PinnedColumn("task_description", "TEXT", notnull=1, primary_key_position=0),
    PinnedColumn("spawning_tool_use_id", "TEXT", notnull=0, primary_key_position=0),
    PinnedColumn("spawn_depth", "INTEGER", notnull=1, primary_key_position=0),
    PinnedColumn("n_turns", "INTEGER", notnull=1, primary_key_position=0),
    PinnedColumn("n_invocations", "INTEGER", notnull=1, primary_key_position=0),
    PinnedColumn("n_reads", "INTEGER", notnull=1, primary_key_position=0),
    PinnedColumn("n_edits", "INTEGER", notnull=1, primary_key_position=0),
    PinnedColumn("n_writes", "INTEGER", notnull=1, primary_key_position=0),
    PinnedColumn("n_bash", "INTEGER", notnull=1, primary_key_position=0),
    PinnedColumn("n_distinct_files", "INTEGER", notnull=1, primary_key_position=0),
    PinnedColumn("n_errors", "INTEGER", notnull=1, primary_key_position=0),
    PinnedColumn("n_denials", "INTEGER", notnull=1, primary_key_position=0),
    PinnedColumn("n_repeated_invocations", "INTEGER", notnull=1, primary_key_position=0),
    PinnedColumn("duration_ms", "INTEGER", notnull=1, primary_key_position=0),
    PinnedColumn("input_tokens", "INTEGER", notnull=1, primary_key_position=0),
    PinnedColumn("output_tokens", "INTEGER", notnull=1, primary_key_position=0),
    PinnedColumn("cache_read_tokens", "INTEGER", notnull=1, primary_key_position=0),
    PinnedColumn("cache_creation_tokens", "INTEGER", notnull=1, primary_key_position=0),
    PinnedColumn("unreadable_line_count", "INTEGER", notnull=1, primary_key_position=0),
    PinnedColumn("agent_id", "TEXT", notnull=1, primary_key_position=0),
    PinnedColumn("agent_definition_id", "TEXT", notnull=0, primary_key_position=0),
    PinnedColumn("parent_session_id", "TEXT", notnull=0, primary_key_position=0),
    PinnedColumn("started_at", "TEXT", notnull=1, primary_key_position=0),
    PinnedColumn("task_prompt_len", "INTEGER", notnull=1, primary_key_position=0),
    PinnedColumn("n_skills_fired", "INTEGER", notnull=1, primary_key_position=0),
    PinnedColumn("derivation_fingerprint", "TEXT", notnull=1, primary_key_position=0),
    PinnedColumn("derivation_observed_mtime_ns", "INTEGER", notnull=1, primary_key_position=0),
)

_FACT_TOOL_EVENT_COLUMNS = (
    PinnedColumn("session_id", "TEXT", notnull=1, primary_key_position=1),
    PinnedColumn("ordinal", "INTEGER", notnull=1, primary_key_position=2),
    PinnedColumn("tool_name", "TEXT", notnull=1, primary_key_position=0),
    PinnedColumn("input_fingerprint", "TEXT", notnull=1, primary_key_position=0),
    PinnedColumn("file_identity", "TEXT", notnull=0, primary_key_position=0),
    PinnedColumn("timestamp", "TEXT", notnull=1, primary_key_position=0),
    PinnedColumn("is_error", "INTEGER", notnull=1, primary_key_position=0),
    PinnedColumn("denial_kind", "TEXT", notnull=0, primary_key_position=0),
    PinnedColumn("result_size", "INTEGER", notnull=0, primary_key_position=0),
)

_DIM_AGENT_COLUMNS = (
    PinnedColumn("agent_definition_id", "TEXT", notnull=0, primary_key_position=1),
    PinnedColumn("scope", "TEXT", notnull=1, primary_key_position=0),
    PinnedColumn("source_project", "TEXT", notnull=0, primary_key_position=0),
    PinnedColumn("name", "TEXT", notnull=1, primary_key_position=0),
    PinnedColumn("model", "TEXT", notnull=0, primary_key_position=0),
    PinnedColumn("effort", "TEXT", notnull=0, primary_key_position=0),
    PinnedColumn("tools", "TEXT", notnull=1, primary_key_position=0),
    PinnedColumn("skills", "TEXT", notnull=1, primary_key_position=0),
    PinnedColumn("revision_mtime_ns", "INTEGER", notnull=1, primary_key_position=0),
    PinnedColumn("revision_size", "INTEGER", notnull=1, primary_key_position=0),
    PinnedColumn("revision_content_hash", "TEXT", notnull=1, primary_key_position=0),
)


_BRIDGE_SESSION_SKILL_COLUMNS = (
    PinnedColumn("session_id", "TEXT", notnull=1, primary_key_position=1),
    PinnedColumn("skill_name", "TEXT", notnull=1, primary_key_position=2),
    PinnedColumn("declared", "TEXT", notnull=1, primary_key_position=0),
    PinnedColumn("available", "TEXT", notnull=1, primary_key_position=0),
    PinnedColumn("fired", "INTEGER", notnull=1, primary_key_position=0),
)

_VERDICT_CLAIM_COLUMNS = (
    PinnedColumn("session_id", "TEXT", notnull=1, primary_key_position=1),
    PinnedColumn("judge_input_hash", "TEXT", notnull=1, primary_key_position=2),
    PinnedColumn("rubric_version", "TEXT", notnull=1, primary_key_position=3),
    PinnedColumn("requested_model", "TEXT", notnull=1, primary_key_position=4),
    PinnedColumn("owner", "TEXT", notnull=1, primary_key_position=0),
    PinnedColumn("expires_at", "TEXT", notnull=1, primary_key_position=0),
)


def _emitted_columns(db_path: Path, table: str) -> tuple[PinnedColumn, ...]:
    with sqlite3.connect(db_path) as connection:
        ensure_schema(connection)
        rows = connection.execute(_TABLE_INFO_SQL, (table,)).fetchall()
    return tuple(
        PinnedColumn(
            name=str(name),
            declared_type=str(declared_type),
            notnull=int(notnull),
            primary_key_position=int(pk),
        )
        for name, declared_type, notnull, pk in rows
    )


def test_fact_session_schema_has_the_declared_columns_in_order(tmp_path: Path) -> None:
    emitted = _emitted_columns(tmp_path / "agentlens.db", "fact_session")
    assert emitted == _FACT_SESSION_COLUMNS


def test_fact_tool_event_schema_has_the_declared_columns_in_order(tmp_path: Path) -> None:
    emitted = _emitted_columns(tmp_path / "agentlens.db", "fact_tool_event")
    assert emitted == _FACT_TOOL_EVENT_COLUMNS


def test_dim_agent_schema_has_the_declared_columns_in_order(tmp_path: Path) -> None:
    emitted = _emitted_columns(tmp_path / "agentlens.db", "dim_agent")
    assert emitted == _DIM_AGENT_COLUMNS


def test_bridge_session_skill_schema_has_the_declared_columns_in_order(tmp_path: Path) -> None:
    emitted = _emitted_columns(tmp_path / "agentlens.db", "bridge_session_skill")
    assert emitted == _BRIDGE_SESSION_SKILL_COLUMNS


def test_verdict_claim_schema_has_the_declared_columns_in_order(tmp_path: Path) -> None:
    emitted = _emitted_columns(tmp_path / "agentlens.db", "verdict_claim")
    assert emitted == _VERDICT_CLAIM_COLUMNS
