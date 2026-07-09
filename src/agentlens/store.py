"""SQLite store: DDL for the full dimensional schema and the persistence layer.

This module owns table creation and the write path. Per the design (D2), all
seven tables are created on first run so the schema is frozen early, but this
change only *populates* ``fact_tool_event`` and ``dim_agent`` — the rest stay
empty until later phases (``fact_session``/``bridge_session_skill`` in
Phase 2, ``fact_verdict`` in Phase 3).

Store location resolution (D9): defaults to ``~/.cache/agentlens/agentlens.db``,
overridable via the ``AGENTLENS_STORE`` env var or an explicit path (wired to
the CLI's ``--store`` flag). Writing inside a ``.claude/`` directory is
refused — the store must never be mistaken for pipeline input.

Verdict-JSON shape stub (``VERDICT_SHAPE_STUB``): the fixed contract that
Phase 3's ``fact_verdict`` rows will follow. Documented here, not populated.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

STORE_PATH_ENV_VAR: Final[str] = "AGENTLENS_STORE"
DEFAULT_STORE_DIR: Final[Path] = Path.home() / ".cache" / "agentlens"
DEFAULT_STORE_FILENAME: Final[str] = "agentlens.db"

#: Fixed contract for `fact_verdict.verdict_json` (Phase 3). Not populated by
#: this change — per-dimension scores, an overall score, evidence quotes,
#: suggested fixes, and the judge's own run-cost fields.
VERDICT_SHAPE_STUB: Final[dict[str, Any]] = {
    "session_id": "",
    "rubric_version": "",
    "judge_model": "",
    "dimensions": {
        "task_completion": {"score": 0, "evidence": []},
        "honesty": {"score": 0, "evidence": []},
        "efficiency": {"score": 0, "evidence": []},
        "scope_adherence": {"score": 0, "evidence": []},
    },
    "overall_score": 0.0,
    "evidence": [],
    "suggested_fixes": [],
    "judge_cost_usd": 0.0,
    "judge_input_tokens": 0,
    "judge_output_tokens": 0,
}

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
    n_turns INTEGER,
    n_tool_calls INTEGER,
    n_reads INTEGER,
    n_edits INTEGER,
    n_writes INTEGER,
    n_bash INTEGER,
    n_files_touched INTEGER,
    n_errors INTEGER,
    n_permission_denials INTEGER,
    n_retry_loops INTEGER,
    claimed_status TEXT,
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
