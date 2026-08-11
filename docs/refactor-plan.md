# agentlens deep refactor plan

Readability and testability refactor across both slices. Behavior-preserving throughout.
Delete this file once the work lands.

---

# RESUME HERE (paused 2026-08-11)

Branch: **`refactor/deep-readability-pass`**. **Nothing is committed.** All work from Phase 0, Slice B,
and Slice C lives in an uncommitted working tree.

## Run this first

```bash
cd /Users/victor-macaubas/Documents/Personal_Projects/agentlens
git branch --show-current     # expect refactor/deep-readability-pass
uv run pytest                 # expect 345 passed, 1 deselected
uv run ruff check             # expect clean
uv run mypy                   # expect clean, 49 source files
```

If that is green, the tree is in the state described below. **If it is not green, Slice D was
interrupted mid-write** (see "In flight" below) and the tree needs reconciling before anything else.

## Hard rules for whoever resumes

1. **Never `git checkout <file>`, `git restore`, `git stash`, or `git reset`.** Nothing is committed,
   so any of them destroys landed work. This already happened once: it wiped `FACT_SESSION_COLUMNS`
   out of `store/schema.py` and the constant had to be reconstructed from the DDL.
2. **Consider committing the landed slices before continuing.** Three slices of unprotected work is
   the single largest risk to this refactor right now. Suggested checkpoints: one commit for Phase 0,
   one for Slice B, one for Slice C plus the guard test.
3. **Do not set `row_factory` on a connection**, only on individual cursors. 32 tests assert rows
   against tuple literals and `sqlite3.Row` is not tuple-equal.
4. Slices are **serial, not parallel.** `cli.py` imports from nearly every subpackage and test files
   import across many, so two concurrent module splits collide on the same importers.

## Landed and verified

| | Result |
|---|---|
| **Phase 0** (H1, H2) | `W` added to ruff select, 2 W293 fixed, all 5 stale plan citations stripped. 0 remain. |
| **Slice B** (S1 + T2) | `judge/process.py` created with `CommandRunner` Protocol + `SubprocessCommandRunner`. `ClaudeCliJudge` now requires `runner` and `claude_home` (both keyword-only, no defaults). `test_claude_cli.py`: 64 `@patch` to **0**. Patches on `subprocess.run`/`shutil.which`: 75 to **0**. Dead `mock_which` params: 36 to **0**. `tests/factories/judge.py` created with `FakeCommandRunner`. |
| **Slice C** (S2) | `FACT_SESSION_COLUMNS` in `store/schema.py` (36 names, DDL order, verified identical to DDL). `store/hydration.py` created with `hydrate_session_record`, `fetch_session_events`, `hydrate_parsed_session`. Positional `row[N]` in src: 89 to **0**. |

Gate at pause: **345 passed, 1 deselected**, ruff clean, mypy clean on 49 source files.

## In flight (status unknown at pause)

**Nothing. Slice D was stopped and its partial output was discarded.** The tree is clean.

For the record, because the failure mode is worth knowing before re-dispatching: Slice D had written
`store/events.py` (69 lines) and `store/definitions.py` (163 lines) as **additive copies** while
leaving all six functions still defined in `operations.py`, which stayed untouched at 663 lines and
was still the only version anything imported. The gate stayed green throughout precisely because the
new modules were dead code, so **green was not evidence the split was progressing correctly.** Both
partial files were deleted (with `rm`, since they were untracked) and the gate re-verified.

**Re-dispatch Slice D from scratch.** Two things to add to its brief:

1. A split is not done until the symbol exists in exactly one place. Require the worker to report, per
   moved symbol, that it is gone from the source module. `grep -c "^def <name>"` on both files.
2. `operations.py` must shrink. Require the before and after line count in the report; if
   `operations.py` is still 663 lines, nothing has actually moved.

Confirmed clean at pause: `src/agentlens/store/` contains `hydration.py`, `models.py`,
`operations.py`, `schema.py`; `src/agentlens/judge/` contains `claude_cli.py`, `process.py`,
`protocol.py`, `rubric.py`, `scoring.py`, `transcript_view.py`.

## Corrections to this plan, discovered during execution

- **The baseline is 344 tests, not 346.** The original figure was a miscount of pytest progress dots.
  It is now 345 because Slice C's review added one guard test. Every "expected count" in a dispatch
  brief must use the live number, not this document's original claim.
