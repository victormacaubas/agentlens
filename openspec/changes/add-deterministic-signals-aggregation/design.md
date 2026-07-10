## Context

Phase 1 landed `fact_tool_event` (one row per tool_use/tool_result pair) and `dim_agent` (scanned agent definitions). `ParsedSession` already resolves per-spawn identity, lineage, and name, but the CLI logs it and discards it — `fact_session`, `bridge_session_skill`, `dim_date`, and `dim_tool` are empty DDL. The only ingest path is `agentlens session <id|--file>`, one transcript at a time.

Phase 2 (per `docs/agentlens-design.md` §8) derives the session grain, populates the bridge and dimensions, adds bulk ingest, and makes `report` produce real deterministic numbers with no LLM. Design decisions below were settled in an explore session against the real `sample-data/` corpus (10 subagent transcripts, 6 main sessions).

Grounding facts from that corpus:
- Token/turn/duration data lives on assistant records' `message.usage`, **per turn** (summed across turns), not on tool events. Cache-read dominates (e.g. one run: 52,696 cache-read vs 1 input token on its final turn).
- Every transcript ends `stop_reason: end_turn` — even ones that reported partial/blocked work. `stop_reason` is useless as a completion signal.
- **Zero** consecutive-identical tool calls across all 10 transcripts; session-wide duplicate `(tool_name, input_hash)` pairs are rare (0–5) and mostly legitimate (re-Read after Edit).
- Skill fires split across two near-disjoint signals: `isMeta:true` injection marker (5 auto-injected fires) and `Skill` tool_use (1 explicit fire); `SKILL.md` reads are noisy and did not resolve to clean skill names.
- Transcripts range 66KB–854KB; usage aggregation must stream, not slurp-and-hold.

## Goals / Non-Goals

**Goals:**
- Persist `fact_session` per spawn: event-derived tool counts + transcript-read usage/turn/duration.
- Populate `bridge_session_skill` (declared / available / fired) and the `dim_date` / `dim_tool` conformed dimensions.
- Add an `ingest` command that walks `projects/**` and upserts every session idempotently.
- Make `report` real: windows, prior-window deltas, low-volume guard, spawns-not-sessions counting, intra-session parent lens — emitting the deterministic slice of the verdict JSON.
- Keep the deterministic layer free of verdicts (counts and booleans only).

**Non-Goals:**
- No LLM judge, scores, or `fact_verdict` (Phase 3).
- No renderers beyond terminal + the JSON deterministic slice — no HTML/markdown report design (Phases 4–5).
- No main-session *scoring* (v2); main sessions are aggregated into `fact_session` but carry no lineage.
- No store migration tooling — the store is a disposable cache; a column rename is applied by recreating the schema.

## Decisions

### D1 — `fact_session` is NOT a pure rollup of `fact_tool_event`

The design doc's "store `fact_tool_event`, derive the rest" cannot hold: usage/turn/duration are not tool-event facts. `fact_session` derivation reads two sources:
- **From `fact_tool_event` (already persisted):** `n_tool_calls`, `n_reads`, `n_edits`, `n_writes`, `n_bash`, `n_files_touched`, `n_errors`, `n_permission_denials`, `n_duplicate_tool_calls`.
- **From the transcript directly:** `n_turns` (count of assistant records), `input_tokens` / `output_tokens` / `cache_read_tokens` / `cache_creation_tokens` (sum of `message.usage` across assistant turns), `duration_sec` (last ts − first ts), `n_skills_fired`, `task_prompt_len`.

**Alternative considered:** add token columns to `fact_tool_event`, or a new `fact_turn` grain, to keep `fact_session` a pure aggregation. Rejected — tokens are a turn-level fact; smearing them across tool events is dishonest, and a `fact_turn` table is scope Phase 2 does not need. We accept the exception and document it (ADR candidate).

**Consequence:** the parser must return usage/turn/duration on `ParsedSession` (Phase 1 returns only events + identity). The aggregation step joins event-derived counts with these transcript-read fields.

### D2 — The deterministic layer emits counts and booleans, never verdicts

- **`n_retry_loops` → `n_duplicate_tool_calls`.** Rule: within a session, count occurrences of each `(tool_name, input_hash)` beyond the first (session-wide, not consecutive). Consecutive-only would be all-zero on real data. This is a raw count handed to the judge, not a "stuck" verdict.
- **`claimed_status` demoted.** It cannot be deterministically set (every transcript ends `end_turn`). Replace the complete/partial column with a raw boolean `final_report_flagged_partial` — true iff the final assistant text block matches a small fixed marker set (`partial`, `blocked`, `couldn't`, unchecked `- [ ]`). The judge owns the real completion verdict.

