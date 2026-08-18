# 0008. Session row derivation happens in Python, inside `ingest`

## Status

Accepted

## Context

ADR 0001 left one question open: where does a `fact_session` row get built. The
design doc's original framing was "store the facts, derive the rest," which
reads as a SQL-side aggregation over `fact_tool_event`. `ingest-single-transcript`
is the first change that needs a `fact_session` row to exist at all, so it is the
change ADR 0001 deferred this decision to.

The forcing constraint: several `fact_session` fields are not aggregations of any
tool invocation. `agent_type`, `task_description`, `spawning_tool_use_id`, and
`spawn_depth` come from the `.meta.json` sidecar or its name-resolution fallback.
`source_project` and `session_kind` come from the transcript's file path.
`revision` comes from the file's own `stat()`. A pure-SQL derivation over
`fact_tool_event` has no way to produce any of these; the sidecar and the path
never reach the store as rows.

A split was also considered: counts and token totals derived in SQL, everything
else assembled in Python. Rejected, because it constructs one logical row across
two packages. Neither package then owns whether the row is correct, and
understanding one `fact_session` record means reading both the parser and the
query that finishes it.

## Decision

**The whole `fact_session` row is derived in Python, inside `ingest`, in the same
pass that produces `fact_tool_event` rows.** `ingest` reads the transcript once
and returns both as one `SessionFacts` value. `core` hands that value to `store`,
which persists it verbatim; `store` performs no aggregation of its own beyond the
SQL needed to write and read rows back.

This keeps a `fact_session` row's correctness owned by exactly one package, and it
is consistent with the design's other soundness rule: `SessionFacts` is a type
that is always safe to persist, never a partially-derived value another layer
has to finish.

## Consequences

- **A schema change cannot be satisfied by re-running a query.** Adding or
  changing a derived field on `fact_session` means re-reading the source
  transcripts, not writing a migration or a backfill query against the existing
  store. ADR 0003 already established that the store is a disposable,
  rebuildable cache for exactly this reason, so the cost is one this project
  already decided to accept — this decision is what makes that acceptance land
  on `ingest` specifically.
- **`ingest` is the one place that must stay current with the source format.**
  Every fact enumerated in `ingest-single-transcript`'s design doc — the two
  `tool_result.content` shapes, `is_error` present-and-true, fragmented
  assistant turns sharing one `message.id`, cumulative `usage` on interior
  fragments — is knowledge `ingest` alone holds. `store` and `render` consume
  `FactSession`/`FactToolEvent` as plain data and never need to know how a field
  was derived.
- **`core` orchestrates, it does not compute.** The cross-stage wiring ADR 0001
  assigns to `core` stays limited to sequencing calls (parse, persist, read back,
  render); no derivation logic accumulates there as a shortcut around the
  `ingest`/`store` boundary.
- **Testing a derived field means testing `ingest` against a synthetic
  transcript**, not testing a query against seeded rows. This is consistent with
  ADR 0006's fixture strategy: the belief about the source format lives in
  `tests/factories.py`, and a wrong belief is wrong for every derived field at
  once rather than only for the ones expressed in SQL.