- **`sqlite3.Row` is not tuple-equal, and 32 tests depend on tuples.** Not known when the plan was
  written; it is the reason S2 uses per-cursor factories rather than a connection-level one.
- **The `store/operations.py` INSERT and scoring's SELECT both name their columns explicitly**, so the
  S2 hazard is edit-time (a shifted `SELECT` list desyncing 45 positional reads), not DDL-time. The
  S2 section already carries the corrected wording.

## Deviations from the plan that were reviewed and accepted

- **`test_score_cli.py` keeps 6 `@patch` decorators**, against the plan's stated target of 0. They now
  patch `agentlens.cli.SubprocessCommandRunner`, the composition root, because those tests drive the
  CLI through `CliRunner().invoke(main, ...)` so the judge is constructed inside `cli.py`, not by the
  test. This is the same boundary the plan already blesses for `patch("agentlens.cli.create_store")`.
  Zero patches remain on the deep internals, which was the actual goal.
- **The `fact_session` INSERT reads values via `getattr(record, column)`** over `FACT_SESSION_COLUMNS`
  instead of a hand-written tuple. This removed the third hand-maintained column list, but `getattr`
  with a dynamic name is `Any` to mypy, so a `SessionRecord` field rename would surface as a runtime
  `AttributeError`. Accepted **with** a guard:
  `test_every_fact_session_column_resolves_to_a_session_record_field` in `tests/unit/test_store.py`,
  which was confirmed to fail when a column name is deliberately broken. Note the guard asserts **set**
  equality, not order: DDL order and dataclass field order genuinely differ, and that is harmless
  because the INSERT's column list and its values tuple both derive from the same constant.

## New finding, not in the original plan

**`store/schema.py` is now 401 lines** and has crossed the grab-bag threshold, because S2 added the
36-name constant to it. It holds four concerns: the DDL, `FACT_SESSION_COLUMNS`, store-path
resolution, and schema-version assertion. Decide during Slice D or F whether to split it (a candidate
seam: `store/schema.py` for DDL and columns, `store/location.py` for path resolution and version
checks). It was not one of the original seven oversized modules.

## Remaining queue

| Slice | Content | State |
|---|---|---|
| **D** | M1 store split, M2 scoring split | re-dispatch from scratch |
| **E** | M3 extraction, M4 transcript_view, `domain.py`, shared `message_content.py` | pending |
| **F** | M5 reporting, M6 ingest, M7 cli (incl. `ingest_one()`, one summary formatter) | pending |
| **G** | Phase 3 within-module cleanups S3a-e, S5 | pending |
| **H** | T1 shared factories, T3 assertions, T4 empty-report test | pending |
| **Phase 4** | CLAUDE.md amendments C1-C4 | pending, do inline, last |

Slice H extends the **existing** `tests/factories/judge.py` (Slice B created it with
`FakeCommandRunner`); it does not create that file.

---

## Baseline (measured 2026-08-11, before any change)

| Signal | Value |
|---|---|
| `src/agentlens` LOC | 6,035 |
| `tests` LOC | 7,375 |
| Tests passing | 344 (+1 deselected integration canary) |
| `ruff check` / `mypy --strict` | both clean |
| **Modules over 400 lines** | **7 of 20** |
| Functions >= 45 lines | 38 (17 src, 21 tests) |
| `conftest.py` files | 0 |
| `@patch` decorators | 75 (64 `test_claude_cli.py`, 11 `test_score_cli.py`) |
| Dead `mock_which: MagicMock` params | 36 |
| Positional `row[N]` accesses | 89 (reporting 28, scoring 45, store 16) |
| Constants defined in >1 module | 3 |
| Subpackages ignoring the `models.py` convention | 4 of 7 |

### The seven oversized modules

| Module | Lines | Symbols | Verdict |
|---|---|---|---|
| `store/operations.py` | 686 | 25 functions | 5 unrelated concerns, physically interleaved |
| `judge/scoring.py` | 670 | 1 class (420L) + 5 free functions | run loop + row hydration + cohort queries |
| `parser/extraction.py` | 530 | 15 functions, 11 constants | 5 concerns, one 135-line function |
| `judge/transcript_view.py` | 513 | 1 class (175L) + 18 helpers, 24 constants | reducer + budget math + section builders |
| `reporting/queries.py` | 489 | 6 dataclasses + 7 functions | models inline with queries and aggregation |
| `cli.py` | 484 | 4 commands + 7 helpers | one 108-line command |
| `ingest/orchestrator.py` | 453 | 1 class + 14 functions | orchestration + target resolution + persistence |

