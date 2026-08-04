from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from datetime import date

from agentlens.store.models import (
    AgentDefRecord,
    SessionRecord,
    SkillBridgeRecord,
    ToolEventRecord,
)


def _replace_session_events(
    conn: sqlite3.Connection,
    session_id: str,
    events: Sequence[ToolEventRecord],
) -> None:
    conn.execute("DELETE FROM fact_tool_event WHERE session_id = ?", (session_id,))
    conn.executemany(
        """
        INSERT INTO fact_tool_event
            (session_id, seq, tool_name, is_error, denial_kind, ts, input_hash, output_bytes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                event.session_id,
                event.seq,
                event.tool_name,
                int(event.is_error),
                event.denial_kind,
                event.ts,
                event.input_hash,
                event.output_bytes,
            )
            for event in events
        ],
    )


def upsert_session_events(
    conn: sqlite3.Connection,
    session_id: str,
    events: Sequence[ToolEventRecord],
) -> None:
    """Replace all `fact_tool_event` rows for `session_id` in one transaction.

    Delete-then-insert per session_id gives idempotency: re-running the
    same session produces the same row set, and a session's events are never
    duplicated across runs.
    """
    with conn:
        _replace_session_events(conn, session_id, events)


def upsert_agent_definition(conn: sqlite3.Connection, agent: AgentDefRecord) -> None:
    """Upsert a `dim_agent` row, keyed on `agent_type` (SCD-style overwrite)."""
    with conn:
        conn.execute(
            """
            INSERT INTO dim_agent
                (agent_type, name, model, effort, declared_tools, declared_skills, definition_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(agent_type) DO UPDATE SET
                name = excluded.name,
                model = excluded.model,
                effort = excluded.effort,
                declared_tools = excluded.declared_tools,
                declared_skills = excluded.declared_skills,
                definition_hash = excluded.definition_hash
            """,
            (
                agent.agent_type,
                agent.name,
                agent.model,
                agent.effort,
                json.dumps(list(agent.declared_tools)),
                json.dumps(list(agent.declared_skills)),
                agent.definition_hash,
            ),
        )


def fetch_declared_skills(conn: sqlite3.Connection, agent_type: str) -> list[str]:
    """Look up `dim_agent.declared_skills` for `agent_type`.

    Returns an empty list when the agent type is unknown or its
    `declared_skills` cannot be decoded — never raises, since a missing
    agent definition should not block skill-bridge derivation.
    """
    row = conn.execute(
        "SELECT declared_skills FROM dim_agent WHERE agent_type = ?", (agent_type,)
    ).fetchone()
    if row is None or row[0] is None:
        return []
    try:
        data = json.loads(row[0])
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def _upsert_session(conn: sqlite3.Connection, record: SessionRecord) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO fact_session (
            session_id, agent_id, agent_type, name_source, session_kind,
            spawn_depth, parent_session_id, spawn_tool_use_id, task_description,
            session_date, n_turns, n_tool_calls, n_reads, n_edits, n_writes, n_bash,
            n_files_touched, n_errors, n_permission_denials, n_duplicate_tool_calls,
            final_report_flagged_partial, duration_sec, input_tokens, output_tokens,
            cache_read_tokens, cache_creation_tokens, task_prompt_len, n_skills_fired
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        (
            record.session_id,
            record.agent_id,
            record.agent_type,
            record.name_source,
            record.session_kind,
            record.spawn_depth,
            record.parent_session_id,
            record.spawn_tool_use_id,
            record.task_description,
            record.session_date,
            record.n_turns,
            record.n_tool_calls,
            record.n_reads,
            record.n_edits,
            record.n_writes,
            record.n_bash,
            record.n_files_touched,
            record.n_errors,
            record.n_permission_denials,
            record.n_duplicate_tool_calls,
            int(record.final_report_flagged_partial),
            record.duration_sec,
            record.input_tokens,
            record.output_tokens,
            record.cache_read_tokens,
            record.cache_creation_tokens,
            record.task_prompt_len,
            record.n_skills_fired,
        ),
    )


def upsert_session(conn: sqlite3.Connection, record: SessionRecord) -> None:
    """Replace the `fact_session` row for `record.session_id`.

    `session_id` is the table's primary key, so `INSERT OR REPLACE` is a
    single-row idempotent upsert — no delete-then-insert needed.
    """
    with conn:
        _upsert_session(conn, record)


def _replace_session_skills(
    conn: sqlite3.Connection,
    session_id: str,
    records: Sequence[SkillBridgeRecord],
) -> None:
    conn.execute("DELETE FROM bridge_session_skill WHERE session_id = ?", (session_id,))
    conn.executemany(
        """
        INSERT INTO bridge_session_skill (session_id, skill_name, declared, available, fired)
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            (
                record.session_id,
                record.skill_name,
                int(record.declared),
                int(record.available),
                int(record.fired),
            )
            for record in records
        ],
    )


