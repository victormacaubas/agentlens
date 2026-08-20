"""SQLite schema and path resolution for the agentlens store.

The store is a disposable cache under `~/.cache/agentlens/` (see
`resolve_store_path`); it is rebuilt from `.claude/` at any time and carries
no migration path. Schema changes are applied by recreating the DDL — delete
the cache file, or point `--store` at a fresh path, after a schema change ships.

`SCHEMA_VERSION` is stamped into `PRAGMA user_version` so that a store left
over from an earlier shape is rejected with instructions rather than half-read.
Bump it whenever the DDL changes in a way the old tables cannot satisfy.
"""

from __future__ import annotations

import os
import sqlite3
import stat
from collections.abc import Mapping
from contextlib import suppress
from pathlib import Path
from typing import Final

from agentlens.errors import StoreLocationError, StoreSchemaError

SCHEMA_VERSION: Final[int] = 2
STORE_PATH_ENV_VAR: Final[str] = "AGENTLENS_STORE"
DEFAULT_STORE_DIR: Final[Path] = Path.home() / ".cache" / "agentlens"
DEFAULT_STORE_FILENAME: Final[str] = "agentlens.db"
PRIVATE_DIRECTORY_MODE: Final[int] = 0o700
PRIVATE_FILE_MODE: Final[int] = 0o600
_SQLITE_SIDECAR_SUFFIXES: Final[tuple[str, ...]] = ("-journal", "-wal", "-shm")

REQUIRED_TABLES: Final[tuple[str, ...]] = (
    "fact_tool_event",
    "fact_session",
    "dim_agent",
    "dim_date",
    "dim_tool",
    "bridge_session_skill",
    "fact_verdict",
    "scoring_claim",
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
    file_path_hash TEXT,
    output_bytes INTEGER,
    PRIMARY KEY (session_id, seq)
);

CREATE TABLE IF NOT EXISTS fact_session (
    session_id TEXT PRIMARY KEY,
    raw_session_id TEXT NOT NULL DEFAULT '',
    source_project TEXT NOT NULL DEFAULT '',
    agent_id TEXT,
    agent_type TEXT,
    agent_definition_id TEXT,
    name_source TEXT,
    session_kind TEXT,
    source_revision TEXT NOT NULL DEFAULT '',
    source_mtime_ns INTEGER NOT NULL DEFAULT 0,
    source_size INTEGER NOT NULL DEFAULT 0,
    source_content_hash TEXT NOT NULL DEFAULT '',
    judge_input_hash TEXT,
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
    agent_definition_id TEXT PRIMARY KEY,
    agent_type TEXT NOT NULL,
    scope TEXT NOT NULL,
    source_project TEXT,
    name TEXT,
    model TEXT,
    effort TEXT,
    declared_tools TEXT,
    declared_skills TEXT,
    definition_hash TEXT NOT NULL
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
    judge_input_hash TEXT NOT NULL DEFAULT '',
    rubric_version TEXT NOT NULL,
    judge_model TEXT NOT NULL,
    verdict_json TEXT NOT NULL,
    judge_cost_usd REAL,
    judge_input_tokens INTEGER,
    judge_output_tokens INTEGER,
    PRIMARY KEY (session_id, judge_input_hash, rubric_version, judge_model)
);

CREATE TABLE IF NOT EXISTS scoring_claim (
    session_id TEXT NOT NULL,
    judge_input_hash TEXT NOT NULL DEFAULT '',
    rubric_version TEXT NOT NULL,
    judge_model TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    PRIMARY KEY (session_id, judge_input_hash, rubric_version, judge_model)
);

CREATE INDEX IF NOT EXISTS idx_fact_session_window
    ON fact_session (session_kind, session_date, agent_type);
CREATE INDEX IF NOT EXISTS idx_fact_session_agent_window
    ON fact_session (session_kind, agent_type, session_date);
CREATE INDEX IF NOT EXISTS idx_fact_session_parent
    ON fact_session (parent_session_id, session_kind, session_date, agent_type);
CREATE INDEX IF NOT EXISTS idx_fact_session_parent_window
    ON fact_session (session_kind, session_date, parent_session_id, agent_type);
CREATE INDEX IF NOT EXISTS idx_dim_agent_resolution
    ON dim_agent (agent_type, scope, source_project);
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

    return _canonicalize_store_path(path)


def _assert_outside_claude_dir(path: Path) -> None:
    if ".claude" in path.parts:
        raise StoreLocationError(f"refusing to write the store inside a .claude directory: {path}")