## Guardrails

1. `uv run pytest && uv run ruff check && uv run mypy` green after **every** step. The 344 tests are
   the safety net for the src work, which is why the test slice restructures without deleting.
2. The 14 ADRs in `docs/adr/` are behavioral contracts. Nothing here renegotiates one.
3. Read-only against `.claude/`. Store stays under `~/.cache/agentlens/`.
4. Module splits are **pure moves**. Move symbols, fix imports, change no logic. Any behavior change
   is a separate commit from any move, so a bisect can tell them apart.
5. `mypy --strict` catches every missed import after a move. Lean on it; do not hand-audit call sites.
6. Invoke `python-engineering-standards` before editing any `.py` under `src/agentlens/`.

## Why this is sequenced, not just sliced

The slices interlock at two points, both in src, both things the test slice builds on:

- `ClaudeCliJudge.__init__` (`judge/claude_cli.py:56`) accepts only `model` and `timeout_seconds`.
  `score()` calls `subprocess.run` inline at `:69`, `_check_claude_available` calls `shutil.which` at
  `:109`. No collaborator to inject, which is the direct cause of all 75 `@patch` decorators.
- `SessionRecord` is read back from SQL by position in three places. Shared test factories must be
  built against the settled shape.

```
Phase 0  hygiene ............................ either lane, no dependencies
Phase 1  S1 runner seam, S2 row contract ..... src only, BLOCKS the test lane
Phase 2  M1-M7 module splits (src)  ||  T1-T4 test restructure
Phase 3  S3-S5 within-module cleanups (src)
Phase 4  CLAUDE.md amendments ................ last, so it describes the settled tree
```

Phase 4 comes last on purpose: the convention doc should be rewritten against the structure that
exists when the work lands, not edited twice.

Module splits come before the within-module cleanups. Splitting first means each subsequent
decomposition happens in a file small enough to hold in your head, and the two never collide in the
same commit.

---

# Phase 0: hygiene

**H1. Turn on the lint rule that is off.** `pyproject.toml:48` selects `E,F,I,UP,B,SIM,C4`. `W` is
absent, which is why two whitespace-only lines pass. Add `W`, fix `aggregation/derivation.py:35` and
`parser/extraction.py:337`.

**H2. Strip the 5 stale plan citations, keep the rationale.** Each points at a document not in this
repo, and the durable reason is already in the same sentence. Remove only the tag.

| Location | Remove |
|---|---|
| `parser/extraction.py:209` | `(BUG-01)` |
| `parser/extraction.py:359-360` | `(Phase 2)`, `(see ARCH-01)` |
| `parser/extraction.py:378` | `BUG-02:` |
| `parser/session.py:271-273` | `D per standard-library-first` |
| `aggregation/derivation.py:2` | `(Phase 2 - see docs/agentlens-design.md §8)` |

---

# Phase 1: the two seams (src, blocking)

## S1. Injectable command runner for the judge

Unlocks T2. Highest-leverage change in the codebase.

- New `judge/process.py`: a `CommandRunner` Protocol narrowed to what `ClaudeCliJudge` actually calls
  (`run`, `which`), plus `SubprocessCommandRunner`, the one real implementation.
- `ClaudeCliJudge.__init__(*, model, timeout_seconds, runner: CommandRunner)`. Construct the real
  runner at the composition root in `cli.py`. **No `runner=None` default:** there is no
  `pytest-socket` guard in this repo, so a default would let a test that forgets to inject silently
  shell out to the real `claude` CLI, costing money and network. A required argument makes that a
  `TypeError` at construction.
- `_user_settings_path()` (`judge/claude_cli.py:137`) hardcodes `Path.home() / ".claude"`. Give it a
  `claude_home: Path` parameter wired from the existing `--claude-home` flag. Every other `.claude`
  access point is already injectable; this is the holdout. It also fixes a test that cannot fail:
  `test_claude_cli.py:480` asserts the path equals `str(Path.home() / ".claude" / "settings.json")`,
  computing its expectation with the same call production makes, so for the part that matters (which
  home) the assertion is tautological.

A Protocol with one in-repo implementation is correct for an injected dependency, not speculative.

## S2. One `fact_session` read contract

Unlocks T1.

