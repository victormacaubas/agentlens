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
| 3 | LLM judge (pluggable, `claude -p` backend, rubric v1) | Planned |
| 4 | Design system for the HTML report | Planned |
| 5 | Renderers (markdown, JSON, terminal, HTML) | Planned |

See [`docs/agentlens-design.md`](docs/agentlens-design.md) for the full design.

## Install & run

agentlens requires Python 3.12 or newer and uses
[uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run agentlens --help
```

Install a checkout as a command-line tool:

```bash
uv tool install .
agentlens --help
```

## Usage

Analyze one subagent transcript:

```bash
agentlens session --file ~/.claude/projects/<project>/<session>/subagents/agent-<id>.jsonl
```

Ask for the same result as JSON when you want to send it to another tool:

```bash
agentlens session --file /path/to/agent-<id>.jsonl --format json
```

Build a deterministic report over one time window. Add `--agent` to restrict
the report to one agent type:

```bash
agentlens report --since 7d
agentlens report --window this-week --agent implementer
agentlens report --from 2026-08-01T00:00:00-03:00 --to 2026-08-08T00:00:00-03:00
```

Both commands accept `--store <path>` to choose the SQLite cache location and
`--dryrun` to calculate the result without writing the store or an artifact.
With `--format json`, they send machine-readable output to standard output and
diagnostics to standard error.

## How it works

```text
.claude/projects/**/*.jsonl              session and subagent transcripts
.claude/projects/**/subagents/*.jsonl    subagent activity
.claude/agents/**/*.md                   agent definitions
                                           |
                                           v
                                      parse and store
                                           |
                                           v
                            deterministic session and report output
```

The primary fact has one row per subagent spawn, never one row per agent type.
It includes tool activity, files touched, errors, permission denials, duplicate
tool calls, timing, token usage, agent-definition evidence, and skill signals.

## Limitations

- `session` requires an explicit `--file` path; stored-session lookup by ID is
  not available.
- The current commands report measured facts. They do not run a model judge.
- The store is a cache, not a system of record. The source logs remain in
  `.claude/`.

## Development

Local development uses uv and a project virtual environment. Run the quality
gate before opening a pull request:

```bash
make check
```

`make integration` invokes the real `claude` CLI. It requires authentication and
can incur usage costs.

Non-trivial work uses [OpenSpec](https://github.com/Fission-AI/OpenSpec). See
[`CLAUDE.md`](CLAUDE.md) for the contributor workflow and project conventions.

## License

[MIT](https://opensource.org/licenses/MIT)
