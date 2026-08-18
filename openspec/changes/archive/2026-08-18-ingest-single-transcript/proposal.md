## Why

The repository has an architecture and a quality gate but no behavior: nothing
reads a transcript, nothing writes a row, nothing prints a result. Every
constraint the baseline declared is currently unexercised, which means none of it
is proven.

This change is the tracer bullet. It takes the narrowest useful path through all
five layers so the wiring is exercised from the first commit rather than
discovered to be wrong after three layers exist. Point it at one subagent
transcript and it produces a populated store and a machine-readable report.

## What Changes

- **New `agentlens session --file <path>` command.** Reads one
  `agent-<agentId>.jsonl` and its `.meta.json` sidecar, persists them, and
  renders a report. Adds the `[project.scripts]` console-script entry, which the
  baseline deliberately left out because there was no `main` to point it at.
- **Qualified session identity.** Derives `session_id` as the SHA-256 of
  `(source_project, session_kind, raw_session_id)`, with `source_project` read
  from the file's position under `.claude/projects/<project>/`. The raw tuple is
  retained for display, since the derived key is not human-readable.
- **Snapshot soundness.** Captures a source revision of
  `(mtime_ns, size, content_hash)` before the read and verifies it after, so a
  file that changed mid-read is rejected rather than half-ingested.
- **`fact_tool_event` at one row per tool invocation.** The `tool_use` and its
  matching `tool_result` join into a single row. An unmatched `tool_use` becomes
  a row with null result fields, because an abandoned call is a health signal
  about the run rather than a parse failure.
- **`fact_session` at one row per spawn**, derived in `ingest` alongside the
  events. This resolves the one decision ADR 0001 deferred to the first change
  that needed it.
- **Two-link name resolution.** `.meta.json` `agentType` is authoritative; an
  `agent_id` hash is the last resort so a session is never dropped. Which link
  won is recorded in `name_source`.
- **Upsert by `session_id`.** Re-running the command replaces a grain only with a
  sound, non-stale snapshot, so repeated runs neither duplicate nor regress data.
- **JSON artifact and terminal summary.** JSON to `reports/session_<id>.json` or
  stdout under `--format json`; a human summary otherwise. The JSON carries one
  typed row per qualified spawn and is explicitly marked unscored.
- **First implementation of the `Clock` seam**, with its fake, giving that
  Protocol the two implementations ADR 0004 requires of it.

Deliberately out of scope, each its own later slice: filesystem discovery under
`.claude/projects/**`, `main` sessions, `dim_agent` and agent-definition
scanning, `bridge_session_skill`, parent lineage through `spawn_tool_use_id`,
name-resolution links 2 and 3, windowed reporting, and the LLM judge.
`JudgeBackend` therefore stays declared and unimplemented; that is expected, not
an oversight.

No new runtime dependency. `click` is already declared and gets its first use
here.

## Capabilities

### New Capabilities

- `session-command`: the `agentlens session` CLI surface. Argument forms,
  validation, the mapping from error family to exit code, and what the command
  guarantees about never writing into `.claude/`.
- `session-parser`: turning one transcript plus its sidecar into typed rows.
  Qualified identity derivation, snapshot soundness, tool-invocation pairing,
  session derivation, and the name-resolution chain.
- `store-schema`: the SQLite schema for `fact_tool_event` and `fact_session`,
  their grains and keys, and the upsert and staleness rules that make re-runs
  safe.
- `session-report`: the JSON artifact and terminal summary for a single session.
  Field set, stream discipline, and the unscored marker.

### Modified Capabilities

None. `openspec/specs/` is currently empty, so every capability here is new and
this change establishes the flat kebab-case organization.

## Impact

- **Code**: first behavior in `cli`, `core`, `ingest`, `store`, `render`, plus
  hashing in `utils` and the first concrete `Clock`. `models` gains the row
  types for the two facts.
- **Packaging**: `[project.scripts]` gains `agentlens = "agentlens.cli:main"`,
  making `uvx agentlens` work for the first time.
- **Filesystem**: creates `reports/` in the working directory and a SQLite file
  under the store path. Reads `.claude/` and never writes to it.
- **Contracts**: exercises all five import contracts against real imports for the
  first time. `sqlite3` must stay inside `store`, `click` inside `cli`, and
  `ingest` must not reach `judge` or `store`.
- **Tests**: establishes `tests/factories.py` as the synthetic-JSONL builder and
  `tests/fakes.py` with `FakeClock`. Per ADR 0006 these fixtures are synthetic,
  so what the factory believes about Claude Code's format is what every test
  believes.
- **Risk**: the format belief above is unverified against real data. The
  mitigation inside this change is that snapshot soundness and name resolution
  both degrade rather than crash on unexpected input.