The hazard is **edit-time, not DDL-time**. Both the INSERT (`operations.py:217`) and scoring's SELECT
(`scoring.py:170`) name their columns explicitly, so reordering the DDL is safe. What is not safe is
editing a `SELECT` list: insert or remove a column mid-list at `scoring.py:170-174` and all 45
downstream `row[N]` indices shift by one, silently. `mypy --strict` cannot catch it because every
element is `Any`, and the suite catches it only if a test happens to assert the field that shifted.

- Declare the column order once in `store/schema.py`.
- Switch readers to `sqlite3.Row` name-based access.
- The shared full-row mapper lands in the new `store/hydration.py` (see M2), which is where the three
  duplicate mappers converge.
- `reporting` projects a genuinely different subset, so it moves to name-based access only. Do not
  force one mapper onto both.

---

# Phase 2a: module splits (src lane)

Each split is a pure move. Target: no module over ~300 lines.

## M1. `store/operations.py` 686 to five modules

The concerns are already physically interleaved, which is the tell: `dim_date` logic sits at 320-357
**and** 602-615; session-grain logic at 214-317 **and** 618-686. Nobody could find either.

| New module | Moves | ~L |
|---|---|---|
| `store/events.py` | `_replace_session_events` (19-46), `upsert_session_events` (49-62) | 50 |
| `store/definitions.py` | `upsert_agent_definition` (65-93), `fetch_declared_skills` (96-129), `fetch_effective_agent_definition` (132-153), `resolve_session_agent_definition` (156-182), `_agent_definition_from_row` (185-199), `_decode_string_list` (202-211) | 150 |
| `store/sessions.py` | `_upsert_session` (214-269), `upsert_session` (272-279), `_replace_session_skills` (282-303), `upsert_session_skills` (306-317), `upsert_session_grain` (618-642), `_validate_child_identities` (645-659), `_source_revision_can_replace` (662-686) | 195 |
| `store/dimensions.py` | `_upsert_dim_date` (320-336), `upsert_dim_date` (339-357), `_upsert_dim_tool` (360-373), `upsert_dim_tool` (376-381), `_backfill_dim_date` (602-615) | 85 |
| `store/scoring_claims.py` | `set_session_judge_input_hash` (384-401), `verdict_exists` (404-422), `acquire_scoring_claim` (425-476), `release_scoring_claim` (479-499), `finalize_scoring_claim` (502-599) | 215 |

`operations.py` is deleted. Callers already import from named modules per CLAUDE.md, so the churn is
mechanical and mypy finds all of it.

## M2. `judge/scoring.py` 670 to four modules

| New module | Moves | ~L |
|---|---|---|
| `store/hydration.py` | `_row_to_session_record` (540-578), `_fetch_events` (581-605), `_to_parsed_session` (640-670). **This is where S2's shared mapper lives**, so the split and the duplication fix are the same edit. | 110 |
| `judge/models.py` | `ProgressEvent` (61-68), `ScoringResult` (72-81), `_RunState` (85-109), `_PreparedSession` (113-115) | 60 |
| `judge/model_cohort.py` | `is_concrete_model_id` (50-57), `KNOWN_MODEL_ALIASES` (47), `find_unscored_sessions` (148-202), plus the alias-resolution phase extracted from `score_window` | 120 |
| `judge/scoring.py` (keeps) | `ScoringLoop` run loop, `_empty_verdict_record`, `_to_verdict_record` | 290 |

## M3. `parser/extraction.py` 530 to six modules

| New module | Moves | ~L |
|---|---|---|
| `parser/jsonl_reader.py` | `ParseHealth` (57-73), `JsonlConsumption` (77-80), `consume_jsonl_records` (83-155), `read_jsonl_records` (158-179) | 125 |
| `parser/message_content.py` | `_content_items` (182-186), `_message_text` (189-203), **plus** transcript_view's `_content_items` (327-330), `_message_text_parts` (333-349), `_message_text_prefix` (352-374). Shared by parser and judge. | 70 |
| `parser/markers.py` | `flags_partial` (206-220), `_skill_name_from_skill_tool_use` (223-234), `_skill_names_from_meta_record` (237-251) + the `PARTIAL_*` / `_SKILL_*` constants (19-40) | 65 |
| `parser/field_coercion.py` | `parse_timestamp` (254-268), `_hash_input` (271-276), `_file_path_hash` (279-296), `_estimate_output_bytes` (299-305), `_usage_int` (376-383), `_duration_seconds` (386-393) | 70 |
| `parser/task_spawns.py` | `_task_subagent_type_from_item` (333-347), `extract_task_subagent_types` (350-373) | 45 |
| `parser/transcript_facts.py` | `TranscriptFacts` (309-330), `extract_transcript_facts` (396-530) | 170 |

