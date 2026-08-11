"""Read the store's `fact_session` / `fact_tool_event` rows back into domain objects.

The shared hydration path for anything that needs a full stored session:
the judge's scoring loop rebuilds a `ParsedSession` to feed the transcript
view, and any future caller that needs the same round trip reaches for
these functions instead of writing its own row mapper.
"""

from __future__ import annotations

import sqlite3

from agentlens.parser.session import SESSION_KIND_SUBAGENT, ParsedSession
from agentlens.store.models import SessionRecord, ToolEventRecord


def hydrate_session_record(row: sqlite3.Row) -> SessionRecord:
    """Map one full `fact_session` row to a `SessionRecord`.

    `row` must come from a cursor with `row_factory = sqlite3.Row` and a
    `SELECT` covering every `fact_session` column (see
    `FACT_SESSION_COLUMNS` in `store/schema.py`).
    """
    return SessionRecord(
        session_id=row["session_id"],
        agent_id=row["agent_id"],
        agent_type=row["agent_type"],
        name_source=row["name_source"],
        session_kind=row["session_kind"],
        spawn_depth=row["spawn_depth"],
        parent_session_id=row["parent_session_id"],
        spawn_tool_use_id=row["spawn_tool_use_id"],
        task_description=row["task_description"],
        session_date=row["session_date"],
        n_turns=row["n_turns"],
        n_tool_calls=row["n_tool_calls"],
        n_reads=row["n_reads"],
        n_edits=row["n_edits"],
        n_writes=row["n_writes"],
        n_bash=row["n_bash"],
        n_files_touched=row["n_files_touched"],
        n_errors=row["n_errors"],
        n_permission_denials=row["n_permission_denials"],
        n_duplicate_tool_calls=row["n_duplicate_tool_calls"],
        final_report_flagged_partial=bool(row["final_report_flagged_partial"]),
        duration_sec=row["duration_sec"],
        input_tokens=row["input_tokens"],
        output_tokens=row["output_tokens"],
        cache_read_tokens=row["cache_read_tokens"],
        cache_creation_tokens=row["cache_creation_tokens"],
        task_prompt_len=row["task_prompt_len"],
        n_skills_fired=row["n_skills_fired"],
        raw_session_id=row["raw_session_id"],
        source_project=row["source_project"],
        source_revision=row["source_revision"],
        source_mtime_ns=row["source_mtime_ns"],
        source_size=row["source_size"],
        source_content_hash=row["source_content_hash"],
        judge_input_hash=row["judge_input_hash"],
        agent_definition_id=row["agent_definition_id"],
    )


def fetch_session_events(conn: sqlite3.Connection, session_id: str) -> list[ToolEventRecord]:
    """Load every `fact_tool_event` row for `session_id`, ordered by `seq`."""
    cursor = conn.cursor()
    cursor.row_factory = sqlite3.Row
    rows = cursor.execute(
        """
        SELECT session_id, seq, tool_name, is_error, denial_kind, ts, input_hash,
               file_path_hash, output_bytes
        FROM fact_tool_event
        WHERE session_id = ?
        ORDER BY seq
        """,
        (session_id,),
    ).fetchall()
    return [
        ToolEventRecord(
            session_id=row["session_id"],
            seq=row["seq"],
            tool_name=row["tool_name"],
            is_error=bool(row["is_error"]),
            denial_kind=row["denial_kind"],
            ts=row["ts"],
            input_hash=row["input_hash"],
            file_path_hash=row["file_path_hash"],
            output_bytes=row["output_bytes"],
        )
        for row in rows
    ]


def hydrate_parsed_session(
    record: SessionRecord, *, events: list[ToolEventRecord]
) -> ParsedSession:
    """Reconstruct the `ParsedSession` fields `build_transcript_view` needs
    from a stored `fact_session` row plus its `fact_tool_event` rows.

    `ambiguous`, `first_ts`, and `fired_skills` aren't read by
    `build_transcript_view` and carry placeholder values here — they're
    name-resolution/skill-bridge concerns settled at ingest time, not
    re-derivable (or needed) from the store alone.
    """
    return ParsedSession(
        session_id=record.session_id,
        session_kind=record.session_kind or SESSION_KIND_SUBAGENT,
        agent_id=record.agent_id,
        name=record.agent_type,
        name_source=record.name_source,
        ambiguous=False,
        parent_session_id=record.parent_session_id,
        spawn_tool_use_id=record.spawn_tool_use_id,
        task_description=record.task_description,
        spawn_depth=record.spawn_depth,
        events=events,
        n_turns=record.n_turns,
        duration_sec=record.duration_sec,
        first_ts=None,
        input_tokens=record.input_tokens,
        output_tokens=record.output_tokens,
        cache_read_tokens=record.cache_read_tokens,
        cache_creation_tokens=record.cache_creation_tokens,
        fired_skills=[],
        final_report_flagged_partial=record.final_report_flagged_partial,
    )
