## Why

The `fact_session` column order is written out by hand seven times: the DDL in
`store/schema.py`, the `INSERT` column list, its 28 `?` placeholders, the
`ON CONFLICT DO UPDATE SET` list, the `SELECT` list in `store/operations.py`, the
tuple built by `fact_session_to_row`, and the 28-name destructuring in
`row_to_fact_session`. Every one of the seven has to agree, and nothing checks
that they do.

Sixteen of the twenty-eight columns are consecutive integer counters, from
`n_turns` through `cache_creation_tokens`. If a new column is appended to
`fact_session_to_row` at a different position than to the `INSERT` list, SQLite
writes the wrong counter into the wrong column, raises nothing, and a round-trip
test that reads back through the same mismatched order still passes. That is a
silent wrong-numbers defect in a tool whose entire product is numbers.

The remaining Phase 1 work adds at least four more columns — `parent_session_id`,
`agent_definition_id`, `task_prompt_len`, `n_skills_fired` — so the hazard is
about to be exercised four more times. Removing it is cheaper before those
columns land than after.

## What Changes

- Declare each table's column order exactly once in `store`, as an ordered
  sequence of column definitions that carries the column name and its SQLite
  type.
- Generate the DDL, the `INSERT` column list, the placeholder run, the
  `DO UPDATE SET` list, and the `SELECT` list from that single declaration
  instead of restating them.
- Read rows by column name rather than by tuple position, so
  `row_to_fact_session` no longer depends on the order it is handed.
- No change to the emitted schema, to the staleness rule, to upsert outcomes, or
  to any observable behavior. Not breaking.

## Capabilities

### New Capabilities

None. This change introduces no capability.

### Modified Capabilities

None. The behavior described by `openspec/specs/store-schema/spec.md` is
unchanged: the same tables, the same columns in the same order, the same
primary keys, the same staleness rule, and the same three upsert outcomes. This
change alters only how that behavior is expressed in code, so per the schema's
own guidance it sets `skip_specs: true` rather than inventing a requirement to
satisfy validation.

## Impact

**Code, all inside `store`:**

- `src/agentlens/store/schema.py` — column declarations become the source of
  truth; `CREATE_FACT_SESSION_SQL` and `CREATE_FACT_TOOL_EVENT_SQL` are derived.
- `src/agentlens/store/operations.py` — the three statements that restate the
  column list are derived from the same declaration.
- `src/agentlens/store/rows.py` — positional tuple unpacking is replaced by
  name-keyed access.
- `src/agentlens/store/connection.py` — sets the row factory that makes
  name-keyed access available.

**Not affected:** no module outside `store` changes. `models`, `ingest`, `core`,
`render`, and `cli` are untouched, so the five `lint-imports` contracts move by
zero edges and nothing `sqlite3`-shaped crosses the package boundary.

**Dependencies:** none added. The change uses `sqlite3.Row` from the standard
library, so the closed runtime set in ADR 0002 is unaffected.

**Acceptance:** the existing 82 tests must pass **unchanged**. This change is
behavior-preserving, so a test that needs editing is evidence the refactor
changed behavior and is the signal to stop, not to update the test.

**Sequencing:** this lands before `ingest-parent-lineage-and-main-sessions`,
which is the change that adds `parent_session_id` and is the first beneficiary.

**Known tension:** `CLAUDE.md` requires changes to be vertical slices rather than
one layer at a time. This change is confined to `store` and so is a single
layer. The rule targets building features layer by layer; a behavior-preserving
refactor of an existing layer is a different animal, and the vertical slice it
protects has already shipped in `ingest-single-transcript`. Recorded here so the
structure review can weigh it deliberately rather than discover it.