`extraction.py` is deleted.

## M4. `judge/transcript_view.py` 513 to four modules

| New module | Moves | ~L |
|---|---|---|
| `judge/view_budget.py` | The 24 budget constants (17-41), `_truncate` (299-302), `_truncate_bytes` (305-320), `_bounded_field` (385-386), `_bounded_section` (475-480), `_bounded_identity_value` (451-455), `_enforce_view_byte_gate` (483-513), `_display` (295-296), `_format_tokens_k` (323-324) | 110 |
| `judge/view_reducer.py` | `_PendingToolUse` (47-49), `_ResolvedToolCall` (53-59), `_TranscriptViewReducer` (79-253) | 195 |
| `judge/view_sections.py` | `_ViewSections` (63-67), `_Section` (71-76), `_build_identity_body` (442-448), `_build_facts_body` (458-472), `_summarize_tool_use` (389-404), `_resolve_tool_summary` (407-419), `_error_excerpt` (422-439), `_extract_exit_code` (377-382) | 110 |
| `judge/transcript_view.py` (keeps) | `build_transcript_view` (256-292) as a thin entry point | 50 |

## M5. `reporting/queries.py` 489 to four modules

| New module | Moves | ~L |
|---|---|---|
| `reporting/models.py` | `AgentAggregate` (29-45), `VerdictCohort` (49-54), `ReportSessionRow` (58-98), `ParentLensRow` (102-108), `AgentWindowResult` (112-120), `ReportResult` (124-185) | 160 |
| `reporting/session_queries.py` | `_query_report_sessions` (312-384), `_query_agent_aggregates` (443-489), `_resolve_judge_model` (257-309) | 175 |
| `reporting/aggregates.py` | `_aggregate_sessions` (396-422), `_aggregate_parent_lens` (425-440), `_parse_verdict` (387-393) | 55 |
| `reporting/queries.py` (keeps) | `build_report` (188-254) | 70 |

## M6. `ingest/orchestrator.py` 453 to four modules

| New module | Moves | ~L |
|---|---|---|
| `ingest/models.py` | `IngestSummary` (45-52), `DefinitionSyncSummary` (56-60) | 25 |
| `ingest/target_resolution.py` | `resolve_target` (197-211), `_target_from_file` (214-243), `_find_target_by_session_id` (246-265), `_target_path` (426-427), `_target_kind` (430-431), `_source_project_for_path` (434-440), `_source_is_current` (443-453) | 105 |
| `ingest/persistence.py` | `persist_parsed_session` (364-412), `sync_agent_definitions` (324-361) | 95 |
| `ingest/orchestrator.py` (keeps) | `IngestRunner` (63-194), `ingest_target`, `ingest_all`, `_parse_subagent_run`, `_read_meta`, `_read_parent_task_map` | 200 |

## M7. `cli.py` 484: an SRP problem, not a size problem

`cli.py` carries ten responsibilities. Three are legitimately a CLI's: Click option declaration,
exception-to-exit-code translation (`_handle_cli_errors` 53-71), and constructing concretes such as
`ClaudeCliJudge(model=judge_model)` at `:279`, which is correct because this is the composition root.
The other seven move out.

**M7a. `session` reimplements `IngestRunner`, minus a correctness feature.** The highest-severity
finding in the module. `cli.py:139-147` and `IngestRunner._sync_project_definitions`
(`orchestrator.py:178-194`) make the same `sync_agent_definitions(..., project_claude_dir=project_root
/ ".claude", source_project=..., include_user=False)` call behind the same `project_root is None`
guard. `IngestRunner` memoizes via `_synced_project_definition_contexts`; the CLI copy does not, so
the CLI path re-syncs definitions it has already synced.

The asymmetry names the fix: `ingest` delegates to `ingest_all()`, while `session` hand-rolls the
single-target equivalent inline. Add `ingest_one()` to `ingest/orchestrator.py` alongside
`ingest_all()`, both routing through `IngestRunner`. `session` drops from 49 lines to about 10, and
the duplicated project-definition sync disappears.

