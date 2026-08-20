## Context

See `proposal.md` — Why, for the motivation and the defect class.

The constraints that shape the approach:

- **ADR 0001 / ADR 0002**: nothing `sqlite3`-shaped may leave `store`. Whatever
  represents a column is a `store` type, not a `models` type — `models` holds
  domain types, and a SQLite column definition is a storage detail.
- **ADR 0002**: the runtime dependency set is closed. No query builder, no ORM,
  no SQL-generation library. Standard library only.
- **ADR 0008**: `fact_session` is derived in Python inside `ingest`; `store`
  persists it verbatim and does no aggregation. This change must not move
  derivation into `store`.
- **CLAUDE.md**: the store is a disposable cache with no migration tooling.
- The `sql-data-analysis` skill governs SQL here, and it prefers explicit
  readable SQL over constructed SQL — a real tension this design has to answer
  rather than ignore.

## Goals / Non-Goals

**Goals:**

- One declaration per table is the only place column order is stated.
- Adding a column is a two-place edit — the declaration and the value extractor
  — and forgetting either one fails a test rather than corrupting data.
- Reads stop depending on column order entirely.
- The emitted DDL is byte-for-byte equivalent in effect to today's, so existing
  stores keep working untouched.

**Non-Goals:**

- Adding, removing, renaming, or retyping any column. `parent_session_id` and
  the other Phase 1 columns belong to later changes; this one must be provably
  behavior-preserving, and mixing a column addition in would destroy that
  property.
- Adding indexes, foreign keys, or constraints beyond the existing primary keys.
- Changing the staleness rule, the transaction shape, or the three
  `UpsertOutcome` values.
- Building a general query builder or a mini-ORM. Two tables and five statements
  is the entire scope.
- Touching any package other than `store`.

## Decisions

### Column order is declared once, as an ordered tuple of column definitions in `store/schema.py`

A module-level tuple of a small frozen dataclass carrying the column name and
its SQLite type declaration. It lives in `store/schema.py` because that module
already owns the DDL, and it stays out of `models` because a SQLite type is a
storage concern.

*Alternative considered — a `dict[str, str]` of name to type.* Insertion-ordered
since Python 3.7, so it would work, but it makes "this is an ordered sequence"
incidental to a data structure chosen for lookup. An explicit tuple says the
order is the point.

*Alternative considered — deriving columns by reflecting over `FactSession`'s
dataclass fields.* Rejected: the mapping is not one-to-one. `FactSession` nests
`identity` and `revision` as sub-objects that flatten into seven columns, and
`session_kind` and `name_source` are enums stored as text. Reflection would have
to encode those exceptions anyway, and it would couple the storage layout to the
domain type's internal shape, so a `models` refactor would silently alter the
schema.

### The declaration carries nullability, and reproduces each table's key style faithfully

Surfaced while pinning the schema in task group 1, and sharp enough to change
what the declaration has to hold.

Nullability is not derivable from the type. Four of the 37 columns are nullable
— `spawning_tool_use_id` on `fact_session`, and `file_identity`, `denial_kind`,
`result_size` on `fact_tool_event` — and every other column carries an explicit
`NOT NULL`. A declaration holding only name and type would emit a DDL in which
everything is nullable.

More subtly, **the two tables declare their primary keys differently, and the
difference is observable.** `fact_session` uses an inline bare
`session_id TEXT PRIMARY KEY`, which SQLite reports as `notnull = 0` and, for a
`TEXT` key, genuinely does not enforce. `fact_tool_event` declares
`session_id TEXT NOT NULL` with a table-level `PRIMARY KEY (session_id, ordinal)`
clause, reporting `notnull = 1`.

A generator that tidies this — treating "is a key column" as implying `NOT NULL`,
or normalizing both tables onto one convention — would emit
`session_id TEXT NOT NULL PRIMARY KEY` for `fact_session` and cause SQLite to
start enforcing a constraint it does not enforce today. That is a real schema
change wearing a refactor's clothes, and it would break this change's central
claim. The generator therefore reproduces each table's existing key style rather
than unifying them, and if the pin test from task 1.1 fails on this, the pin is
right and the generator is wrong.

Whether the `fact_session` key *should* be `NOT NULL` is a fair question and a
separate change. It is out of scope here precisely because this change must be
provably behavior-preserving.

### The five restatements are generated from that declaration

