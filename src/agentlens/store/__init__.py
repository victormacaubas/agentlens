"""SQLite schema, path resolution, and upserts for the agentlens store.

The store is a disposable cache under `~/.cache/agentlens/` (see
`resolve_store_path`); it is rebuilt from `.claude/` at any time and carries
no migration path. Schema changes (e.g. the `n_retry_loops` ->
`n_duplicate_tool_calls` rename and `claimed_status` demotion below) are
applied by recreating the DDL — delete the cache file, or point `--store`
at a fresh path, after a schema change ships.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

STORE_PATH_ENV_VAR: Final[str] = "AGENTLENS_STORE"
DEFAULT_STORE_DIR: Final[Path] = Path.home() / ".cache" / "agentlens"
DEFAULT_STORE_FILENAME: Final[str] = "agentlens.db"

REQUIRED_TABLES: Final[tuple[str, ...]] = (
    "fact_tool_event",
    "fact_session",
    "dim_agent",
    "dim_date",
    "dim_tool",
    "bridge_session_skill",
    "fact_verdict",
)

_DDL = """
CREATE TABLE IF NOT EXISTS fact_tool_event (
    session_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    tool_name TEXT NOT NULL,
    is_error INTEGER NOT NULL DEFAULT 0,
    denial_kind TEXT,
    ts TEXT,
    input_hash TEXT,
    output_bytes INTEGER,
    PRIMARY KEY (session_id, seq)
);

CREATE TABLE IF NOT EXISTS fact_session (
    session_id TEXT PRIMARY KEY,
    agent_id TEXT,
    agent_type TEXT,
    name_source TEXT,
    session_kind TEXT,
    spawn_depth INTEGER,
    parent_session_id TEXT,
    spawn_tool_use_id TEXT,
    task_description TEXT,
    session_date TEXT,
    n_turns INTEGER,
    n_tool_calls INTEGER,
    n_reads INTEGER,
    n_edits INTEGER,
    n_writes INTEGER,
    n_bash INTEGER,
    n_files_touched INTEGER,
    n_errors INTEGER,
    n_permission_denials INTEGER,
    n_duplicate_tool_calls INTEGER,
    final_report_flagged_partial INTEGER NOT NULL DEFAULT 0,
    duration_sec REAL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cache_read_tokens INTEGER,
    cache_creation_tokens INTEGER,
    task_prompt_len INTEGER,
    n_skills_fired INTEGER
);

CREATE TABLE IF NOT EXISTS dim_agent (
    agent_type TEXT PRIMARY KEY,
    name TEXT,
    model TEXT,
    effort TEXT,
    declared_tools TEXT,
    declared_skills TEXT,
    definition_hash TEXT
);

CREATE TABLE IF NOT EXISTS dim_date (
    date TEXT PRIMARY KEY,
    year INTEGER,
    month INTEGER,
    day INTEGER,
    iso_week INTEGER
);

CREATE TABLE IF NOT EXISTS dim_tool (
    tool_name TEXT PRIMARY KEY,
    category TEXT
);

CREATE TABLE IF NOT EXISTS bridge_session_skill (
    session_id TEXT NOT NULL,
    skill_name TEXT NOT NULL,
    declared INTEGER NOT NULL DEFAULT 0,
    available INTEGER NOT NULL DEFAULT 0,
    fired INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (session_id, skill_name)
);

