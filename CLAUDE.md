## What this project is

agentlens reads Claude Code session logs (JSONL under `~/.claude/projects/`) and turns them into deterministic facts, LLM-judged scores, and actionable fix proposals for custom subagents. The full design lives in [`docs/agentlens-design.md`](docs/agentlens-design.md); read it before making structural changes.

## Project structure

`src/agentlens/` is organized by responsibility. The package root holds only `cli.py`, `errors.py`, and `__init__.py` — everything else lives in a named module inside a subpackage:

- **`errors.py`** — all custom exception classes (`WindowResolutionError`, `StoreLocationError`, `JudgeError`, `JudgeTimeoutError`, `JudgeUnavailableError`). Import exceptions from here, not from the subpackage that raises them.
- **`discovery/`** — find files on disk (main sessions, subagent runs, agent definitions under `.claude/`). No parsing, no I/O beyond `Path`/`glob`.
  - `filesystem.py` — filesystem discovery functions (`discover_main_sessions`, `discover_subagent_runs`, `discover_agent_defs`, `discover_available_skills`)
  - `models.py` — result dataclasses (`MainSessionFile`, `SubagentRun`, `AgentDefFile`)
- **`parser/`** — turn raw JSONL records into structured facts, split by concern: `extraction.py` (tool-event pairing, transcript facts), `name_resolution.py` (subagent name resolution), `session.py` (session assembly, agent-definition frontmatter parsing).
- **`store/`** — SQLite schema/DDL, store-path resolution, record dataclasses, and upserts. The only subpackage allowed to touch the database.
  - `schema.py` — DDL, `create_store`, `resolve_store_path`, path constants
  - `models.py` — frozen dataclasses (`ToolEventRecord`, `AgentDefRecord`, `SessionRecord`, `SkillBridgeRecord`)
  - `operations.py` — all upsert/query functions
- **`ingest/`** — orchestration: resolves a CLI target, then calls into `parser` to parse it and `store` to persist it. Also owns the bulk `ingest_all` walk over `projects/**`. Cross-cutting by design — don't fold it into `discovery/`, `parser/`, or `store/`.
  - `orchestrator.py` — `IngestRunner` class (owns connection + caches for bulk runs), `resolve_target`, `ingest_target`, `persist_parsed_session`, `ingest_all`
- **`judge/`** — LLM judge layer (Phase 3): pluggable scoring interface, `claude -p` subprocess backend, rubric definition, transcript view preparation, and the scoring loop with verdict persistence.
  - `protocol.py` — `DimensionScore` and `Verdict` frozen dataclasses, `Judge` Protocol (single `score()` method)
  - `transcript_view.py` — `build_transcript_view(parsed, jsonl_path)` produces a ~10-12KB structured text from a session for the judge to score
  - `rubric.py` — `RUBRIC_VERSION` (manual semver), `RUBRIC_PROMPT_TEMPLATE`, `VERDICT_JSON_SCHEMA`, `DIMENSION_NAMES`
  - `claude_cli.py` — `ClaudeCliJudge` class (subprocess backend using `claude -p` headless mode)
  - `scoring.py` — `ScoringLoop` class (find unscored sessions, call judge, persist verdicts), `ScoringResult` dataclass
- **`aggregation/`** — derive the `fact_session` per-spawn grain from parsed sessions: event-derived tool counts joined with transcript-read usage/turn/duration, the `n_duplicate_tool_calls` rule, and the declared-vs-fired skill bridge. Reads facts; emits counts and booleans, never verdicts ([ADR 0003](docs/adr/0003-deterministic-layer-emits-counts-not-verdicts.md)).
  - `derivation.py` — `derive_fact_session`, `count_duplicate_tool_calls`, `derive_skill_bridge`
- **`reporting/`** — windowed rollups over the store: window resolution, prior-window deltas, low-volume guard, intra-session parent lens, and the deterministic verdict-JSON slice. Reads the store only; never ingests.
  - `date_window.py` — `WindowRange` dataclass, `resolve_window`, date-parsing helpers
  - `queries.py` — `build_report`, aggregate dataclasses (`AgentAggregate`, `ParentLensRow`, `AgentWindowResult`, `ReportResult`)
  - `rendering.py` — `render_terminal_summary`

