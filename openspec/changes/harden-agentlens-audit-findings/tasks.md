## 1. Identity and Schema Foundations

- [x] 1.1 Add synthetic failing tests for cross-project and cross-kind raw-ID collisions, qualified parent lineage, and ambiguous raw-ID lookup.
- [x] 1.2 Add a named source-identity model and deterministic qualified-session-key helper carrying source project, session kind, and raw ID.
- [x] 1.3 Extend discovery, parser, store record, and aggregation models with qualified identity, raw identity, source revision, judge-input hash, and effective definition identity.
- [x] 1.4 Rebuild the disposable-cache DDL with qualified session columns, normalized file-path hashes, versioned agent definitions, input-bound verdict identity, scoring claims, and report-window indexes.
- [x] 1.5 Validate all event and skill child identities before a session-grain replacement mutates any row, with rollback tests for mismatches.

## 2. Versioned and Resilient Ingest

- [x] 2.1 Replace full JSONL materialization with an incremental record reader that computes content hash and parse-health counters while bounding retained payload state.
- [x] 2.2 Capture source stat metadata before and after parsing, classify changed-during-read input, and return a stable source revision with each parsed session.
- [x] 2.3 Add transactional source-revision comparison so degraded, changed-during-read, or stale snapshots cannot replace a newer committed grain.
- [x] 2.4 Make main-session and subagent discovery lazy, isolate root and per-project `OSError`, and include failed paths in the ingest summary.
- [x] 2.5 Apply the ingest limit through lazy selection so later projects are not enumerated after the target budget is reached.
- [x] 2.6 Make raw-ID resolution collect every match and report project/kind ambiguity instead of selecting the first result.
- [x] 2.7 Add one complete ISO timestamp parser with the documented naive-time policy and UTC normalization; use it for duration and preserve other facts on malformed input.
- [x] 2.8 Add regression tests for malformed re-ingest, stale-writer order, active-file mutation, unreadable/disappearing directories, mixed timezones, and large transcript memory bounds.

## 3. Agent Definition and Skill Attribution

- [x] 3.1 Preserve definition scope and source-project identity when parsing and storing user and project agent definitions.
- [x] 3.2 Store every definition hash as a version and resolve the effective definition with project-before-user precedence.
- [x] 3.3 Bind each ingested subagent session to its effective definition version and derive declared skills from that binding.
- [x] 3.4 Add synthetic tests for same-named user and multi-project definitions, definition updates over time, and project-correct skill bridge rows.

## 4. Deterministic Aggregation Corrections

- [x] 4.1 Extract and normalize file-addressing tool paths into a dedicated hash while retaining whole-input hashes for duplicate-call detection.
- [x] 4.2 Count distinct files from path hashes and add tests for repeated reads with different offsets, edits with different content, malformed inputs, and distinct paths.
- [x] 4.3 Derive `session_date` from the shared complete timestamp parser in UTC and test malformed suffixes and offset crossings at UTC midnight.

## 5. Bounded Transcript Views

- [x] 5.1 Refactor prepared-view extraction into a streaming reducer with bounded pending tool pairs, bounded task/final text, counters, and deterministic tool samples.
- [x] 5.2 Give Errors & Denials an explicit byte budget that retains total count, stable head/tail step references, bounded excerpts, and a truncation marker.
- [x] 5.3 Add a final UTF-8 whole-document byte gate that preserves all six headers and never exceeds `VIEW_MAX_BYTES`.
- [x] 5.4 Add boundary tests for hundreds of errors, multibyte text, tens of thousands of tool calls, multi-megabyte result bodies, and oversized fixed sections.

## 6. Verdict Validation and Safe Diagnostics

