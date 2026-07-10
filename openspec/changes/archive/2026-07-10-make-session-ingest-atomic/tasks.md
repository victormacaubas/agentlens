## 1. Regression Coverage

- [x] 1.1 Add a synthetic SQLite-trigger test proving that a late write failure during re-ingest preserves the previously committed events, session facts, skill bridge, and dimensions.
- [x] 1.2 Add coverage proving that a failed first ingest leaves no session-derived rows and does not roll back other successful targets in the same bulk run.

## 2. Atomic Store Interface

- [x] 2.1 Refactor the existing session and dimension upserts into private statement helpers while preserving the transaction-owning behavior of their public single-table interfaces.
- [x] 2.2 Add a typed `upsert_session_grain` store interface that writes events, the session row, skill rows, tool dimensions, and the date dimension inside one per-session transaction.

## 3. Ingest Integration

- [x] 3.1 Change `persist_parsed_session` to delegate the complete derived write set to `upsert_session_grain` and remove the separate dimension-backfill write path.
- [x] 3.2 Confirm `ingest_all` retains per-target exception isolation and counts only fully committed sessions as ingested.

## 4. Verification and Closure

- [x] 4.1 Run the targeted ingest and store tests covering idempotency, full-grain replacement, rollback, and batch isolation.
- [x] 4.2 Run `uv run pytest`, `uv run ruff check`, and `uv run mypy` successfully.
- [x] 4.3 Record the implementation and verification result on the open `ARCH-01` audit finding.
