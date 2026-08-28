# Architecture

## What this is

agentlens is a local, read-only developer CLI. It reads Claude Code session logs
out of the user's `.claude/`, persists them in a dimensional SQLite store, scores
subagent runs with an LLM judge, and renders findings across four output
surfaces. It is distributed on PyPI and invoked as `uvx agentlens`.

Python 3.12 floor, `src/` layout, two runtime dependencies: `click` at the
entrypoint and `jinja2` in the renderers, where autoescaping is a security
control rather than a convenience. Everything else is standard library:
`sqlite3` for persistence, `subprocess` for the judge, `dataclasses` for domain
types. The set is closed, and ADR 0002 records what was excluded and why.

The tool never writes into `.claude/`. Reads only, always.

## Shape

Four stage packages, one per stage of the pipeline, sit under an orchestration
layer.

`ingest` discovers transcripts under `.claude/`, parses the JSONL, and derives
whole fact rows, including snapshot revision and agent name resolution. `store`
owns all SQLite access and every line of SQL over the star schema. `judge` owns
the `claude -p` backend: argv construction, the rubric, the prepared transcript
view, and verdict validation. `render` owns the terminal, markdown, JSON, and
HTML surfaces, and is the boundary where untrusted model output gets marked and
escaped.

Those four are siblings and mutually independent, which is the enforceable form
of the product's "measured versus modeled, kept separate" rule: a deterministic
fact can never be computed from model output, and a verdict cannot reach the
store except by way of `core`. Every cross-stage flow therefore passes through
`core`, which sequences calls (parse, persist, read back, render) and resolves
windows. `core` orchestrates; it does not compute. `cli` parses arguments, maps
exit codes, and wires the program together. `models` and `utils` sit at the
bottom holding domain types, Protocols, and leaf helpers, with no in-project
dependencies of their own.

A package that owns an external technology owns its types, so nothing
`sqlite3`-shaped leaves `store`, nothing `subprocess`-shaped leaves `judge`, and
nothing `jinja2`-shaped leaves `render`. ADR 0001 holds the dependency
direction; `CLAUDE.md` holds the may-import table that machine-enforces it.

## Identity

The unit of analysis is the **spawn**. One row of `fact_session` is one agent
run, never one agent type: four `implementer` spawns inside a parent session are
four rows, which is why every surface that shows a count says spawns.

Raw Claude Code IDs are not globally unique across projects or session kinds, so
the natural key is derived: `session_id = SHA-256(source_project, session_kind,
raw_session_id)`, with all three components retained as columns because a hash
cannot be eyeballed in a display or an error message. Beneath it,
`fact_tool_event` is one row per tool invocation, with the `tool_use` and its
matching `tool_result` joined at parse time; an unmatched use survives as a row
with null result fields, because that is a health signal about the run rather
than a parse failure. `fact_verdict` is one row per scored identity: session,
prepared-input hash, rubric version, and the concrete `judge_model` resolved
from the response envelope rather than the alias typed at the CLI.

Re-running upserts, never duplicates. A grain is replaced only by a snapshot
proven sound against a `(mtime_ns, size, content_hash)` revision captured before
and verified after the read, so a stale or torn read cannot overwrite a newer
sound grain. The store is a disposable cache: nothing lands in it that cannot be
regenerated from `.claude/`, and that property is what makes hand-written SQL
and rebuild-instead-of-migrate affordable.

## Seams

Two, both declared as Protocols in `models/protocols.py`. `JudgeBackend`,
because scoring crosses a process boundary, costs money, and is
nondeterministic, and because an API-key backend for CI would go behind the same
Protocol. `Clock`, because time is needed in `core`, `store`, and `render`, and
threading one `now` argument down through three layers is the tell that a value
wants injecting instead.

Injection is required, never defaulted, so a test that forgets to inject fails
loudly instead of quietly constructing a real, paid judge. `cli.py` is the only
composition root; nothing below it constructs its own collaborators.

The filesystem, the store, and `subprocess` were each considered and rejected as
seams. ADR 0004 carries the rule that decides membership, and a new candidate
gets judged by that rule rather than by resemblance to these two.

## Failure model

Everything raised deliberately inherits from `AgentlensError`. Four families sit
under it, `ConfigError`, `SourceError`, `StoreError`, and `JudgeError`, mapping
to exit codes 2, 3, 4, and 5. Success is 0, and anything that escapes the
taxonomy is 1. Those codes are a public contract, because scripts wrapping the
tool branch on them.

Each package translates foreign exceptions at its own boundary: `store` catches
`sqlite3.Error`, `judge` catches process and JSON decoding failures, `ingest`
catches `OSError` and malformed records. The signal that this has eroded is
`cli.py` catching a driver exception, at which point that driver has become part
of the CLI's own contract. Exit-code mapping lives in exactly one place in
`cli.py`, never per command.

Not every failure is an exception. A stale snapshot is a decision to skip a
replacement, so it is a return value.

## Decisions

| ADR | What it decides |
|---|---|
| [0001. Layer map and dependency direction](adr/0001-layer-map-and-dependency-direction.md) | Package boundaries, one-way dependency flow, and the four stage packages as independent siblings. Open this before adding a package or moving code across one. |
| [0002. Runtime stack and dependencies](adr/0002-runtime-stack-and-dependencies.md) | The closed runtime dependency set, each library's owning package, and the exclusions (no ORM, no pydantic, no dataframes, no HTTP client). Open this before adding any third-party import. |
| [0003. Identity and grain](adr/0003-identity-and-grain.md) | What one row is at each grain, the derived and qualified session key, verdict cache identity, and upsert-on-rerun. Open this for anything touching keys, schema, or counts. |
| [0004. Seams](adr/0004-seams.md) | Which dependencies are injected Protocols, why the filesystem and store are not, and where concrete implementations get wired. Open this before adding a Protocol or reaching for a mock. |
| [0005. Error taxonomy and translation boundaries](adr/0005-error-taxonomy-and-translation-boundaries.md) | The exception families, their exit codes, and which package translates which foreign exception. Open this before raising or catching anything. |
| [0006. Testing approach](adr/0006-testing-approach.md) | Factories, fakes, injection over patching, and synthetic-only fixtures with the drift risk that comes with them. Open this before adding shared test infrastructure. |
| [0007. Toolchain and quality gate](adr/0007-toolchain-and-quality-gate.md) | The single gate command, the import contracts that encode ADRs 0001 and 0002, strict typing over `src` and `tests`, and why a new contract must be proven to fail before it is trusted to pass. Also why passing the gate is not the same as being done. |
| [0008. Session row derivation](adr/0008-session-row-derivation.md) | `fact_session` rows are derived wholly in Python inside `ingest`, never partly in SQL. Open this before adding a derived session field or considering a backfill query. |
| [0009. Judge invocation bounds and model resolution](adr/0009-judge-invocation-bounds-and-model-resolution.md) | The hardened `claude -p` invocation as verified against a real CLI: `--settings` for auth under `--bare`, `--max-budget-usd` in place of the nonexistent `--max-turns`, why the timeout and spend ceiling live on `ClaudeCliJudge` rather than the `JudgeBackend` Protocol, and why the resolved model is read from `modelUsage` rather than the requested alias. Open this before touching the invocation or re-verifying it against a new CLI release. |