New code that doesn't fit an existing folder (Phase 3 judge/rubric, Phase 5 renderers) gets its own subpackage when that phase actually starts — don't pre-create empty folders for phases that haven't landed yet.

**Editing or creating any `.py` file under `src/agentlens/` → invoke the `python-engineering-standards` skill first.**

## Workflow

### Non-trivial changes go through OpenSpec

For any non-trivial change — a new capability, a schema change, cross-cutting work, anything with more than a trivial single-file edit — **use OpenSpec**. Propose the change (`proposal.md`, `design.md`, spec deltas, `tasks.md`) before implementing, implement against the approved tasks, then archive.

```bash
openspec list                                   # active changes
openspec validate <change-name> --strict        # validate before/after work
openspec archive <change-name>                   # archive once complete
```

Trivial, low-risk edits (a typo, a one-line fix) can skip the ceremony — but when in doubt, propose.

### Local development uses uv with a virtual environment

Use `uv` with the project `.venv` for everything. Never system Python, bare `python`/`python3`, or asdf shims.

```bash
uv sync                  # create/refresh .venv and install deps (incl. dev group)
uv run agentlens --help  # run the CLI from the venv
uv run pytest            # tests
uv run ruff check        # lint
uv run mypy              # type-check (strict)
```

Adding a dependency means declaring it in `pyproject.toml` and running `uv sync` — never `pip install` into the venv directly.

## Test structure

Tests live under `tests/unit/` (all current tests are synthetic-only unit tests). pytest discovers them via `testpaths = ["tests"]` in `pyproject.toml`.

```
tests/
├── __init__.py
└── unit/
    ├── __init__.py
    ├── test_aggregation.py
    ├── test_claude_cli.py
    ├── test_cli.py
    ├── test_discovery.py
    ├── test_ingest.py
    ├── test_judge_protocol.py
    ├── test_parser.py
    ├── test_reporting.py
    ├── test_rubric.py
    ├── test_score_cli.py
    ├── test_scoring.py
    ├── test_store.py
    └── test_transcript_view.py
```

When integration tests arrive (real filesystem, live `claude -p` calls), they'll go in `tests/integration/` with a `@pytest.mark.integration` marker.

## Module conventions

- **`__init__.py` is always empty (0 bytes).** Never put code in `__init__.py`. Every function, class, and constant lives in a named module.
- **Import from the named module**, not the package root: `from agentlens.store.operations import upsert_session`, not `from agentlens.store import upsert_session`.
- **Custom exceptions live in `errors.py`** at the package root. Don't define exception classes inside subpackage modules.
- **Stateful orchestration uses a class** (e.g., `IngestRunner`). Pass dependencies in `__init__`, hold caches as instance state. Stateless transforms stay as free functions.
- **Use `contextlib.closing()`** for `sqlite3.Connection` lifecycle in CLI commands, not bare `try/finally`.

## Project guardrails

- **Read-only against `.claude/`.** agentlens reads `~/.claude/projects/**` and `.claude/agents/**` but never writes into any `.claude/` directory. The SQLite store lives under `~/.cache/agentlens/` (override via `--store` or `$AGENTLENS_STORE`).
- **Tests are synthetic-only** (see [`docs/adr/0001-synthetic-only-tests.md`](docs/adr/0001-synthetic-only-tests.md)). Every fixture is hand-built under `tmp_path`; **no test reads the real `~/.claude` tree**, and there are no strict content assertions against real subagent logs (deferred to v2). If a test needs `~/.claude`, it is wrong.
- **Measured vs. modeled stay separate.** Deterministic facts (`fact_tool_event`, `fact_session`, `dim_*`) and LLM verdicts (`fact_verdict`) never mix in the same table.
- **The spawn is the unit.** The primary fact grain is one row per agent run (per spawn), keyed by the per-spawn `agent_id` — never deduped by `agent_type`.

## Architectural decisions

Decisions that bind future changes are recorded as ADRs in [`docs/adr/`](docs/adr/) (Nygard format: Status, Context, Decision, Consequences). Before archiving an OpenSpec change, promote any decision in its `design.md` that constrains future work to an ADR.

## Quality gate

Before considering a change done, all three must be green:

```bash
uv run pytest
uv run ruff check
uv run mypy
```

## Commits

Don't commit or push unless asked.
