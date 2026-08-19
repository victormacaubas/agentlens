## 1. Enrich one subagent session end to end

- [x] 1.1 Extend the canonical test factories with keyword-only builders for the new session context and derivation values; keep existing defaults compatible with current tests.
- [x] 1.2 Add failing schema and round-trip tests for `agent_id`, nullable `agent_definition_id`, `parent_session_id`, `started_at`, `task_prompt_len`, `n_skills_fired`, derivation fingerprint, and derivation observation time.
- [x] 1.3 Add the typed model fields and derive raw agent identity, qualified parent identity, earliest usable timestamp, and task-prompt length for one transcript.
- [x] 1.4 Extend the ordered store declarations, generated SQL, row extractors, and named-row reconstruction for the new fields without changing existing column semantics.
- [x] 1.5 Add a derivation identity that keeps transcript revision separate while hashing every shaping input and recording the newest shaping-input observation time.
- [x] 1.6 Update staleness handling so a newer changed derivation refreshes context, an identical derivation is skipped, and an older derivation leaves the complete stored snapshot untouched.
- [x] 1.7 Prove `agentlens session --file` still round-trips the expanded row and preserves its existing output and exit behavior.
- [x] 1.8 Run `make check`.

## 2. Catalog agent definitions and bind only provable history

- [x] 2.1 Add synthetic definition fixtures covering user scope, project scope, scalar and list frontmatter forms, unknown keys, malformed known fields, and changed content.
- [x] 2.2 Implement the bounded frontmatter reader for name, model, effort, tools, and skills with sound stat-read-stat revisions and source-specific errors.
- [x] 2.3 Add typed definition identity, scope, revision, and configuration values plus a content-addressed identifier.
- [x] 2.4 Discover user and project definitions and resolve project-over-user precedence for each source project.
- [x] 2.5 Add `dim_agent` DDL, ordered rows, upsert/read operations, and schema-pin tests without adding an ORM or migration layer.
- [x] 2.6 Bind a spawn only when the effective observed definition is no newer than `started_at`; otherwise persist an unknown definition identity.
- [x] 2.7 Add tests proving a definition edit creates a new content identity, re-evaluates old spawns to unknown, and remains reproducible after a store rebuild.
- [x] 2.8 Run `make check`.

## 3. Derive session-skill signals vertically

- [x] 3.1 Add synthetic fixtures for declared skills, user/project/plugin availability, `Skill` tool invocations, each supported injected-skill marker, repeated fires, and `SKILL.md` reads without firing.
- [x] 3.2 Add a three-state model for declared and available (`true`, `false`, `unknown`) and a boolean fired state at `(session_id, skill_name)` grain.
- [x] 3.3 Discover sound skill inventories from user, project, and installed plugin skill directories, retaining revisions needed for conservative historical claims.
- [x] 3.4 Implement isolated firing-evidence parsing; pin supported marker shapes and ensure ordinary `SKILL.md` reads never count as firing.
- [x] 3.5 Derive the bridge from the union of applicable declarations and firing evidence, resolve availability for each resulting row, and derive `n_skills_fired` from distinct fired rows. Scope the skill-inventory derivation input to the skills in that narrowed set, so editing an unrelated skill cannot invalidate a spawn.
- [x] 3.6 Add `bridge_session_skill` DDL, ordered rows, atomic per-session replacement, named reads, and schema-pin tests.
- [x] 3.7 Add end-to-end tests for declared-only, declared-but-unproven-availability, fired-only, unknown historical state, repeated firing, and reingest with changed evidence.
- [x] 3.8 Run `make check`.

## 4. Complete deterministic name and parent resolution

- [x] 4.1 Parse distinct assistant attribution values and parent `Task` evidence without emitting a main-session row. Cover: one attribution value, conflicting attribution values, a parent `Task` subagent type, an unavailable parent transcript.
- [x] 4.2 Implement the ordered sidecar, attribution, parent-task, and raw-agent fallback chain with an explicit ambiguous outcome. Cover: every fallback link, conflicting sources, missing parent input.
- [x] 4.3 Include every name-resolution input in the derivation fingerprint, so a changed parent or sidecar refreshes only the affected derived facts.
- [x] 4.4 `make quick T=tests/unit/test_ingest_parsing.py tests/unit/test_ingest_derivation.py`

## 5. Discover and batch-ingest all subagent sources

