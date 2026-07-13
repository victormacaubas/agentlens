<div align="center">

  <h1>agentlens</h1>

  <img src="assets/agentlens-logo-v2.png" alt="agentlens cyberpunk lens logo" width="260" />

  <p>Analyze, score, and improve your Claude Code subagents from their session logs.</p>

  [![License](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
  [![Python](https://img.shields.io/badge/Python_3.14-3776AB?logo=python&logoColor=white)](https://www.python.org/)
  [![uv](https://img.shields.io/badge/uv-Package_Manager-DE5FE9?logo=astral&logoColor=white)](https://docs.astral.sh/uv/)
  [![OpenSpec](https://img.shields.io/badge/spec--driven-OpenSpec-000000.svg)](https://github.com/Fission-AI/OpenSpec)
  [![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](http://makeapullrequest.com)

</div>

---

## What it is

You have a growing set of custom subagents (`.claude/agents/*.md`). Today there's no way to see, across many sessions, which agents perform well, which go off-track, and what to fix. Claude Code already records everything as JSONL under `.claude/projects/`. **agentlens** reads that data, scores each subagent run, and produces actionable fix proposals you can hand back to Claude Code.

It is a local CLI that turns raw session logs into:

- a **machine-readable handoff** for Claude to patch your agents, and
- a **well-designed report** for a human to read.

**What it is not:** a cost dashboard, a live monitor, or a hosted service. It runs on demand, locally, and is read-only against your `.claude/` directory.

## Design principles

1. **One data core, many thin renderers.** A dimensional SQLite store is the source of truth; terminal, markdown, HTML, and a future dashboard are all queries over it.
2. **Measured vs. modeled, kept separate.** Deterministic facts (what happened) are immutable and reproducible. LLM verdicts (how good it was) are subjective, versioned, and re-scoreable — they never mix in the same table.
3. **The killer output is the fix, not the score.** A single number invites gaming and hides the *why*. The product is per-session findings plus concrete fixes.

## Status

Early development. **Phases 0–2 are complete**: the CLI scaffold, the full SQLite dimensional schema, the deterministic parser core, and the aggregation layer that derives the per-spawn `fact_session` grain, the declared-vs-fired skill bridge, and windowed `report` output (prior-window deltas, low-volume guards) — all with no LLM. Scoring (the LLM judge) and the rendered reports are upcoming phases.

| Phase | Scope | State |
|---|---|---|
| 0 | Scaffold & contracts (CLI, schema DDL, verdict-JSON shape) | ✅ Done |
| 1 | Deterministic parser core (no LLM) | ✅ Done |
| 2 | Deterministic signals & aggregation (windows, deltas) | ✅ Done |
| 3 | LLM judge (pluggable, `claude -p` backend, rubric v1) | ✅ Done |
| 4 | Design system for the HTML report | Planned |
| 5 | Renderers (markdown, JSON, terminal, HTML) | Planned |

See [`docs/agentlens-design.md`](docs/agentlens-design.md) for the full design.

## Install & run

agentlens is a Python package managed with [uv](https://docs.astral.sh/uv/).

```bash
# Run without installing (once published)
uvx agentlens --help

# Or install for regular use
pipx install agentlens
```

## Usage

```bash
# Analyze a single session — the primitive everything builds on
agentlens session <session-id>
agentlens session --file path/to/agent-<id>.jsonl

# Bulk-ingest every session under ~/.claude/projects into the store
agentlens ingest
agentlens ingest --limit 50            # bound the first run

# Aggregate rollup across sessions in a window (reads the store, never ingests)
agentlens report                        # defaults to the last 7 days
agentlens report --agent implementer --since 30d
agentlens report --today                # just today (≡ --since 1d)
agentlens report --since 7d --json      # deterministic verdict-JSON slice to stdout
```

`ingest` builds the corpus; `report` reads only what's already in the store, so run `ingest` first (and again whenever new sessions appear). The store lives at `~/.cache/agentlens/agentlens.db` by default — override it with `--store <path>` or the `AGENTLENS_STORE` environment variable. agentlens never writes inside any `.claude/` directory.

## How it works

Four stages, each a thin layer over the previous:

```
.claude/projects/**/*.jsonl              (main sessions)     ─┐
.claude/projects/**/<sid>/subagents/
        agent-<agentId>.jsonl            (subagent runs)     ─┤
        agent-<agentId>.meta.json        (spawn sidecar)     ─┼──▶  PARSE  ──▶  store (SQLite)
.claude/agents/**/*.md                   (agent defs)        ─┘         │
                                                                        ├──▶  SCORE  (deterministic + LLM judge)
                                                                        └──▶  RENDER (terminal / markdown / json / html)
```

The store is a star schema that keeps grains separate so windows (date filters) and prior-window deltas (self-joins) stay trivial. The grain of the primary fact is a **single agent run** — one row per spawn, never an agent *type*.

## Development

Local development uses **uv** with a project virtual environment (`.venv`). See [`CLAUDE.md`](CLAUDE.md) for the full contributor workflow.

```bash
uv sync                 # create/refresh the .venv and install deps
uv run agentlens --help # run the CLI from the venv
uv run pytest           # tests
uv run ruff check       # lint
uv run mypy             # type-check (strict)
```

Non-trivial changes are proposed and tracked with [OpenSpec](https://github.com/Fission-AI/OpenSpec); Python follows the project's `python-engineering-standards`. Architectural decisions that bind future work live in [`docs/adr/`](docs/adr/).

## License

[MIT](https://opensource.org/licenses/MIT)
