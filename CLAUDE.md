## What this project is

agentlens reads Claude Code session logs (JSONL under `~/.claude/projects/`) and turns them into deterministic facts, LLM-judged scores, and actionable fix proposals for custom subagents. The full design lives in [`docs/agentlens-design.md`](docs/agentlens-design.md); read it before making structural changes.

## Workflow

### Non-trivial changes go through OpenSpec

For any non-trivial change — a new capability, a schema change, cross-cutting work, anything with more than a trivial single-file edit — **use OpenSpec**. Propose the change (`proposal.md`, `design.md`, spec deltas, `tasks.md`) before implementing, implement against the approved tasks, then archive.

```bash
openspec list                                   # active changes
openspec validate <change-name> --strict        # validate before/after work
openspec archive <change-name>                   # archive once complete
```

Trivial, low-risk edits (a typo, a one-line fix) can skip the ceremony — but when in doubt, propose.

### Python follows python-engineering-standards

All Python — services, CLIs, parsing, tests — follows the `python-engineering-standards` skill. Invoke it building python code.

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