- [x] 5.1 Implement deterministic discovery of subagent source bundles under project trees, excluding project-level main-session transcripts. Cover: multi-project trees carrying transcripts, sidecars, definitions and skills; duplicate raw IDs that must not collide once qualified; a changed-during-read source; main-session JSONL present but never ingested.
- [x] 5.2 Parse all bundles and context before opening the persistent write transaction; abort on any hard source failure while retaining counted unreadable-line behavior.
- [x] 5.3 Add a store batch operation applying definitions, sessions, tool events, and skill rows in one transaction, preserving the existing per-session staleness outcomes. Cover: mid-batch parse failure and mid-batch database failure each write nothing, unchanged sources do not duplicate, four same-type spawns remain four rows.
- [x] 5.4 Assert no path under `.claude/` changes across successful, failed, and dry-run discovery.
- [x] 5.5 `make quick`

## 6. Resolve report windows

- [x] 6.1 Add typed window-selector and resolved-window models: current and prior UTC bounds, original selector, local timezone metadata, trend threshold.
- [x] 6.2 Implement relative-duration, named-calendar, and explicit-range resolution, deriving the previous equal-elapsed-duration window, and reject zero, negative, malformed, or unsupported durations as configuration errors. Cover, against an injected fixed clock: `7d`, explicit `--from/--to`, `this-week`, half-open boundaries, local timezone conversion, a daylight-saving transition.
- [x] 6.3 Add a testable report-argument parser requiring exactly one selector form, pairing `--from` with `--to`, and supporting `--agent`, `--store`, `--format json`, `--dryrun`. Cover: invalid selector combinations exit 2 and write neither the store nor report files.
- [x] 6.4 `make quick`

## 7. Query current and prior deterministic aggregates

- [x] 7.1 Add canonical builders for typed spawn rows, metric totals, per-spawn averages, weighted proportions, trend status, and agent rollups.
- [x] 7.2 Add a store read returning every qualifying current-window subagent spawn in deterministic order, with the optional agent filter. Cover: lower and upper window boundaries, same-type spawns from several parents, main rows excluded, unknown context, zero results.
- [x] 7.3 Add analytical SQL for current and prior agent populations, totals, per-spawn averages, and weighted cache-read proportion without joining verdict data, plus signed deltas when both windows meet the threshold, retaining raw values and returning `insufficient_data` otherwise. Cover: current-only agents, prior-only agents, totals never driving directional trends, an empty current window returning empty typed collections.
- [x] 7.4 `make quick T=tests/unit/test_store.py`

## 8. Deliver the deterministic report path

- [ ] 8.1 Add a versioned report document model: generation metadata, selector and resolved bounds, filter, threshold, complete spawn rows, agent rollups.
- [ ] 8.2 Render that model as JSON at a stable scope-derived artifact path that overwrites in place, leaving the existing session artifact path unchanged. Cover: every qualifying spawn appears, unknown context stays explicit, low-volume trends carry no direction, no modeled field appears anywhere.
- [ ] 8.3 Add a thin terminal summary naming the window, agent scope, total spawns, per-agent populations, trend status, and artifact path, presenting no score.
- [ ] 8.4 Implement the core report workflow (resolve scope, discover and parse, batch-upsert, query both windows, build the document, choose JSON stdout or artifact plus summary) and wire `agentlens report` through the CLI composition root, logging resolved arguments once as JSON with identifying window and agent context. Cover end to end: `report --since 7d` over several subagent transcripts emits real current and prior numbers without constructing a judge; plus JSON-stream, agent-filter, zero-results, stable-overwrite, source-error, store-error, and success exit codes.
- [ ] 8.5 `make quick`

## 9. Make dry run use the production query path

- [ ] 9.1 Add a store operation cloning an existing SQLite cache into a disposable temporary database, initializing an empty clone when no persistent store exists, and closing and removing it on success and every failure path.
- [ ] 9.2 Route `report --dryrun` through the clone, applying the validated batch and the same analytical reads and renderers as a normal report. Cover: normal and dry-run JSON documents match apart from generation and path metadata for the same starting store and sources; dry run includes newly discovered spawns while leaving the configured store and report paths byte-for-byte unchanged.
- [ ] 9.3 `make quick`

## 10. Verify the Phase 2 boundary and finish

- [ ] 10.1 Rebuild a store twice from the same synthetic source tree and prove all added definitions, session context, skill rows, spawn rows, and aggregates are equivalent.
- [ ] 10.2 Prove a changed sidecar, definition, skill inventory, or parent evidence updates the derivation fingerprint, while an older composite snapshot cannot overwrite newer stored facts.
- [ ] 10.3 Confirm the report path neither ingests main-session rows nor constructs or calls `JudgeBackend`, and that new SQLite-shaped signatures stay inside `store` and source-tree types stay inside `ingest`.
- [ ] 10.4 Run `openspec validate report-subagent-deterministic-signals --strict --json`.
- [ ] 10.5 Run the `structure-review` skill against the completed change; resolve every blocking finding and re-review before archive.
- [ ] 10.6 Run `make check` and confirm a clean gate.
