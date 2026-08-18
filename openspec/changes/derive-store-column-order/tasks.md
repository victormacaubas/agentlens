## 1. Pin the current schema before touching it

These tests are written against the existing literal DDL and must pass before
any refactoring starts. That ordering is what makes them a before/after
comparison rather than the new generator being checked against itself. Once
they pass, they are frozen — see task 6.1.

- [x] 1.1 Add a test that runs `ensure_schema` against a real SQLite file and asserts `PRAGMA table_info(fact_session)` reports exactly 28 columns in the current order, each with its declared type, `notnull` flag, and primary-key flag.
- [x] 1.2 Add the same assertion for `fact_tool_event`: 9 columns in order, with the composite primary key on `(session_id, ordinal)`.
- [x] 1.3 Run `make check` and confirm both new tests pass against the unmodified DDL. Do not proceed until they do.

Landed in `tests/unit/test_store_schema.py` as two tests, each a single equality
over the whole ordered column tuple, so count, order, type, nullability, and
key position are pinned together. Gate green at 84 tests with `src/` untouched.

## 2. Declare each table's column order once

- [x] 2.1 Add a module-private frozen dataclass in `store/schema.py` holding a column's name, its SQLite type, and its nullability. Nullability is not derivable from the type — four columns are nullable and the rest are `NOT NULL`. See `design.md`, "The declaration carries nullability".
- [x] 2.2 Declare the ordered `fact_session` column tuple, transcribed from the current DDL with no changes to names, types, order, or nullability.
- [x] 2.3 Declare the ordered `fact_tool_event` column tuple the same way.
- [x] 2.4 Generate `CREATE_FACT_SESSION_SQL` and `CREATE_FACT_TOOL_EVENT_SQL` from the declarations, keeping `CREATE TABLE IF NOT EXISTS`, the `PRIMARY KEY` clauses, and the table names literal in the skeleton. Reproduce each table's existing key style rather than unifying them: `fact_session` keeps a bare inline `session_id TEXT PRIMARY KEY` with no `NOT NULL`, and `fact_tool_event` keeps `NOT NULL` columns with a table-level composite `PRIMARY KEY`. Adding `NOT NULL` to the `fact_session` key is a real schema change, not a tidy-up.
- [x] 2.5 Run `make check`. Tasks 1.1 and 1.2 must pass without being edited.

Both `CREATE TABLE` statements came out byte-for-byte identical to the literals
they replaced. `_column_ddl` appends `NOT NULL` and `PRIMARY KEY` from separate
unconnected conditions, so no code path can unify the two key styles; NULL-key
enforcement was checked directly against the pre-change behavior and matches.

## 3. Generate the column lists in the SQL statements

Statement skeletons stay literal. Only the enumerations are interpolated.

- [x] 3.1 Derive the `INSERT` column list and the placeholder run for both tables from the declarations, replacing the hand-written names and the hand-counted `?` marks.
- [x] 3.2 Derive the `ON CONFLICT DO UPDATE SET` list for `fact_session`, excluding `session_id` as the conflict target. Leave the staleness `WHERE` predicate written out literally — it is the subtlest logic in the module and must stay greppable.
- [x] 3.3 Derive the `SELECT` lists for `_SELECT_SESSION_SQL` and `_SELECT_TOOL_EVENTS_SQL`, keeping the `WHERE` and `ORDER BY ordinal` clauses literal.
- [x] 3.4 Run `make check`.

Ruff's S608 fires on all four constructed statements. Resolved with four
narrowly scoped `# noqa: S608` directives rather than by restructuring, since
the obvious restructurings evade the regex without changing what the code does
and would destroy the greppable statement skeleton this design asked to keep.
RUF100 confirms none of the four is dead weight.

## 4. Make writes name-keyed

- [x] 4.1 Replace the positional tuple in `fact_session_to_row` with a mapping from column name to a function extracting that value from `FactSession`, including the flattening of `identity` and `revision` and the enum-to-text conversions.
- [x] 4.2 Do the same for `fact_tool_event_to_row`, preserving the existing `timestamp.isoformat()` and `int(event.is_error)` conversions.
- [x] 4.3 Assemble each row tuple by walking the column declaration and calling each extractor, so no position is ever written by hand.
- [x] 4.4 Add a test asserting, for both tables, that the extractor map's key set equals the declaration's column-name set exactly. This is the guard that makes a forgotten column fail loudly instead of silently shifting values.
- [x] 4.5 Run `make check`.

`FACT_SESSION_VALUE_EXTRACTORS` and `FACT_TOOL_EVENT_VALUE_EXTRACTORS` now
drive the write tuples in declaration order, and two focused coverage tests
guard both key sets. Gate green at 86 tests.

## 5. Make reads name-keyed

- [x] 5.1 Set `row_factory = sqlite3.Row` on the connection in `store/connection.py`.
- [x] 5.2 Rewrite `row_to_fact_session` to read each value by column name, deleting the 28-name positional destructuring.
- [x] 5.3 Rewrite `row_to_fact_tool_event` the same way.
- [x] 5.4 Confirm the staleness path's `stored_hash_row[0]` still works under `sqlite3.Row`, which supports integer indexing, and that `read_session`'s `tuple(...)` conversions are removed or adjusted consistently.
- [x] 5.5 Run `make check`.

The store connection now returns `sqlite3.Row` values; reads consume names,
while the staleness check retains supported integer indexing. All five import
contracts remain kept.

## 6. Verify the refactor preserved behavior

- [x] 6.1 Run `git diff` over `tests/` and confirm it contains only additions. Any modification to one of the original 82 tests means behavior changed — stop and re-examine rather than updating the test.
- [x] 6.2 Confirm `lint-imports` still reports 5 contracts kept and that no `sqlite3.Row` appears in a signature outside `store`.
- [x] 6.3 Write a store with the pre-change code, then read it with the post-change code and confirm the session round-trips identically. This proves the no-migration claim in `design.md`.
- [x] 6.4a Add one regression test per row converter using a real `sqlite3.Row` whose projection order differs from the table declaration, proving reads are name-keyed.
- [x] 6.4 Run the `structure-review` skill against the change. A review asking for changes blocks the archive.
- [x] 6.5 Run `make check` one final time and confirm a clean gate.

Both test files are additions; no original test changed. `lint-imports` reports
5 kept and 0 broken contracts, with `sqlite3.Row` confined to `store`. An
isolated `HEAD` worktree wrote a store containing two differently shaped tool
events, and the post-change code read back the expected `SessionFacts`
identically.

Both row converters now have a focused regression test using a reversed
projection from a real SQLite table. The tests fail under positional
reconstruction and pass under name-keyed reads.

The initial structure review requested those tests. Re-review verified the
finding fixed and returned `approve_with_comments`; its non-blocking module
docstring comment was also applied.

Final gate green: formatting, lint, 5 import contracts, mypy, and 88 tests.