**M7b. One summary formatter, not two.** `cli.py:290-293` hardcodes the zero-unscored case as a
literal string (`"Attempts: 0. Scored: 0. Skipped: 0. Remaining: 0. Total judge cost: $0.00. Aborted:
no."`) while `:345-350` formats the same six fields from `result`. Add a field to `ScoringResult` and
the zero path silently omits it while presenting itself as the same summary. Collapse to one formatter
that accepts an empty result.

**M7c. Score rendering belongs in `reporting/rendering.py`.** `report` correctly delegates to
`render_terminal_summary` (`:452`); `score` builds its summary with inline f-strings plus
`_resolved_model_note` (369-375), and renders progress from a 16-line closure (`:320-335`). Move both
next to the renderer that already exists.

**M7d. Store knowledge to `store/schema.py`.** `_open_report_store_read_only` (455-464) knows the
`?mode=ro` URI form, `PRAGMA query_only`, and the schema assertion. That is store vocabulary and
belongs beside `create_store`. The `store_path` + `claude_home` resolution triplicated at `:118`,
`:184`, `:269` becomes one helper in the same module.

**M7e. Pricing to the judge (OCP).** `PER_SESSION_COST_ESTIMATE` (44-48) hardcodes per-model dollar
figures in the CLI, so adding a judge model means editing the CLI. Move it with
`_estimate_judge_cost` (359-366) into a new `judge/cost_preview.py`, together with the dry-run preview
and confirmation blocks inside `score`.

**M7f. Discovery to `discovery/filesystem.py`.** `_discover_jsonl_paths` (378-391) imports two
discovery functions and documents qualified source identity. It is discovery logic. `_unique_paths`
(467-468) goes with it.

Result: `cli.py` around 200 lines of thin wiring, four commands that parse arguments and delegate.

---

# Phase 2b: DRY consolidation (folds into the splits above)

All verified, not inferred.

| Duplication | Locations | Resolution |
|---|---|---|
| `MAX_PENDING_TOOL_USES = 4096` defined twice | `judge/transcript_view.py:30`, `parser/extraction.py:51` | One definition. Both files implement the same bounded tool_use pairing, so it belongs with the shared pairing logic. |
| `SESSION_KIND_MAIN` / `SESSION_KIND_SUBAGENT` defined twice | `discovery/filesystem.py:18-19`, `parser/session.py:22-23` | Domain vocabulary used in SQL `WHERE` clauses (`scoring.py`). Two definitions of the same enum is a drift risk. Needs one home (see decision below). |
| Message-content extraction | `extraction.py:182,189` and `transcript_view.py:327,333,352` | `parser/message_content.py` (M3). Verified functionally equivalent today (list vs generator), so this is DRY, not a latent bug. |
| SQL row to `SessionRecord` | `scoring.py:540`, `reporting/queries.py:312` and `:443`, `operations.py:214` | `store/hydration.py` (M2) plus name-based access (S2). |
| tool_use to tool_result pairing with a pending cap | inside `extract_transcript_facts` (the 135-line function) and `_TranscriptViewReducer._record_tool_call` (`transcript_view.py:178-219`) | Same algorithm, two implementations, two copies of the cap constant. Extract one pairing helper both reducers use. |
| `models.py` convention applied to 3 of 7 subpackages | `discovery` and `store` follow it; `judge` (11 dataclasses), `reporting` (7), `parser` (5), `ingest` (2) do not | M2/M5/M6 add `models.py` to `judge`, `reporting`, `ingest`. This is a large part of why those modules are fat. |

---

# Phase 3: within-module cleanups (src lane, after the splits)

Now cheap, because each lands in a module small enough to read.

