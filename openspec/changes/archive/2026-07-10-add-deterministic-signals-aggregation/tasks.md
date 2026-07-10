## 1. Store schema & records

- [x] 1.1 Rename `n_retry_loops` → `n_duplicate_tool_calls` and replace `claimed_status` with boolean `final_report_flagged_partial` in the `fact_session` DDL (`store/__init__.py`); note in the module that the store is a disposable cache with no migration path
- [x] 1.2 Add a `SessionRecord` dataclass and `upsert_session(conn, record)` that replaces the row by `session_id`
- [x] 1.3 Add a `SkillBridgeRecord` dataclass and `upsert_session_skills(conn, session_id, records)` (delete-then-insert by `session_id`)
- [x] 1.4 Add `upsert_dim_date` / `upsert_dim_tool` backfill helpers (idempotent inserts)
- [x] 1.5 Confirm `create_store` still applies cleanly on a fresh file after the rename

## 2. Parser: usage, turns, duration, skill signals

- [x] 2.1 Extend `extract_transcript_facts` (or add a sibling) to sum `message.usage` across assistant records defensively (missing fields → 0), returning `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_creation_tokens`
- [x] 2.2 Capture `n_turns` (assistant record count) and `duration_sec` (first-to-last timestamp span)
- [x] 2.3 Extract fired-skill names: `isMeta:true` records with `<skill-format>true` + `<command-name>NAME</command-name>`, and `Skill` tool_use input names
- [x] 2.4 Compute `final_report_flagged_partial` from the final assistant text against a small documented marker set
- [x] 2.5 Extend `ParsedSession` with the new fields (usage, turns, duration, fired skills, partial marker); update `parse_main_session` / `parse_subagent_run`

## 3. Aggregation module

- [x] 3.1 Create `aggregation/` subpackage per CLAUDE.md (new phase → new subpackage)
- [x] 3.2 Implement `derive_fact_session`: join event-derived counts (`n_reads`, `n_edits`, `n_writes`, `n_bash`, `n_files_touched`, `n_errors`, `n_permission_denials`, `n_tool_calls`, `n_duplicate_tool_calls`) with the parser's usage/turn/duration fields into a `SessionRecord`
- [x] 3.3 Implement `n_duplicate_tool_calls` rule: session-wide count of `(tool_name, input_hash)` occurrences beyond the first
- [x] 3.4 Implement `derive_skill_bridge`: union of declared (from `dim_agent.declared_skills`) and fired skills; set `declared` / `available` (best-effort from `.claude/skills/**`) / `fired`
- [x] 3.5 Set `n_skills_fired` on the session from the fired set

## 4. Ingest: bulk command

- [x] 4.1 Add `ingest_all` in `ingest/`: walk `projects/**` via discovery, parse each target, derive + upsert `fact_session`, `fact_tool_event`, `bridge_session_skill`; backfill `dim_date` / `dim_tool`; parse each parent transcript once and reuse its Task map across sibling spawns
- [x] 4.2 Wire the `agentlens ingest` CLI subcommand with `--claude-home` and `--limit N`
- [x] 4.3 Ensure idempotency: a second run over an unchanged tree writes no new rows

## 5. Reporting: windows, deltas, guards

- [x] 5.1 Create `reporting/` subpackage; implement window resolution (`--since 7d|30d|<date>`, `--from/--to`, `--agent`, `--today`) to a `[start, end)` range, defaulting to 7d when no window flag is given
- [x] 5.2 Query `fact_session` for the window (spawns counted, joined via `dim_date`); implement prior-window delta as a preceding equal-length span
- [x] 5.3 Implement the low-volume guard (`min_sessions_for_trend` default 5 → suppress arrows, label "insufficient data")
- [x] 5.4 Implement the intra-session parent lens (group by `parent_session_id`: spawn count, failures, denials)
- [x] 5.5 Emit the deterministic slice of the verdict JSON + a thin terminal summary; ensure `report` reads the store only and never ingests
- [x] 5.6 Replace the `report` stub in `cli.py` with the real implementation

## 6. Tests (synthetic-only) & quality gate

- [x] 6.1 `fact_session` derivation: tool-count aggregation, usage summing across turns, missing-usage tolerance, duration, `n_duplicate_tool_calls` rule
- [x] 6.2 Skill bridge: injection-marker fire, `Skill` tool_use fire, SKILL.md-read does NOT fire, declared-not-fired and fired-not-declared rows
- [x] 6.3 Ingest: bulk populate under `tmp_path`, idempotent re-run, `--limit`, full-grain replace on re-ingest
- [x] 6.4 Reporting: window resolution, prior-window delta, low-volume guard, spawns-not-sessions count, parent lens, report-does-not-ingest
- [x] 6.5 `final_report_flagged_partial` marker matching (positive + clean-completion negative)
- [x] 6.6 Green quality gate: `uv run pytest`, `uv run ruff check`, `uv run mypy`

## 7. Decisions & docs

- [x] 7.1 Promote to ADRs: `fact_session` direct-read exception, and "deterministic layer emits counts not verdicts" (Nygard format under `docs/adr/`)
- [x] 7.2 Update `docs/agentlens-design.md` §3 to reflect the `n_retry_loops`→`n_duplicate_tool_calls` rename and `claimed_status` demotion
- [x] 7.3 Note in CLAUDE.md project structure that `aggregation/` and `reporting/` subpackages now exist
