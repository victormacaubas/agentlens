from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from datetime import date

from agentlens.errors import ScoringClaimError, StaleVerdictError
from agentlens.store.models import (
    AgentDefRecord,
    ScoringClaimRecord,
    SessionRecord,
    SkillBridgeRecord,
    ToolEventRecord,
    VerdictRecord,
)
from agentlens.store.schema import FACT_SESSION_COLUMNS

_FACT_SESSION_INSERT_SQL = (
    f"INSERT OR REPLACE INTO fact_session ({', '.join(FACT_SESSION_COLUMNS)}) "
    f"VALUES ({', '.join('?' for _ in FACT_SESSION_COLUMNS)})"
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
            (session_id, seq, tool_name, is_error, denial_kind, ts, input_hash,
             file_path_hash, output_bytes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                event.file_path_hash,
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
    _validate_child_identities(session_id, events, ())
    with conn:
        _replace_session_events(conn, session_id, events)


def upsert_agent_definition(conn: sqlite3.Connection, agent: AgentDefRecord) -> None:
    """Persist one immutable agent-definition version."""
    with conn:
        conn.execute(
            """
            INSERT INTO dim_agent
                (agent_definition_id, agent_type, scope, source_project, name, model,
                 effort, declared_tools, declared_skills, definition_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(agent_definition_id) DO UPDATE SET
                name = excluded.name,
                model = excluded.model,
                effort = excluded.effort,
                declared_tools = excluded.declared_tools,
                declared_skills = excluded.declared_skills
            """,
            (
                agent.effective_definition_id,
                agent.agent_type,
                agent.scope,
                agent.source_project,
                agent.name,
                agent.model,
                agent.effort,
                json.dumps(list(agent.declared_tools)),
                json.dumps(list(agent.declared_skills)),
                agent.definition_hash,
            ),
        )


def fetch_declared_skills(
    conn: sqlite3.Connection,
    agent_type: str,
    *,
    source_project: str | None = None,
    definition_id: str | None = None,
) -> list[str]:
    """Look up `dim_agent.declared_skills` for `agent_type`.

    Returns an empty list when the agent type is unknown or its
    `declared_skills` cannot be decoded — never raises, since a missing
    agent definition should not block skill-bridge derivation.
    """
    if definition_id is not None:
        cursor = conn.cursor()
        cursor.row_factory = sqlite3.Row
        row = cursor.execute(
            "SELECT declared_skills FROM dim_agent WHERE agent_definition_id = ?",
            (definition_id,),
        ).fetchone()
    else:
        effective = fetch_effective_agent_definition(
            conn,
            agent_type=agent_type,
            source_project=source_project or "",
        )
        if effective is None:
            return []
        return list(effective.declared_skills)
    if row is None or row["declared_skills"] is None:
        return []
    try:
        data = json.loads(row["declared_skills"])
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def fetch_effective_agent_definition(
    conn: sqlite3.Connection,
    *,
    agent_type: str,
    source_project: str,
) -> AgentDefRecord | None:
    """Resolve the latest project definition before the latest user fallback."""
    cursor = conn.cursor()
    cursor.row_factory = sqlite3.Row
    for scope, project in (("project", source_project), ("user", None)):
        row = cursor.execute(
            """
            SELECT agent_definition_id, agent_type, scope, source_project, name, model,
                   effort, declared_tools, declared_skills, definition_hash
            FROM dim_agent
            WHERE agent_type = ? AND scope = ? AND source_project IS ?
            ORDER BY rowid DESC
            LIMIT 1
            """,
            (agent_type, scope, project),
        ).fetchone()
        if row is not None:
            return _agent_definition_from_row(row)
    return None


def resolve_session_agent_definition(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    source_revision: str,
    agent_type: str,
    source_project: str,
) -> AgentDefRecord | None:
    """Preserve a session's prior binding for the same source revision."""
    cursor = conn.cursor()
    cursor.row_factory = sqlite3.Row
    row = cursor.execute(
        """
        SELECT da.agent_definition_id, da.agent_type, da.scope, da.source_project,
               da.name, da.model, da.effort, da.declared_tools, da.declared_skills,
               da.definition_hash
        FROM fact_session fs
        JOIN dim_agent da ON da.agent_definition_id = fs.agent_definition_id
        WHERE fs.session_id = ? AND fs.source_revision = ?
        """,
        (session_id, source_revision),
    ).fetchone()
    if row is not None:
        return _agent_definition_from_row(row)
    return fetch_effective_agent_definition(
        conn,
        agent_type=agent_type,
        source_project=source_project,
    )


def _agent_definition_from_row(row: sqlite3.Row) -> AgentDefRecord:
    declared_tools = _decode_string_list(row["declared_tools"])
    declared_skills = _decode_string_list(row["declared_skills"])
    return AgentDefRecord(
        definition_id=str(row["agent_definition_id"]),
        agent_type=str(row["agent_type"]),
        scope=str(row["scope"]),
        source_project=(
            str(row["source_project"]) if row["source_project"] is not None else None
        ),
        name=str(row["name"]),
        model=str(row["model"]) if row["model"] is not None else None,
        effort=str(row["effort"]) if row["effort"] is not None else None,
        declared_tools=declared_tools,
        declared_skills=declared_skills,
        definition_hash=str(row["definition_hash"]),
    )


def _decode_string_list(value: object) -> list[str]:
    if not isinstance(value, str):
        return []
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(decoded, list):
        return []
    return [item for item in decoded if isinstance(item, str)]


def _upsert_session(conn: sqlite3.Connection, record: SessionRecord) -> None:
    conn.execute(_FACT_SESSION_INSERT_SQL, _fact_session_values(record))


def _fact_session_values(record: SessionRecord) -> tuple[object, ...]:
    """Read `record` in `FACT_SESSION_COLUMNS` order, matching `_FACT_SESSION_INSERT_SQL`.

    `SessionRecord`'s field names match the `fact_session` column names, so
    the column list drives the value order directly rather than a second
    hand-maintained tuple.
    """
    return tuple(
        int(value) if isinstance(value, bool) else value
        for value in (getattr(record, column) for column in FACT_SESSION_COLUMNS)
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
    _validate_child_identities(session_id, (), records)
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


def set_session_judge_input_hash(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    source_revision: str,
    judge_input_hash: str,
) -> bool:
    """Set the exact prepared-input hash if the source revision is still current."""
    with conn:
        cursor = conn.execute(
            """
            UPDATE fact_session
            SET judge_input_hash = ?
            WHERE session_id = ? AND source_revision = ?
            """,
            (judge_input_hash, session_id, source_revision),
        )
    return cursor.rowcount == 1


def verdict_exists(conn: sqlite3.Connection, record: VerdictRecord) -> bool:
    """Return whether the exact input/rubric/concrete-model verdict is cached."""
    row = conn.execute(
        """
        SELECT 1
        FROM fact_verdict
        WHERE session_id = ?
          AND judge_input_hash = ?
          AND rubric_version = ?
          AND judge_model = ?
        """,
        (
            record.session_id,
            record.judge_input_hash,
            record.rubric_version,
            record.judge_model,
        ),
    ).fetchone()
    return row is not None


def acquire_scoring_claim(
    conn: sqlite3.Connection,
    claim: ScoringClaimRecord,
    *,
    now: str,
) -> bool:
    """Atomically acquire unscored work, replacing only an expired claim."""
    with conn:
        cursor = conn.execute(
            """
            INSERT INTO scoring_claim (
                session_id, judge_input_hash, rubric_version, judge_model,
                owner_id, expires_at
            )
            SELECT ?, ?, ?, ?, ?, ?
            WHERE EXISTS (
                SELECT 1
                FROM fact_session
                WHERE session_id = ? AND judge_input_hash = ?
            )
              AND NOT EXISTS (
                SELECT 1
                FROM fact_verdict
                WHERE session_id = ?
                  AND judge_input_hash = ?
                  AND rubric_version = ?
                  AND judge_model = ?
              )
            ON CONFLICT(session_id, judge_input_hash, rubric_version, judge_model)
            DO UPDATE SET
                owner_id = excluded.owner_id,
                expires_at = excluded.expires_at
            WHERE scoring_claim.expires_at <= ?
               OR scoring_claim.owner_id = excluded.owner_id
            """,
            (
                claim.session_id,
                claim.judge_input_hash,
                claim.rubric_version,
                claim.judge_model,
                claim.owner_id,
                claim.expires_at,
                claim.session_id,
                claim.judge_input_hash,
                claim.session_id,
                claim.judge_input_hash,
                claim.rubric_version,
                claim.judge_model,
                now,
            ),
        )
    return cursor.rowcount == 1


def release_scoring_claim(conn: sqlite3.Connection, claim: ScoringClaimRecord) -> bool:
    """Release a claim only when its current owner requests release."""
    with conn:
        cursor = conn.execute(
            """
            DELETE FROM scoring_claim
            WHERE session_id = ?
              AND judge_input_hash = ?
              AND rubric_version = ?
              AND judge_model = ?
              AND owner_id = ?
            """,
            (
                claim.session_id,
                claim.judge_input_hash,
                claim.rubric_version,
                claim.judge_model,
                claim.owner_id,
            ),
        )
    return cursor.rowcount == 1


def finalize_scoring_claim(
    conn: sqlite3.Connection,
    *,
    claim: ScoringClaimRecord,
    verdict: VerdictRecord,
    now: str,
) -> None:
    """Persist a verdict and release its active claim in one transaction.

    The concrete verdict model may differ from the claim model when a floating
    alias was used before the successful call resolved model identity.
    """
    if (
        verdict.session_id != claim.session_id
        or verdict.judge_input_hash != claim.judge_input_hash
        or verdict.rubric_version != claim.rubric_version
    ):
        raise ScoringClaimError("verdict identity does not match the scoring claim")

    with conn:
        inserted = conn.execute(
            """
            INSERT INTO fact_verdict (
                session_id, judge_input_hash, rubric_version, judge_model,
                verdict_json, judge_cost_usd, judge_input_tokens, judge_output_tokens
            )
            SELECT ?, ?, ?, ?, ?, ?, ?, ?
            WHERE EXISTS (
                SELECT 1
                FROM fact_session
                WHERE session_id = ? AND judge_input_hash = ?
            )
              AND EXISTS (
                SELECT 1
                FROM scoring_claim
                WHERE session_id = ?
                  AND judge_input_hash = ?
                  AND rubric_version = ?
                  AND judge_model = ?
                  AND owner_id = ?
                  AND expires_at > ?
              )
            ON CONFLICT(session_id, judge_input_hash, rubric_version, judge_model)
            DO UPDATE SET
                verdict_json = excluded.verdict_json,
                judge_cost_usd = excluded.judge_cost_usd,
                judge_input_tokens = excluded.judge_input_tokens,
                judge_output_tokens = excluded.judge_output_tokens
            """,
            (
                verdict.session_id,
                verdict.judge_input_hash,
                verdict.rubric_version,
                verdict.judge_model,
                verdict.verdict_json,
                verdict.judge_cost_usd,
                verdict.judge_input_tokens,
                verdict.judge_output_tokens,
                claim.session_id,
                claim.judge_input_hash,
                claim.session_id,
                claim.judge_input_hash,
                claim.rubric_version,
                claim.judge_model,
                claim.owner_id,
                now,
            ),
        )
        if inserted.rowcount != 1:
            session_cursor = conn.cursor()
            session_cursor.row_factory = sqlite3.Row
            session_row = session_cursor.execute(
                "SELECT judge_input_hash FROM fact_session WHERE session_id = ?",
                (claim.session_id,),
            ).fetchone()
            if session_row is None or session_row["judge_input_hash"] != claim.judge_input_hash:
                raise StaleVerdictError(
                    f"session {claim.session_id} changed while scoring was in flight"
                )
            raise ScoringClaimError("scoring claim is no longer active for this owner")

        deleted = conn.execute(
            """
            DELETE FROM scoring_claim
            WHERE session_id = ?
              AND judge_input_hash = ?
              AND rubric_version = ?
              AND judge_model = ?
              AND owner_id = ?
            """,
            (
                claim.session_id,
                claim.judge_input_hash,
                claim.rubric_version,
                claim.judge_model,
                claim.owner_id,
            ),
        )
        if deleted.rowcount != 1:
            raise ScoringClaimError("scoring claim ownership changed during finalization")


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
) -> bool:
    """Atomically replace every store row derived from one parsed session.

    The session facts, events, skill bridge, and dimension backfills commit
    together. Any exception rolls the complete write set back, preserving the
    previously committed session version when one exists.
    """
    _validate_child_identities(record.session_id, events, skills)
    with conn:
        if not _source_revision_can_replace(conn, record):
            return False
        _replace_session_events(conn, record.session_id, events)
        _upsert_session(conn, record)
        _replace_session_skills(conn, record.session_id, skills)
        for tool_name in sorted({event.tool_name for event in events}):
            _upsert_dim_tool(conn, tool_name)
        if record.session_date is not None:
            _backfill_dim_date(conn, record.session_date)
    return True


def _validate_child_identities(
    session_id: str,
    events: Sequence[ToolEventRecord],
    skills: Sequence[SkillBridgeRecord],
) -> None:
    mismatched_events = [event.session_id for event in events if event.session_id != session_id]
    mismatched_skills = [
        skill.session_id for skill in skills if skill.session_id != session_id
    ]
    if mismatched_events or mismatched_skills:
        raise ValueError(
            "session-grain child identity mismatch: "
            f"expected {session_id}, event_ids={sorted(set(mismatched_events))}, "
            f"skill_ids={sorted(set(mismatched_skills))}"
        )


def _source_revision_can_replace(
    conn: sqlite3.Connection,
    record: SessionRecord,
) -> bool:
    cursor = conn.cursor()
    cursor.row_factory = sqlite3.Row
    row = cursor.execute(
        """
        SELECT source_mtime_ns, source_size, source_content_hash
        FROM fact_session
        WHERE session_id = ?
        """,
        (record.session_id,),
    ).fetchone()
    if row is None:
        return True

    stored_mtime_ns = int(row["source_mtime_ns"])
    stored_size = int(row["source_size"])
    stored_content_hash = str(row["source_content_hash"])
    if record.source_mtime_ns < stored_mtime_ns:
        return False
    return not (
        record.source_mtime_ns == stored_mtime_ns
        and record.source_size == stored_size
        and record.source_content_hash != stored_content_hash
    )
