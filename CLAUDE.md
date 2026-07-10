## What this project is

agentlens reads Claude Code session logs (JSONL under `~/.claude/projects/`) and turns them into deterministic facts, LLM-judged scores, and actionable fix proposals for custom subagents. The full design lives in [`docs/agentlens-design.md`](docs/agentlens-design.md); read it before making structural changes.

## Project structure

`src/agentlens/` is organized by responsibility. Keep only `cli.py` and `__init__.py` at the package root — everything else lives in a subpackage:

- **`discovery/`** — find files on disk (main sessions, subagent runs, agent definitions under `.claude/`). No parsing, no I/O beyond `Path`/`glob`. `models.py` holds its result dataclasses (`MainSessionFile`, `SubagentRun`, `AgentDefFile`); import them from `agentlens.discovery.models`, not the package root.
- **`parser/`** — turn raw JSONL records into structured facts, split by concern: `extraction.py` (tool-event pairing, transcript facts), `naming.py` (subagent name resolution), `session.py` (session assembly, agent-definition frontmatter parsing).
- **`store/`** — SQLite schema/DDL, store-path resolution, record dataclasses, and upserts. The only subpackage allowed to touch the database.
- **`ingest/`** — orchestration: resolves a CLI target, then calls into `parser` to parse it and `store` to persist it. Cross-cutting by design — don't fold it into `discovery/`, `parser/`, or `store/`.

New code that doesn't fit an existing folder (Phase 2 aggregation, Phase 3 judge/rubric, Phase 5 renderers) gets its own subpackage when that phase actually starts — don't pre-create empty folders for phases that haven't landed yet.

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
