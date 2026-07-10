## Context

`persist_parsed_session` derives one session's fact row and skill bridge, then calls five store upsert paths for events, the session row, skills, tools, and date. Each public upsert currently opens its own `with conn:` block, so SQLite commits each part independently. The bulk ingest loop deliberately catches failures per target and continues; that isolation is correct, but today it can continue after leaving the failed target partially updated.

The store is a disposable cache, tests must remain synthetic-only, and the existing public single-table upserts are directly tested. The change therefore needs to add per-session atomicity without creating a transaction for the entire bulk run or silently changing standalone upsert semantics.

## Goals / Non-Goals

**Goals:**

- Make every database write derived from one parsed session commit or roll back together.
- Preserve the previous complete version when re-ingest fails and leave no session-derived rows when first ingest fails.
- Keep `ingest_all` failure isolation and successful commits scoped to individual targets.
- Concentrate transaction ownership in the store module behind one high-leverage interface.

**Non-Goals:**

- No schema, DDL, migration, CLI, reporting, or parser behavior changes.
- No transaction spanning agent-definition sync or multiple session targets.
- No generic transaction abstraction, retry policy, or alternate database adapter.

## Decisions

### D1 — The store exposes one atomic full-session write

Add a store interface such as `upsert_session_grain` that accepts the derived `SessionRecord`, tool events, and skill bridge records. It derives the required tool names and date backfill from those typed records and performs all related writes inside one `with conn:` transaction.

This makes atomicity an invariant of the store interface rather than an ordering rule every ingest caller must remember. `persist_parsed_session` remains responsible for parsing/aggregation orchestration and delegates persistence with one call.

**Alternative considered:** wrap the existing public upserts in `with conn:` from the ingest module. Rejected because nested SQLite connection context managers commit on each inner exit, so the apparent outer transaction would not provide atomicity.

### D2 — Separate statement helpers from transaction-owning interfaces

Refactor the SQL bodies used by session events, the session row, skill rows, and dimension rows into private helpers that execute statements without committing. Existing public single-table upserts keep their current transaction-owning behavior by wrapping the corresponding helper. The new full-session interface opens one transaction and calls the private helpers directly.

This preserves current callers and tests while preventing nested commits in the atomic path.

**Alternatives considered:**

- Remove transactions from every public upsert and require all callers to commit. Rejected because it changes existing interface semantics and makes transaction ownership implicit.
- Add `commit=False`, cursor, or transaction flags to every upsert. Rejected because it expands several interfaces with coordination details callers should not need.
- Introduce savepoint management. Rejected because a single known composite write path does not require a generic nested-transaction mechanism.

### D3 — Dimension backfills participate in the same transaction

`dim_tool` and `dim_date` are derived from the same parsed session and are currently written after the three session-grain tables. They participate in the atomic write so a late dimension failure cannot commit a mixed-version session. Existing dimension rows shared with other sessions are only inserted with conflict-safe statements and remain unchanged.

### D4 — Verify rollback with a real SQLite failure

Use a synthetic SQLite trigger in the test database to abort a late dimension write during re-ingest. The regression test first commits a complete old version, then attempts a changed version and asserts that events, session facts, skill rows, and dimensions still reflect the old version. This exercises SQLite rollback through the public ingest path without mocking implementation details or reading real `.claude` data.

## Risks / Trade-offs

- **A session transaction holds the SQLite write lock slightly longer.** → The write set is small and bounded to one parsed session; parsing and aggregation remain outside the transaction.
- **Private statement helpers add a second layer beneath existing upserts.** → Keep them narrowly named and colocated with their public wrappers; the atomic interface provides the resulting locality and leverage.
- **A broad `ingest_all` exception handler could obscure the injected database error.** → The regression test asserts both the skipped-session outcome and the unchanged persisted rows; direct store tests may additionally assert the raised SQLite exception if useful.

## Migration Plan

No data migration is required. Deploy the code and rerun ingest; existing stores remain compatible. If the change is reverted, the schema and stored rows remain readable, though the original partial-commit risk returns.

## Open Questions

None.
