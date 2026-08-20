## 1. Project scaffold & tooling

- [x] 1.1 Initialize `uv` project: `pyproject.toml` with `src/agentlens/` layout, `requires-python`, project metadata for PyPI
- [x] 1.2 Configure `[project.scripts]` entry point `agentlens = "agentlens.cli:main"` so `uvx agentlens` / `pipx install agentlens` resolve
- [x] 1.3 Add dev tooling per python-engineering-standards: pytest, ruff, mypy; configure in `pyproject.toml`
- [x] 1.4 Choose CLI dependency (click, per design D8) and add it; commit `uv.lock`
- [x] 1.5 Verify `uv run agentlens --help` executes and `uv run pytest` collects (empty) without error

## 2. Store schema & DDL (TDD)

- [x] 2.1 Write test: `create_store(path)` produces a SQLite file containing all required tables (`fact_tool_event`, `fact_session`, `dim_agent`, `dim_date`, `dim_tool`, `bridge_session_skill`, `fact_verdict`)
- [x] 2.2 Implement `agentlens/store.py` DDL + `create_store()`; make test pass
- [x] 2.3 Implement store location resolution (default `~/.cache/agentlens/agentlens.db`, override via env var / `--store` flag; never inside `.claude/`); test default + override
- [x] 2.4 Define `fact_tool_event` columns (`session_id`, `seq`, `tool_name`, `is_error`, `denial_kind`, `ts`, `input_hash`, `output_bytes`) and `dim_agent` columns (`agent_type` key, `name`, `model`, `effort`, `declared_tools`, `declared_skills`, `definition_hash`)
- [x] 2.5 Document the verdict-JSON shape stub (per-dimension scores, overall, evidence, fixes, judge-cost fields) as a module constant / docstring contract — not populated

## 3. Discovery

- [x] 3.1 Implement `agentlens/discovery.py`: find main sessions (`projects/**/*.jsonl`), subagent runs (`projects/**/<sid>/subagents/agent-*.jsonl`), and pair each with its `.meta.json` sidecar
- [x] 3.2 Implement agent-def discovery under `.claude/agents/**` — flat (`<name>.md`) and nested (`<name>/<name>.md`), project- and user-level
- [x] 3.3 Unit test discovery against synthetic `.claude`-shaped trees under `tmp_path` (main sessions, subagent+meta pairing, flat/nested agent defs, missing-dir empties) — no real-log dependency

## 4. Parser core

- [x] 4.1 Implement `agentlens/parser.py`: read a JSONL transcript defensively (`.get()` defaults, skip malformed lines / unknown record types)
- [x] 4.2 Pair `tool_use` → `tool_result` via `tool_result.tool_use_id`; emit `fact_tool_event` rows ordered by `seq` with `is_error`, `denial_kind` (from `toolDenialKind`), `ts`, `input_hash`, `output_bytes`
- [x] 4.3 Resolve parent lineage: `parent_session_id` from path `<sid>`, `spawn_tool_use_id` from sidecar `toolUseId`
- [x] 4.4 Tag `session_kind` (`subagent` | `main`); main sessions stored without lineage
- [x] 4.5 Parse agent defs into `dim_agent` rows with a stable `definition_hash`

## 5. Name resolution (TDD)

- [x] 5.1 Write test (plain dicts, no real logs): fallback chain returns meta `agentType` first, then `attributionAgent`, then parent `Task` `subagent_type`, then `agent_id` hash; asserts correct `name_source` each time
- [x] 5.2 Write test: conflicting distinct names flag `ambiguous`; missing everything still resolves via hash (session never dropped)
- [x] 5.3 Implement `resolve_name()` in `agentlens/parser.py`; make tests pass; resolve once per session

## 6. Persistence & idempotency (TDD)

- [x] 6.1 Write test: ingesting the same session twice yields exactly one set of rows (upsert by `session_id`, transactional per-session)
- [x] 6.2 Write test: a second run with a new session adds only the new session, leaving prior rows unchanged
- [x] 6.3 Implement upsert/idempotent write path in `agentlens/store.py`; make tests pass

## 7. CLI wiring & pipeline (TDD)

- [x] 7.1 Write test (click `CliRunner`): `agentlens --help` lists `session` and `report`; both subcommands exit 0 when wired
- [x] 7.2 Implement `agentlens/cli.py` with `session` (accepts `<id>` or `--file`) and `report` (stub, accepts `--agent`/`--since`) subcommands
- [x] 7.3 Wire `session` to the parse pipeline: ensure store exists → discover/parse target → persist; report missing input with non-zero exit and no partial write
- [x] 7.4 Write test: empty pipeline (no sessions) creates a valid store with all tables and exits 0

## 8. Integration smoke & verification

- [x] 8.1 Integration test the full `session` pipeline (discover → parse → persist) against a synthetic subagent transcript under `tmp_path`; assert `fact_tool_event` rows land with resolved parent lineage
- [x] 8.2 Integration test ingest of both a synthetic subagent run and a synthetic main session without exceptions (real-log validation deferred to v2 per D7)
- [x] 8.3 Run full quality gate: `uv run ruff check`, `uv run mypy`, `uv run pytest` all green
- [x] 8.4 Run `openspec validate scaffold-and-parser-core --strict` and confirm the change validates