**Alternative considered:** keep `claimed_status` and set it by heuristic. Rejected — it reads as authoritative when it is a guess, violating measured-vs-modeled separation. (ADR candidate.)

### D3 — `bridge_session_skill.fired` = union of two strong signals

`fired` is true if **either**:
1. an `isMeta:true` record's text contains `<skill-format>true` and a `<command-name>NAME</command-name>` (auto-injected skills — declared or hooked), **or**
2. a `Skill` tool_use names the skill in its input (explicitly invoked).

Both resolve the skill **name**, which is the join key for the bridge. `SKILL.md` reads are **not** used for `fired` (noisy; demoted). Rows are keyed `(session_id, skill_name)`:
- `declared` — skill is in the session's agent's `dim_agent.declared_skills`.
- `available` — skill is present under `.claude/skills/**` (incl. plugins). Best-effort; false when the skills tree is unreadable.
- `fired` — the union above.

A skill can fire without being declared (injection), so the row set is the **union** of declared and fired skills, not just declared ones.

### D4 — Dedicated `ingest` command; `report` reads the store

`agentlens ingest [--claude-home] [--limit N]` walks `projects/**`, resolves every main session and subagent run via existing discovery, parses each, and upserts `fact_session` + `fact_tool_event` + `bridge_session_skill`. Idempotent by `session_id` (per-spawn `agent_id` for subagents). `dim_date` / `dim_tool` are backfilled from the ingested rows. `report` never ingests — it reads the store only, so a stale store yields stale numbers by design (the store is append-only truth).

**Alternative considered:** `report` ingests-on-read. Rejected — hides cost, couples read to write, and fights the append-only-store model.

### D5 — Windows, deltas, and guards computed in SQL over the store

- Window flags: `--since 7d|30d|<date>`, `--from/--to`, `--agent <type>`. Resolve to a `[start, end)` date range; filter `fact_session` via `dim_date`.
- **Prior-window delta:** self-join against the immediately preceding equal-length span.
- **Low-volume guard:** below `min_sessions_for_trend` (default 5 spawns), show raw values + N but suppress delta arrows, labeled "insufficient data."
- **N counts spawns, not parent sessions** — every aggregate is labeled in spawns; `task_description` distinguishes same-type spawns in detail views.
- **Intra-session parent lens:** group spawns by `parent_session_id` to roll up "this session fanned out N subagents; M failed, K hit denials."
- Output: the deterministic slice of the verdict JSON (per-session counts + window rollups), plus a thin terminal summary. No scores.

## Risks / Trade-offs

- **Transcript re-read cost.** Subagent aggregation re-reads the parent transcript for name resolution; a parent with many spawns is re-parsed per sibling. → `ingest` parses each parent once and reuses `extract_task_subagent_types` (the cheap path already built in Phase 1); aggregate siblings against the cached parent map.
- **Usage schema drift.** `message.usage` field names (`cache_read_input_tokens`, `cache_creation_input_tokens`) are provider-shaped and may vary. → read defensively (`.get`, default 0); a missing field yields 0, never a crash. Sum only integer values.
- **`available` accuracy.** Detecting a skill under `.claude/skills/**` including plugin trees is best-effort. → default `available=0` when unresolved; never block a row on it. The judge treats `available` as advisory.
- **Column rename with an existing store.** Renaming `n_retry_loops` breaks an old store file. → the store is a disposable cache; document that Phase 2 requires a fresh store (delete the cache file or use a new `--store`), no migration script.
- **`final_report_flagged_partial` false signal.** Keyword matching mislabels. → it is explicitly a raw marker, not a verdict; the judge is the authority. Keep the marker set small and documented.

## Migration Plan

Store is a cache under `~/.cache/agentlens/`. The `n_retry_loops` → `n_duplicate_tool_calls` rename ships as a DDL change; existing cache files are recreated on next run (or the user deletes the file / points `--store` at a fresh path). No data migration — Phase 1 stores are re-ingestable from `.claude/` at any time.

## Open Questions

- Marker set for `final_report_flagged_partial` — finalize the exact keyword/regex list during implementation against the corpus; keep it conservative.
- Whether `dim_date` should span the full observed range or only dates with sessions — lean "only dates present" for now; a report never needs empty date rows in Phase 2.
