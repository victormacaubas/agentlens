## Why

Phase 1 populates `fact_tool_event` and `dim_agent`, but the session grain (`fact_session`), the skill bridge, and every aggregate table are still empty DDL. `ParsedSession` already resolves identity and lineage, then the CLI logs it and throws it away — nothing persists it. And the only ingest path is one file at a time, so there is no corpus to aggregate over. Phase 2 turns the parsed facts into the deterministic signals and rollups that `report --since 7d` needs, with no LLM involved — roughly the next 20% of the tool's value and the foundation the Phase 3 judge reads from.

## What Changes

- **Populate `fact_session`, one row per spawn.** Derive tool counts (`n_reads`, `n_edits`, `n_bash`, `n_files_touched`, `n_errors`, `n_permission_denials`) by aggregating `fact_tool_event`, and read usage/turn/duration fields (`input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_creation_tokens`, `n_turns`, `duration_sec`) **directly from the transcript** — token usage lives on assistant records' `message.usage` (per turn, summed), not on tool events. This is a deliberate, documented exception to "store `fact_tool_event`, derive the rest."
- **The deterministic layer emits counts and booleans, never verdicts.** `n_retry_loops` is **renamed `n_duplicate_tool_calls`** (session-wide count of repeated `tool_name`+`input_hash`, with a pinned rule) because real transcripts show zero consecutive-identical calls — a loop detector would be a dead signal. `claimed_status` is **demoted** from an authoritative complete/partial verdict to a raw boolean marker (or dropped); "did it really finish" is the judge's honesty axis, not a deterministic fact.
- **Populate `bridge_session_skill`** (declared / available / fired). `fired` is the union of two strong, near-disjoint signals: the `isMeta:true` skill-injection marker (`<skill-format>true` + `<command-name>`, catches auto-injected skills) and the `Skill` tool_use (catches explicitly-invoked skills). A skill can fire without being declared, so fired-but-not-declared rows are expected and useful.
- **Populate `dim_date` and `dim_tool`** as conformed dimensions for cheap slicing.
- **Add a dedicated `ingest` command** that walks `projects/**`, resolves every main session and subagent run, and upserts each idempotently — the honest way to build a corpus. `report` reads the store; it never ingests on read.
- **Implement `report --since|--agent`** for real: windows (`7d|30d|<date>`, `--from/--to`), prior-window deltas, a low-volume guard (suppress trend arrows below `min_sessions_for_trend`, default 5), N counted as **spawns not parent sessions**, and an intra-session parent lens. Output is the **deterministic slice of the verdict JSON** — no scores yet.

## Capabilities

### New Capabilities

- `session-aggregation`: derive and persist the `fact_session` per-spawn grain — event-derived tool counts plus transcript-read usage/turn/duration — and populate the `dim_date` / `dim_tool` conformed dimensions.
- `skill-usage-bridge`: populate `bridge_session_skill` with declared / available / fired, where `fired` is the union of the skill-injection marker and the `Skill` tool_use.
- `windowed-reporting`: `report` produces real deterministic numbers over a window — prior-window deltas, low-volume guard, spawns-not-sessions counting, intra-session parent lens — emitting the deterministic slice of the verdict JSON.

### Modified Capabilities

- `session-parser`: extract per-turn `message.usage`, turn count, session duration, and skill-fire signals from transcripts (Phase 1 captured only tool events).
- `store-schema`: rename `n_retry_loops` → `n_duplicate_tool_calls`; demote `claimed_status` from complete/partial to a raw marker (or drop it).
- `cli-scaffold`: add the `ingest` subcommand (bulk `projects/**` walk); promote `report` from a stub to real deterministic aggregation.

## Impact

- **Code:** new aggregation module (`fact_session` derivation, windows/deltas); new `store` upserts and record dataclasses for `fact_session`, `bridge_session_skill`, `dim_date`, `dim_tool`; `parser` extraction extended for usage/turns/skill-fires; `cli.py` gains `ingest`, fills in `report`; `store` DDL column rename (fresh store — no migration path needed, store is a disposable cache under `~/.cache/agentlens/`).
- **Contracts:** the deterministic verdict-JSON slice becomes the stable shape Phase 3 layers scores onto.
- **Decisions:** two ADR candidates — the `fact_session` direct-read exception, and "the deterministic layer emits counts, not verdicts."
- **No new runtime dependencies.** Read-only against `.claude/` unchanged; store stays under `~/.cache/agentlens/`.
