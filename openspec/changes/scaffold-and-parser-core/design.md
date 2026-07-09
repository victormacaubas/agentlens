## Context

`agentlens` reads Claude Code session logs (JSONL under `~/.claude/projects/`) and turns them into scored, actionable agent findings. The full architecture is four stages — parse → score → render — over a dimensional SQLite store (see `docs/agentlens-design.md`). This change delivers the first two phases together:

- **Phase 0**: repo scaffold, CLI skeleton, SQLite schema DDL, verdict-JSON shape stub.
- **Phase 1**: the deterministic parser core (no LLM) that populates `fact_tool_event` + `dim_agent` from real logs.

They are combined because Phase 0 ships no standalone value and shares the SQLite schema with Phase 1; splitting them would define the schema twice.

**Verified against real data** on this machine (`~/.claude/projects/`): 115 subagent runs each with a matching `.meta.json`, 126 main sessions. Confirmed present in the logs: `thinking`/`text`/`tool_use`/`tool_result` content blocks; `usage` token fields; `tool_result.is_error` and `tool_result.tool_use_id`; `toolDenialKind` (e.g. `"permission-rule"`); `attributionAgent`; flat agent defs in `~/.claude/agents/`. The meta shape matches the doc: `{"agentType","description","toolUseId","spawnDepth"}`.

**Constraints:** Python + `uv` (locked in the doc). Read-only against `~/.claude/`. Standard-library-first. Follows `python-engineering-standards`.

## Goals / Non-Goals

**Goals:**
- A `uvx agentlens`-runnable CLI with `session` and `report` subcommands wired end-to-end (stubs OK).
- SQLite store created from DDL on first run; all tables defined for schema stability.
- Parser populates `fact_tool_event` and `dim_agent` from real subagent + main session logs.
- Parent lineage (path-based + `.meta.json` pairing) and the guarded name-resolution fallback chain, recording `name_source`.
- Idempotent ingest: upsert by `session_id`, re-runs add no duplicates.
- Main sessions ingested as `session_kind = main` (stored, not scored).

**Non-Goals:**
- `fact_session` derivation and `bridge_session_skill` population → **Phase 2**.
- Any LLM judge / `fact_verdict` population → **Phase 3**.
- Renderers (HTML/markdown/JSON reports), windows, deltas → **Phase 2/5**.
- Strict JSON-schema/row-content assertions against real subagent logs → **v2** (per user direction).
- Main-session *scoring* → **v2**.

## Decisions

### D1 — Combine Phase 0 + Phase 1 in one change
Phase 0 is pure scaffolding with no shippable value and defines the schema that Phase 1 fills. Keeping them together gives one coherent, testable deliverable ("point it at a real log → correct store") and avoids writing the schema spec twice. *Alternative (rejected):* two separate changes — more ceremony, a throwaway intermediate state.

### D2 — Define all tables now, populate only two
DDL for `fact_tool_event`, `fact_session`, `dim_agent`, `dim_date`, `dim_tool`, `bridge_session_skill`, `fact_verdict` is created on first run, but this change only *populates* `fact_tool_event` and `dim_agent`. This freezes the schema contract early so Phases 2–3 add rows without migrations. *Alternative (rejected):* create tables lazily per phase — invites migration churn and schema drift between phases.

### D3 — `fact_tool_event` is the finest grain; derive the rest later
Store one row per `tool_use`/`tool_result` pair. `fact_session` is an aggregation of this and is deferred to Phase 2. Pair `tool_use` → `tool_result` via `tool_result.tool_use_id` (confirmed present). This keeps Phase 1 a clean "raw events land correctly" deliverable.

### D4 — Name resolution: guarded fallback chain, resolved once per session
Order (authoritative first): (1) `.meta.json` `agentType`; (2) `attributionAgent` from assistant records (distinct values); (3) parent `Task` tool `subagent_type` via `spawn_tool_use_id`; (4) `agent_id` hash (last resort — never drop a session). Record the winning source in `name_source`; conflicting values flag `ambiguous`. *Alternative (rejected):* parse `attributionAgent` first — the doc calls `.meta.json` authoritative and it is present for all modern spawns.

