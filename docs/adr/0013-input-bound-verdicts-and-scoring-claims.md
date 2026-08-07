# ADR 0013: Verdicts bind to prepared input and paid scoring uses claims

## Status

Accepted

## Context

ADR 0010 requires matching rubric, concrete model, and judge context before verdicts are
comparable. That identity is incomplete when a session transcript changes under the same
session ID. Re-ingest can replace deterministic facts while a verdict keyed only by
session, rubric, and model remains a cache hit. Reports then join fresh facts to a score
produced from older input.

Scoring also performs an external paid call between its unscored query and verdict write.
Two processes can select the same row, both pay the judge, and race to replace one verdict.
A database transaction cannot remain open during the judge call without blocking unrelated
work and increasing failure risk.

Floating model aliases add a second orchestration stage. One successful call must resolve
the concrete model before the loop can query the final cache identity. A session-specific
failure in the first candidate must not wedge healthy sessions, and the configured maximum
must cover both stages.

## Decision

The SHA-256 of the exact prepared transcript view is `judge_input_hash`.
`fact_session` stores the current hash, and `fact_verdict` uses
`(session_id, judge_input_hash, rubric_version, concrete judge_model)` as its identity.
Finalization verifies that the session's current hash still matches the scored input.
Historical verdicts remain stored but do not satisfy cache queries for changed input.

Before a judge call, the scoring loop atomically acquires an owner-scoped, expiring SQLite
claim for the target verdict identity. It commits the claim before calling the judge and
holds no transaction during the external call. Only the claim owner can finalize or
release it. Another process can recover the work after expiry.

Before an alias resolves, the configured alias participates in the temporary claim
identity. The loop tries candidates in stable order until one succeeds, candidates are
exhausted, the invocation-wide attempt budget is consumed, or the existing consecutive
failure threshold aborts. Session-specific failures count as skips and consume attempts;
judge unavailability still propagates immediately. Once resolved, the loop re-queries by
the concrete model and current input hashes.

## Consequences

- Unchanged prepared input remains a cache hit. A transcript or deterministic-view change
  creates a new scoreable identity without deleting historical verdict provenance.
- An ingest that completes while scoring is in flight causes stale finalization to fail
  instead of attaching the verdict to newer facts.
- Concurrent processes do not duplicate paid work for one identity. Expiry makes claims
  recoverable after crashes, at the cost of a bounded delay before another run can retry.
- Alias runs can cost one resolution call even when all sessions are already scored under
  the concrete model. This is the price of learning where the alias points without a
  separate alias cache.
- `--max-sessions` counts judge attempts, including failed resolution candidates, rather
  than only successful persisted verdicts.
- Claims add transient store state. They are coordination records, not deterministic facts
  or modeled verdicts, and reports do not read them.