The DDL body, the `INSERT` column list, the placeholder run, the
`DO UPDATE SET` list, and the `SELECT` list are all produced from the
declaration. The statement skeletons — `INSERT INTO ... ON CONFLICT ... WHERE`,
the staleness predicate, `ORDER BY ordinal` — stay literal in
`store/operations.py`. Only the column lists are interpolated.

This is the answer to the `sql-data-analysis` tension: the parts of the SQL that
carry meaning and that a reader greps for stay written out, and only the
mechanical enumeration is generated. The staleness `WHERE` clause in particular
remains literal, because it is the subtlest logic in the module.

Interpolating these strings is not an injection surface: the column names are
module constants and no caller supplies them. Values remain parameterized.

### Writes go through a name-keyed extractor map, so the tuple is generated rather than written

`fact_session_to_row` becomes a mapping from column name to a function that
pulls that value off `FactSession`, with the tuple assembled by walking the
declaration. The author of a new column writes a name-keyed entry and never
writes a position.

This is the decision that actually kills the defect. Generating the SQL alone
would not: a hand-written value tuple could still drift from a generated
`INSERT` list. Once both sides are ordered by the same declaration, the two
cannot disagree.

The guard is a test asserting the extractor map's key set equals the
declaration's name set. Adding a column without an extractor fails it; adding an
extractor without a column fails it. That test is the reason this refactor is
worth doing rather than just being tidier.

### Reads use `sqlite3.Row` and access by name

`store/connection.py` sets `row_factory = sqlite3.Row`, and
`row_to_fact_session` reads `row["n_turns"]` instead of unpacking 28 positions.
`sqlite3.Row` supports integer indexing too, so the existing
`stored_hash_row[0]` in the staleness path keeps working unchanged.

`sqlite3.Row` is a driver type and must not escape `store`. It does not:
`row_to_fact_session` and `row_to_fact_tool_event` consume it and return
`models` types. The existing `lint-imports` contract confining `sqlite3` to
`store` already enforces this.

*Alternative considered — `dict(zip(COLUMN_NAMES, row, strict=True))`.* Attractive
because `strict=True` raises loudly on an arity mismatch, and it avoids changing
the connection's row factory. Rejected as the primary mechanism because the
`SELECT` list is generated from the same declaration the names come from, so the
arity it would check cannot diverge — the check guards an impossible state while
adding a zip on every row read.

### `fact_tool_event` gets the same treatment despite lower risk

Nine columns, of which only two are adjacent same-typed nullables, so the
corruption hazard is much smaller than `fact_session`'s sixteen-integer run.
Doing it anyway: two tables handled two different ways is its own trap, because
the next person has to work out which convention a given table follows before
touching it.

## Risks / Trade-offs

**Generated SQL is harder to grep than literal SQL.** A developer searching for
`n_denials` will find the declaration and the extractor, but not an `INSERT`
statement containing it. → Mitigated by keeping statement skeletons literal so
the *shape* of every statement is still readable in place, and by a test that
executes the generated DDL and asserts `PRAGMA table_info` reports exactly the
declared columns, in order, with the declared types. That test doubles as
executable documentation of the emitted schema.

**This reads as the first step toward an ORM.** → It is not, and ADR 0002 closes
the question. The design note above bounds it explicitly: two tables, five
statements, no generalization. A future change that wants a third table copies
the pattern rather than abstracting over it.

**Abstraction overhead for a two-table store.** Honest cost: a reader now has one
indirection between "what columns exist" and the SQL text. → Accepted because
four more columns are queued behind this change and the failure it prevents is
silent rather than loud. If only one column were coming, this would not be worth
it.

**A refactor can change behavior while all tests still pass.** → The acceptance
criterion is that the existing 82 tests pass *unchanged*. Any test needing an
edit is the signal to stop and re-examine, not to update the test. Additionally,
the `PRAGMA table_info` assertion pins the emitted schema against the pre-change
DDL rather than against the new generator, so the two are compared to each other
rather than to themselves.

## Migration Plan

No migration. The generated DDL produces the same table definitions as the
current literal DDL, both statements are `CREATE TABLE IF NOT EXISTS`, and no
column is added, dropped, or retyped, so an existing `agentlens.db` is read and
written identically before and after.

Rollback is reverting the commit. Because the on-disk schema is unchanged, a
store written by the new code is fully readable by the old code and the reverse,
so rollback needs no data step. This is a stronger position than ADR 0008's
usual rebuild-from-source answer, and it holds only because this change is
deliberately column-neutral.