def upsert_session_skills(
    conn: sqlite3.Connection,
    session_id: str,
    records: Sequence[SkillBridgeRecord],
) -> None:
    """Replace all `bridge_session_skill` rows for `session_id`.

    Delete-then-insert per session_id, mirroring `upsert_session_events`.
    """
    with conn:
        _replace_session_skills(conn, session_id, records)


def _upsert_dim_date(
    conn: sqlite3.Connection,
    date_str: str,
    *,
    year: int,
    month: int,
    day: int,
    iso_week: int,
) -> None:
    conn.execute(
        """
        INSERT INTO dim_date (date, year, month, day, iso_week)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(date) DO NOTHING
        """,
        (date_str, year, month, day, iso_week),
    )


def upsert_dim_date(
    conn: sqlite3.Connection,
    date_str: str,
    *,
    year: int,
    month: int,
    day: int,
    iso_week: int,
) -> None:
    """Idempotently insert a `dim_date` row; a no-op if `date_str` already exists."""
    with conn:
        _upsert_dim_date(
            conn,
            date_str,
            year=year,
            month=month,
            day=day,
            iso_week=iso_week,
        )


def _upsert_dim_tool(
    conn: sqlite3.Connection,
    tool_name: str,
    *,
    category: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO dim_tool (tool_name, category)
        VALUES (?, ?)
        ON CONFLICT(tool_name) DO NOTHING
        """,
        (tool_name, category),
    )


def upsert_dim_tool(
    conn: sqlite3.Connection, tool_name: str, *, category: str | None = None
) -> None:
    """Idempotently insert a `dim_tool` row; a no-op if `tool_name` already exists."""
    with conn:
        _upsert_dim_tool(conn, tool_name, category=category)


def _backfill_dim_date(conn: sqlite3.Connection, date_str: str) -> None:
    try:
        parsed_date = date.fromisoformat(date_str)
    except ValueError:
        return
    _, iso_week, _ = parsed_date.isocalendar()
    _upsert_dim_date(
        conn,
        date_str,
        year=parsed_date.year,
        month=parsed_date.month,
        day=parsed_date.day,
        iso_week=iso_week,
    )


def upsert_session_grain(
    conn: sqlite3.Connection,
    *,
    record: SessionRecord,
    events: Sequence[ToolEventRecord],
    skills: Sequence[SkillBridgeRecord],
) -> None:
    """Atomically replace every store row derived from one parsed session.

    The session facts, events, skill bridge, and dimension backfills commit
    together. Any exception rolls the complete write set back, preserving the
    previously committed session version when one exists.
    """
    with conn:
        _replace_session_events(conn, record.session_id, events)
        _upsert_session(conn, record)
        _replace_session_skills(conn, record.session_id, skills)
        for tool_name in sorted({event.tool_name for event in events}):
            _upsert_dim_tool(conn, tool_name)
        if record.session_date is not None:
            _backfill_dim_date(conn, record.session_date)