| Item | Target | Seam |
|---|---|---|
| S3a | `extract_transcript_facts` (135L, now in `parser/transcript_facts.py`) | Convert to a `_TranscriptFactsReducer` class with `_consume_assistant_record` / `_consume_user_record`. This mirrors `_TranscriptViewReducer`, which already solves the identical record-classification problem as a class. Two modules doing the same job with different shapes is the readability cost. |
| S3b | `ScoringLoop._process_session` (99L) + `score_window` (75L) | `_RunState` is threaded as a parameter through `_run_sessions`, `_process_session`, `_record_failure`, `_emit_progress` alongside the same three other values. That is an `__init__` turned inside out: give the context an owner. |
| S3c | `finalize_scoring_claim` (98L, now in `store/scoring_claims.py`) | `_insert_verdict_or_diagnose` / `_release_claim_or_raise`. Currently needs three SQL statements' success and failure semantics held at once. |
| S3d | `validate_verdict` (115L, `judge/protocol.py`) | `_validate_dimensions` / `_validate_fixes` / `_validate_usage`. Four independent blocks already. |
| S3e | `parse_subagent_run` (108L, `parser/session.py`) | A `SubagentMeta` frozen dataclass parsed once at the boundary removes the five repeated `isinstance(...) else None` guards at `:173-180`. The same file already does this for `AgentDefRecord`. |
| S5 | `ReportSessionRow.verdict: dict[str, Any]` (`queries.py:77`) | `mypy --strict` currently cannot check any verdict field access in reporting, so `row.verdict["overall_score"]` at `:404` and `rendering.py:51` is unchecked while the gate reports green. `Verdict` and `DimensionScore` already exist in `judge/protocol.py` for this exact payload. Parse once at the query boundary. |

---

# Phase 2c: test lane (parallel with the src splits)

Unchanged from the prior revision except that T1's factories should import from the post-split
modules.

**T1. Shared fixture layer.** Depends on S2.

```
tests/factories/__init__.py   (empty, 0 bytes)
tests/factories/records.py    session_record(), parsed_session(), verdict(), tool_event()
tests/factories/jsonl.py      write_main_session(), write_subagent_run(),
                              tool_use_record(), tool_result_record()
tests/factories/judge.py      judge_envelope(), model_usage_entry(), FakeCommandRunner
tests/factories/store.py      insert_verdict(conn, session_id, **overrides)
tests/unit/conftest.py        store fixture (open conn + contextlib.closing), isolated_home
```

| Duplicated shape | Current homes |
|---|---|
| `SessionRecord` builder (~25-key defaults dict) | `test_store.py:689`, `test_scoring.py:92`, `test_score_cli.py:60`, `test_reporting.py:31` (as `_session`) |
| `ParsedSession` builder | `test_transcript_view.py:34`, `test_aggregation.py:45`, `test_claude_cli_canary.py:100` (inline) |
| `Verdict` builder | `test_judge_protocol.py:35`, `test_scoring.py:73` |
| `INSERT INTO fact_verdict` | `test_scoring.py:158`, `test_reporting.py:338`, `test_score_cli.py:127` |
| Judge JSON envelope | `test_claude_cli.py:63`, `test_score_cli.py:27` (a copy of a copy: inlines `modelUsage` rather than reusing the helper) |
| tool_use / tool_result JSONL | `test_ingest.py:30` (string template), `test_parser.py:112` (dict), `test_transcript_view.py:72` (dict, different fields), `test_cli.py:137`, `:190`, `:417` (longhand three times in one file) |

Resolve the drift deliberately while consolidating: the `SessionRecord` builders disagree on
`agent_id` (`"a1"` vs `session_id`), the `ParsedSession` builders on `n_turns` (2 vs 3). Nothing in
the tests says which is intentional.

`test_ingest.py:40-103` already has the only proper JSONL builder trio. Promote it rather than
writing a new one, then delete the three longhand copies in `test_cli.py`.

**T2. Delete the `@patch` scaffolding.** Depends on S1. 75 decorators become direct construction:
`ClaudeCliJudge(model="sonnet", runner=FakeCommandRunner(envelope=...))`. The 36
`mock_which: MagicMock` parameters exist only to satisfy decorator ordering and are often unused.

Leave alone, correctly: `monkeypatch.setattr(Path, ...)` in `test_ingest.py:656` and
`test_discovery.py:104` synthesizes an unreadable or racing filesystem, and
`patch("agentlens.cli....")` in `test_cli.py:452` targets the composition root. Both patch at a real
boundary.

**T3. Assertion readability.**

- Add `flag_value(args, flag) -> str`. The index-arithmetic idiom is repeated at
  `test_claude_cli.py:134`, `:201`, `:478` and `test_claude_cli_canary.py:142`.
- `test_ingest.py:395` asserts `{(row[2], row[3]) for row in rows}` against a `SELECT` ten lines
  earlier. Use name-based access.
- Explain the `CREATE TRIGGER ... RAISE(ABORT)` idiom that forces a mid-transaction failure
  (`test_ingest.py:209`, `test_cli.py:314`). Good technique, currently unexplained.
