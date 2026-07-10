"""SQLite schema and path resolution for the agentlens store.

The store is a disposable cache under `~/.cache/agentlens/` (see
`resolve_store_path`); it is rebuilt from `.claude/` at any time and carries
no migration path. Schema changes are applied by recreating the DDL — delete
the cache file, or point `--store` at a fresh path, after a schema change ships.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Final

from agentlens.errors import StoreLocationError

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
