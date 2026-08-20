## Why

Persisting one parsed session currently commits its events, session row, skill bridge, and dimension backfills independently. A late write failure can therefore leave the disposable store with a mixed-version session until the next successful ingest, violating the existing full-grain replacement guarantee.

## What Changes

- Persist every table derived from one parsed session in a single SQLite transaction.
- Roll back the complete per-session write set when any write fails, preserving the prior version of an existing session or leaving no rows for a new session.
- Keep bulk-ingest isolation at the session level so one failed target does not roll back other successfully ingested sessions.
- Add a synthetic regression test that forces a late database failure and verifies rollback across the session grain and dimension backfills.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `session-parser`: Strengthen idempotent ingest so full-grain replacement and its dimension backfills are atomic per session.

## Impact

- `src/agentlens/store/__init__.py`: add a single atomic store interface for the complete per-session write set and refactor statement-level helpers so nested commits cannot occur.
- `src/agentlens/ingest/__init__.py`: replace separate store calls with the atomic interface.
- `tests/test_ingest.py` and, if needed, `tests/test_store.py`: cover rollback on a forced late write failure while retaining idempotency and batch error isolation.
- No schema, CLI, dependency, or migration changes.