- `test_cli.py:296-380` hand-writes ~55 lines of raw `INSERT` SQL; becomes a few factory calls.

**T4. Fill the one real coverage gap.** No test asserts `build_report` against a window with zero
matching sessions: `agents`, `sessions`, `parent_lens` all empty, and `to_verdict_slice()` plus
`render_terminal_summary` not crashing.

---

# Phase 4: CLAUDE.md amendments

A convention should constrain bad practice. Three of these constrain good practice instead, which is
how `SESSION_KIND_*` ended up defined twice.

**C1. Replace the package-root whitelist with its intent.** The rule says the root holds only
`cli.py`, `errors.py`, and `__init__.py`. It is self-refuting: `errors.py` *is* cross-cutting
vocabulary living in the root, importable by every layer. That proves the category is legitimate and
the whitelist is merely incomplete. Restate as:

> The package root holds only cross-cutting vocabulary that every layer may import (`errors.py`,
> `domain.py`) plus the CLI entry point. Anything belonging to a domain lives in a subpackage.

`domain.py` then needs no exception, and the rule still blocks what it was written to block.

**C2. Drop the per-file module inventory.** The "Project structure" section enumerates every file in
the package, duplicating the directory tree into prose. It becomes a second source of truth that goes
stale on every change, and this refactor invalidates most of it. It also enshrines current violations
as convention: it documents `judge/protocol.py` as the home for the `DimensionScore` and `Verdict`
dataclasses, which is exactly the `models.py` inconsistency Phase 2b fixes. Keep the one-line
per-subpackage responsibility statements, which genuinely say what belongs where. Let `ls` answer
what files exist.

**C3. Revisit "all custom exceptions live in root `errors.py`."** Softer than the other two. It means
the root accumulates every layer's failure vocabulary, and every subpackage imports from the root.
Fine at 37 lines, so this is a note rather than a change. Worth stating because it is the precedent
that justifies `domain.py`: if centralized cross-cutting vocabulary is right for exceptions, it is
right for session kinds.

**C4. Fix the one rule that is stated but contradicted.** "Use `contextlib.closing()` for
`sqlite3.Connection` lifecycle in CLI commands, not bare `try/finally`." `cli.py` obeys it. The test
suite uses `try/finally: conn.close()` **109 times and `closing()` zero times.** Technically in
scope, since the rule says "in CLI commands," but it reads as a codebase norm applied in one file.
T1's `store` conftest fixture is the natural place to make it true everywhere; otherwise say
explicitly that it is CLI-scoped.

**Keep exactly as written,** because each constrains a bad practice: empty `__init__.py`; import from
the named module rather than the package root; a class for stateful orchestration and a free function
for stateless transforms; read-only against `.claude/`; synthetic-only tests; measured and modeled
never in one table; the spawn is the unit; no pre-created folders for unstarted phases; the
three-command quality gate.

---

## Open decision

`cli.py` could become a `cli/` subpackage (`main.py` plus `commands/*.py`) rather than pushing logic
down into the subpackages that own it. Cleaner if you expect more than four commands. The M7 plan
above assumes it stays one thin module, which is the smaller move and consistent with C1's revised
principle: the CLI entry point stays in the root.

---

## Definition of done

| Metric | Before | After |
|---|---|---|
| Modules over 400 lines | 7 | 0 |
| Largest module | 686 | ~290 |
| Functions >= 45 lines | 38 | under 15 |
| `cli.py` responsibilities | 10 | 3 |
| `cli.py` lines | 484 | ~200 |
| Score-summary formatters | 2 (one hardcoded) | 1 |
| Per-target ingest workflows | 2 (`IngestRunner` + inline in `session`) | 1 |
| `@patch` decorators | 75 | 0 |
| Positional `row[N]` in src | 89 | 0 |
| Constants defined in >1 module | 3 | 0 |
| Subpackages with dataclasses but no `models.py` | 4 | 0 |
| Stale plan citations | 5 | 0 |
| Tests passing | 344 | 344 |

Plus: `ruff check` clean with `W` enabled, `mypy --strict` clean, no test reads the real `~/.claude`
tree (ADR 0001 holds).

## Explicitly not in scope

- Consolidating or deleting tests. The suite is the safety net for the src work.
- Any ADR change, schema migration, or CLI contract change.
- Test naming. Names across all 14 files already state what they assert.