### D5 — Parent lineage from filesystem path + meta
`parent_session_id` = the `<sid>` folder the `subagents/` dir sits under (path-derived). `spawn_tool_use_id` = `.meta.json` `toolUseId`, joining to the parent's `Task` block. No transcript scanning needed for lineage.

### D6 — Idempotency via upsert by `session_id`
Store is append-only truth: `INSERT ... ON CONFLICT(session_id) DO UPDATE` (or delete-then-insert of a session's events within a transaction) so re-running a window adds only genuinely new sessions. Events are re-derived per session, never duplicated.

### D7 — Testing scope (per user direction)
TDD on logic we own: schema DDL creation, store upsert/idempotency, name-resolution fallback (fed plain dicts), CLI wiring, and the parse pipeline end-to-end.

**Tests are synthetic-only. No test reads the real `~/.claude` tree.** Every fixture is hand-built under `tmp_path` (a synthetic `.claude`-shaped tree, a hand-authored JSONL transcript, plain dicts). This is the hard rule, and it exists because an earlier draft's phrase "smoke-run against real input" was read as license to ingest real logs — tests were written with a machine-pinned path into `~/.claude/projects` and content assertions on real subagent JSON. Both are prohibited:
- **No real-log reads at test time** (no `Path.home() / ".claude"` in tests; the machine-pinned, "point at `~/.claude`" approach was explicitly rejected — it is non-deterministic and not CI-portable). *Exception:* asserting the default store-path resolution logic (which references `~/.cache/...`) is fine — that computes a path, it does not read logs.
- **No strict JSON-schema / row-content assertions against real subagent logs** — deferred to v2 while the parser shape settles.

*Rationale:* real fixtures carry proprietary content, aren't reproducible across machines/CI, and the exact schema is still firming up; synthetic fixtures give deterministic red-green and slot cleanly into v2's stricter suite. Manual verification against real logs (a one-off `agentlens session --file <real log>` by the developer) is fine; it just never becomes an automated test.

### D8 — CLI framework
Use `click` for ergonomic subcommands and testable `CliRunner`, or stdlib `argparse` if we want zero deps. Lean `click` (clean subcommand groups, standard in the ecosystem, trivial to test). *Alternative:* `typer` — nice but heavier; `argparse` — zero-dep but more boilerplate.

### D9 — Store location
Default `~/.cache/agentlens/agentlens.db`, overridable via env var / flag. Never write inside `.claude/`.

## Risks / Trade-offs

- **JSONL schema drift across Claude Code versions** → Parse defensively (`.get()` with defaults, skip unknown record types); the deferred-testing decision means we don't lock to a shape prematurely. Revisit strict validation in v2.
- **Deferring content assertions hides parser bugs** → Accept for now. Coverage comes from synthetic transcripts exercising each parse rule (per D7), and Phase 2 (which reads these rows) will expose semantic errors. A one-off manual run against a real log confirms it doesn't crash on real shapes, but that stays manual — not an automated test. Keep parser functions small and pure so v2 tests slot in.
- **Name-resolution ambiguity** → Never drop a session (hash fallback); flag `ambiguous` for later human/judge review rather than guessing.
- **Empty/malformed sidecars or truncated JSONL** → Treat as best-effort: a missing `.meta.json` falls through the resolution chain; a malformed line is skipped, not fatal.
- **Schema frozen too early (D2)** → Mitigated by grounding DDL in the design doc's fully-specified star schema and real data verification; low risk of a large miss.

## Open Questions

- **CLI dep**: `click` vs stdlib `argparse` — leaning `click` (D8); confirm during scaffold.
- **Store path config surface**: env var name (`AGENTLENS_STORE`?) and/or `--store` flag — settle in scaffold.