- [x] 6.1 Create one backend-independent verdict validation/factory boundary for exact dimensions, score types and bounds, locally derived overall, known fix enums, and concrete model identity.
- [x] 6.2 Enforce evidence and suggested-fix count and length limits at runtime for Claude and custom judge backends.
- [x] 6.3 Validate judge cost and token fields as finite non-negative numbers, rejecting booleans, NaN, infinity, negative values, and malformed types as `JudgeError`.
- [x] 6.4 Route every model-controlled exception value through a bounded excerpt or structural summary helper.
- [x] 6.5 Add tests proving invalid custom verdicts, oversized model output, malformed accounting, and long private sentinels cannot be persisted or dumped into logs.

## 7. Input-Bound and Concurrent Scoring

- [x] 7.1 Hash the exact prepared view, persist that hash as the session's current judge input, and query verdict cache hits by session, input hash, rubric, and concrete model.
- [x] 7.2 Verify the session's current input hash during verdict finalization so concurrent re-ingest rejects stale in-flight scores.
- [x] 7.3 Implement atomic scoring-claim acquisition, ownership, expiry, release, finalization, and crashed-owner recovery.
- [x] 7.4 Refactor alias resolution to continue past session-specific candidate failures, preserve unavailable-judge propagation, and stop only on exhaustion or the systemic failure threshold.
- [x] 7.5 Track one attempt budget across alias resolution and post-resolution scoring, with stable ordering and accurate scored/skipped/aborted totals.
- [x] 7.6 Add tests for changed-input cache invalidation, stale finalization, two-process duplicate-spend prevention, claim expiry, first-candidate failure, alias movement, and repeated capped-run progression.

## 8. Comparable and Complete Reporting

- [x] 8.1 Add a typed per-spawn report row and emit scored and unscored qualified sessions in the deterministic JSON slice.
- [x] 8.2 Require or deterministically resolve one rubric version and concrete judge model for modeled report output; expose that cohort in `ReportResult`.
- [x] 8.3 Join verdicts on session, current input hash, rubric, and concrete model, and derive score averages only from the selected one-row-per-spawn cohort.
- [x] 8.4 Update terminal and JSON rendering for qualified/raw identity and per-session rows without changing `n_spawns_with_errors` naming.
- [x] 8.5 Add report tests for multiple rubrics, multiple models, reversed insertion order, mixed scored/unscored sessions, same-type spawns, and aggregate-to-session reconciliation.
- [x] 8.6 Add query-plan tests on a large synthetic store and tune the report indexes until window and parent-lens queries avoid full `fact_session` scans.

## 9. CLI and Store Boundary Hardening

- [x] 9.1 Make ingest and scoring limits positive Click ranges and prove invalid values perform no store or judge work.
- [x] 9.2 Remove cap and alias orchestration from the CLI, delegate it to the scoring loop, and render attempt, skip, abort, remaining-work, cost, and resolved-model state.
- [x] 9.3 Return non-zero status after partial ingest, skipped scoring, or scoring abort while preserving successful rows and useful summaries.
- [x] 9.4 Translate expected store, judge availability, filesystem, and SQLite failures into concise Click errors with original causes retained.
- [x] 9.5 Read `agentlens --version` from distribution metadata and restore every package `__init__.py` to zero bytes.
- [x] 9.6 Canonicalize store paths through symlinks, reject physical `.claude` ancestors, and create or validate database, directory, and sidecar owner-only permissions.
- [x] 9.7 Add CLI and store tests for partial exit codes, capped failure summaries, actionable domain errors, version consistency, symlink escape attempts, and permissive umasks.

## 10. Documentation and Verification

- [x] 10.1 Update `docs/agentlens-design.md` for qualified identities, versioned definitions, source revisions, input-bound verdicts, claims, and explicit report cohorts.
- [x] 10.2 Record binding architectural decisions from `design.md` as ADRs before archiving the change.
- [x] 10.3 Re-run each audit artifact's acceptance scenarios and mark all 31 Critical, High, and Medium findings fixed or document any deliberate deferral.
- [x] 10.4 Run `uv run pytest`, `uv run ruff check`, and `uv run mypy`; resolve every regression before completion.
- [x] 10.5 Run `openspec validate harden-agentlens-audit-findings --strict` and verify all tasks, specs, design decisions, and audit traceability agree before implementation handoff.