def _canonicalize_store_path(path: Path) -> Path:
    lexical_path = Path(os.path.abspath(path.expanduser()))
    _assert_outside_claude_dir(lexical_path)
    physical_path = lexical_path.resolve(strict=False)
    _assert_outside_claude_dir(physical_path)
    return physical_path


def create_store(path: Path) -> sqlite3.Connection:
    """Create (if needed) and open the SQLite store, applying the full DDL.

    Idempotent: safe to call on an already-initialized store file.

    Raises:
        StoreSchemaError: If the file already holds tables written by a
            different `SCHEMA_VERSION`. The DDL is `CREATE TABLE IF NOT
            EXISTS`, so it would leave those tables untouched and every
            later query would fail on a missing column.
    """
    store_path = _canonicalize_store_path(path)
    _create_private_directory(store_path.parent)
    _create_or_validate_private_file(store_path)
    _validate_existing_sidecars(store_path)

    conn = sqlite3.connect(store_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        _assert_schema_version(conn, store_path)
        with conn:
            conn.executescript(_DDL)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        _validate_existing_sidecars(store_path)
    except BaseException:
        conn.close()
        raise
    return conn


def assert_readable_schema_version(conn: sqlite3.Connection, path: Path) -> None:
    """Reject a store whose schema predates or postdates `SCHEMA_VERSION`.

    For read-only callers, which cannot stamp a version themselves.

    Raises:
        StoreSchemaError: If the store's version does not match.
    """
    _assert_schema_version(conn, path)


def _assert_schema_version(conn: sqlite3.Connection, path: Path) -> None:
    version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    if version == SCHEMA_VERSION:
        return
    # An unstamped store is only fresh if nothing has been written yet;
    # otherwise it predates the stamp and carries the old table shapes.
    if version == 0 and not _has_any_table(conn):
        return
    raise StoreSchemaError(
        f"store at {path} was built by schema version {version}, "
        f"but this build requires {SCHEMA_VERSION}. The store is a disposable "
        f"cache with no migration path: delete it and re-run ingest to rebuild."
    )


def _has_any_table(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' LIMIT 1"
    ).fetchone()
    return row is not None


def _create_private_directory(path: Path) -> None:
    missing: list[Path] = []
    candidate = path
    while not candidate.exists():
        missing.append(candidate)
        if candidate.parent == candidate:
            break
        candidate = candidate.parent

    for directory in reversed(missing):
        with suppress(FileExistsError):
            directory.mkdir(mode=PRIVATE_DIRECTORY_MODE)

    _validate_private_directory(path)


def _create_or_validate_private_file(path: Path) -> None:
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, PRIVATE_FILE_MODE)
    except FileExistsError:
        _validate_private_file(path, label="store database")
        return
    os.close(fd)
    _validate_private_file(path, label="store database")


def _validate_existing_sidecars(path: Path) -> None:
    for suffix in _SQLITE_SIDECAR_SUFFIXES:
        sidecar = Path(f"{path}{suffix}")
        if sidecar.exists():
            _validate_private_file(sidecar, label="SQLite sidecar")


def _validate_private_directory(path: Path) -> None:
    try:
        info = path.stat()
    except OSError as exc:
        raise StoreLocationError(f"cannot inspect store directory {path}: {exc}") from exc
    if not stat.S_ISDIR(info.st_mode):
        raise StoreLocationError(f"store parent is not a directory: {path}")
    _validate_owner_and_mode(
        path,
        info=info,
        required_owner_bits=PRIVATE_DIRECTORY_MODE,
        label="store directory",
    )


def _validate_private_file(path: Path, *, label: str) -> None:
    try:
        info = path.stat()
    except OSError as exc:
        raise StoreLocationError(f"cannot inspect {label} {path}: {exc}") from exc
    if not stat.S_ISREG(info.st_mode):
        raise StoreLocationError(f"{label} is not a regular file: {path}")
    _validate_owner_and_mode(
        path,
        info=info,
        required_owner_bits=PRIVATE_FILE_MODE,
        label=label,
    )


def _validate_owner_and_mode(
    path: Path,
    *,
    info: os.stat_result,
    required_owner_bits: int,
    label: str,
) -> None:
    getuid = getattr(os, "getuid", None)
    if getuid is not None and info.st_uid != getuid():
        raise StoreLocationError(f"{label} is not owned by the current user: {path}")

    mode = stat.S_IMODE(info.st_mode)
    if mode & 0o077 or mode & required_owner_bits != required_owner_bits:
        required = f"{required_owner_bits:o}"
        raise StoreLocationError(
            f"{label} must have owner-only permissions ({required}): {path} has {mode:o}"
        )