CREATE TABLE IF NOT EXISTS fact_verdict (
    session_id TEXT NOT NULL,
    rubric_version TEXT NOT NULL,
    judge_model TEXT NOT NULL,
    verdict_json TEXT NOT NULL,
    judge_cost_usd REAL,
    judge_input_tokens INTEGER,
    judge_output_tokens INTEGER,
    PRIMARY KEY (session_id, rubric_version, judge_model)
);
"""


class StoreLocationError(ValueError):
    """Raised when a resolved store path would write inside a `.claude/` tree."""


def resolve_store_path(
    *,
    store_override: Path | str | None = None,
    env: Mapping[str, str] | None = None,
) -> Path:
    """Resolve the store's on-disk location.

    Precedence: an explicit ``store_override`` (the CLI's ``--store`` flag)
    wins, then the ``AGENTLENS_STORE`` env var, then the default
    ``~/.cache/agentlens/agentlens.db``. Never resolves to a path inside a
    ``.claude/`` directory.

    Args:
        store_override: Explicit path, e.g. from a CLI flag.
        env: Environment mapping to read ``AGENTLENS_STORE`` from; defaults
            to ``os.environ`` when not given.

    Raises:
        StoreLocationError: If the resolved path falls inside a `.claude/`
            directory.
    """
    if store_override is not None:
        path = Path(store_override).expanduser()
    else:
        env_map = env if env is not None else os.environ
        env_value = env_map.get(STORE_PATH_ENV_VAR)
        default_path = DEFAULT_STORE_DIR / DEFAULT_STORE_FILENAME
        path = Path(env_value).expanduser() if env_value else default_path

    _assert_outside_claude_dir(path)
    return path


def _assert_outside_claude_dir(path: Path) -> None:
    if ".claude" in path.parts:
        raise StoreLocationError(f"refusing to write the store inside a .claude directory: {path}")


def create_store(path: Path) -> sqlite3.Connection:
    """Create (if needed) and open the SQLite store, applying the full DDL.

    Idempotent: safe to call on an already-initialized store file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    with conn:
        conn.executescript(_DDL)
    return conn


@dataclass(frozen=True)
class ToolEventRecord:
    """One `fact_tool_event` row: a paired tool_use/tool_result observation."""

    session_id: str
    seq: int
    tool_name: str
    is_error: bool
    denial_kind: str | None
    ts: str | None
    input_hash: str | None
    output_bytes: int | None


def upsert_session_events(
    conn: sqlite3.Connection,
    session_id: str,
    events: Sequence[ToolEventRecord],
) -> None:
    """Replace all `fact_tool_event` rows for `session_id` in one transaction.

    Delete-then-insert per session_id gives idempotency (D6): re-running the
    same session produces the same row set, and a session's events are never
    duplicated across runs.
    """
    with conn:
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


@dataclass(frozen=True)
class AgentDefRecord:
    """One `dim_agent` row: an agent definition scanned from `.claude/agents/**`."""

    agent_type: str
    name: str
    model: str | None
    effort: str | None
    declared_tools: Sequence[str]
    declared_skills: Sequence[str]
    definition_hash: str


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


@dataclass(frozen=True)
class SessionRecord:
    """One `fact_session` row: the per-spawn deterministic grain (D1).

    Event-derived counts (aggregated from `fact_tool_event`) and
    transcript-read fields (usage, turns, duration — a documented exception
    to "store `fact_tool_event`, derive the rest") are combined by
    `agentlens.aggregation.derive_fact_session`.
    """

    session_id: str
    agent_id: str | None
    agent_type: str | None
    name_source: str | None
    session_kind: str
    spawn_depth: int | None
    parent_session_id: str | None
    spawn_tool_use_id: str | None
    task_description: str | None
    session_date: str | None
    n_turns: int
    n_tool_calls: int
    n_reads: int
    n_edits: int
    n_writes: int
    n_bash: int
    n_files_touched: int
    n_errors: int
    n_permission_denials: int
    n_duplicate_tool_calls: int
    final_report_flagged_partial: bool
    duration_sec: float
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    task_prompt_len: int | None
    n_skills_fired: int


def upsert_session(conn: sqlite3.Connection, record: SessionRecord) -> None:
    """Replace the `fact_session` row for `record.session_id`.

    `session_id` is the table's primary key, so `INSERT OR REPLACE` is a
    single-row idempotent upsert — no delete-then-insert needed.
    """
    with conn:
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


@dataclass(frozen=True)
class SkillBridgeRecord:
    """One `bridge_session_skill` row: declared/available/fired for one skill."""

    session_id: str
    skill_name: str
    declared: bool
    available: bool
    fired: bool


def upsert_session_skills(
    conn: sqlite3.Connection,
    session_id: str,
    records: Sequence[SkillBridgeRecord],
) -> None:
    """Replace all `bridge_session_skill` rows for `session_id`.

    Delete-then-insert per session_id, mirroring `upsert_session_events`.
    """
    with conn:
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


def upsert_dim_date(
    conn: sqlite3.Connection,
    date: str,
    *,
    year: int,
    month: int,
    day: int,
    iso_week: int,
) -> None:
    """Idempotently insert a `dim_date` row; a no-op if `date` already exists."""
    with conn:
        conn.execute(
            """
            INSERT INTO dim_date (date, year, month, day, iso_week)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(date) DO NOTHING
            """,
            (date, year, month, day, iso_week),
        )


def upsert_dim_tool(
    conn: sqlite3.Connection, tool_name: str, *, category: str | None = None
) -> None:
    """Idempotently insert a `dim_tool` row; a no-op if `tool_name` already exists."""
    with conn:
        conn.execute(
            """
            INSERT INTO dim_tool (tool_name, category)
            VALUES (?, ?)
            ON CONFLICT(tool_name) DO NOTHING
            """,
            (tool_name, category),
        )
