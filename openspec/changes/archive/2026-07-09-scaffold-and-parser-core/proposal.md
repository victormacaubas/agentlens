## Why

`agentlens` needs a foundation before it can score anything: a runnable CLI, a source-of-truth store, and a parser that turns raw Claude Code session logs into structured deterministic facts. Phase 0 (scaffold + contracts) alone ships no user value — it is pure setup that shares the SQLite schema with Phase 1 — so the two are proposed together. Phase 1 is where the design doc says "~60% of the value ships": point the tool at a real `agent-*.jsonl` and get a correctly populated store with parent lineage resolved, no LLM required.

## What Changes

- **New `uv`-managed Python package** distributable via `uvx agentlens`, with a CLI exposing `session` and `report` subcommands as stubs (wired end-to-end, no analysis logic yet).
- **SQLite dimensional store** created from DDL on first run: `fact_tool_event`, `fact_session`, `dim_agent`, `dim_date`, `dim_tool`, `bridge_session_skill`, and `fact_verdict`. All tables are defined now for schema stability; only `fact_tool_event` and `dim_agent` are *populated* by this change. The rest are created empty (populated in Phase 2 / Phase 3).
- **Verdict-JSON shape** defined as a documented contract stub (consumed by Phase 3), so downstream phases build against a fixed shape.
- **Deterministic parser core** that discovers and ingests:
  - main sessions (`projects/**/*.jsonl`) as `session_kind = main` (stored, not scored)
  - subagent runs (`projects/**/<sid>/subagents/agent-*.jsonl`) + their `.meta.json` sidecars
  - agent definitions (`.claude/agents/**`, flat and nested, project- and user-level)
- **Parsing into `fact_tool_event` + `dim_agent`**, with path-based parent linkage, `.meta.json` pairing, and the guarded name-resolution fallback chain (meta → attribution → parent Task → agent_id hash), recording `name_source`.
- **Idempotent store writes**: upsert by `session_id`; re-runs add only new sessions, never duplicates.
- Read-only against `~/.claude/`; never writes into it.

## Capabilities

### New Capabilities
- `cli-scaffold`: The `agentlens` command-line entry point, `session`/`report` subcommand skeleton, `uv`/PyPI packaging, and end-to-end empty pipeline that opens the store and exits cleanly.
- `store-schema`: The SQLite dimensional schema (all fact/dim/bridge tables), DDL creation on first run, store location resolution, and the verdict-JSON shape contract stub.
- `session-parser`: Discovery of main sessions, subagent runs + `.meta.json`, and agent defs; parsing into `fact_tool_event` + `dim_agent`; parent lineage, name resolution with `name_source`, `session_kind` tagging, and idempotent upsert.

### Modified Capabilities
<!-- None — greenfield repo, no existing specs. -->

## Impact

- **New code**: Python package (`src/agentlens/`), CLI module, store/DDL module, parser module, discovery module.
- **New dependencies**: standard-library-first (`sqlite3`, `json`, `pathlib`, `argparse`/`click`); dev tooling per `python-engineering-standards` (pytest, ruff, mypy).
- **Packaging**: `pyproject.toml` configured for `uvx`/`pipx` distribution.
- **Filesystem**: reads `~/.claude/projects/**` and `.claude/agents/**` (read-only); writes SQLite store under `~/.cache/agentlens/` (configurable).
- **Testing**: TDD on logic we own (schema DDL, store upsert/idempotency, name-resolution fallback given plain dicts, CLI wiring). Tests catch compilation + Python errors and smoke-run the pipeline against real input. Strict JSON-schema/row-content assertions on subagent logs are **deferred to v2**.
- **No breaking changes** — greenfield.
