## Why

Scoring a spawn today pays the judge every time, including for a spawn whose inputs
have not moved. That makes the tool expensive to run twice and unusable in a loop,
and Phase 3's exit criterion is explicitly "re-runs hit cache". `score-single-spawn`
named this as its own top risk and deferred it here.

Reuse is also what keeps a score honest. A verdict is only meaningful against the
exact input that produced it, so the same mechanism that avoids re-paying is the
mechanism that stops a number from attaching itself to the wrong run.

## What Changes

- **A verdict is looked up before it is bought.** A spawn whose prepared input,
  rubric version, and resolved model all match a stored verdict returns that verdict
  and calls no judge. Cost for the run is zero, reported as zero, and marked as
  reused alongside the original `scored_at`.

- **A new claim grain coordinates concurrent scorers.** A scorer acquires an
  expiring, owner-scoped claim on a verdict identity before calling the judge. A
  scorer that finds a live claim held by someone else skips the spawn and reports it
  as claimed elsewhere. An abandoned claim expires and becomes available again, so a
  crashed process cannot wedge an identity permanently.

- **The store gains a concurrency configuration it has never had.** WAL journaling, an
  explicit lock-wait timeout, and `BEGIN IMMEDIATE` for claim acquisition. Claim
  acquisition is not atomic without them, and the current deferred-transaction plus
  implicit five-second default produces `SQLITE_BUSY` under exactly the race this
  change exists to close.

- **`Clock` reaches `store` for the first time.** ADR 0004 justified the `Clock` seam
  on four use sites and claim expiry is the one never built. Claims are the reason it
  was named.

- **Finalization rechecks the rendered prompt hash, not a session-level hash.** A
  verdict whose input moved while the judge was thinking is stored under the hash the
  judge actually saw and reported as already behind the current input.

- **No database transaction is held open across the judge call.** Claim acquisition
  commits, the judge runs outside any transaction, and finalization opens its own.

## Capabilities

### New Capabilities

- `verdict-claims`: coordinating concurrent scorers over a verdict identity — what a
  claim is, who owns one, how long it lives, what happens to the loser of a race,
  and how an abandoned claim is reclaimed.

### Modified Capabilities

- `spawn-scoring`: adds the cache-hit path (an unchanged identity returns its stored
  verdict and invokes no judge), the finalization recheck against the rendered prompt
  hash, and the rule that a lost claim is a skip rather than a failure. **This
  capability does not exist under `openspec/specs/` yet** — it is introduced by the
  delta in the unarchived `score-single-spawn` change, so this change's delta stacks
  on that one.

- `store-schema`: adds the claim grain and its key, the expiry and ownership columns,
  and the store's concurrency configuration as a stated property rather than an
  implementation accident.

- `session-report`: adds the reused-verdict shape — zero cost for the run, a reused
  marker, and the original `scored_at` — and the claimed-elsewhere outcome, so a
  skipped spawn is distinguishable from a spawn that was never scored.

- `session-command`: adds the owner token to the resolved-argument log line, which is
  what makes a claim held by another process diagnosable from the outside.

## Impact

**Stacked on `score-single-spawn` (#26).** That change's sections 5 and 6 are still
open, so the orchestration this one extends does not exist yet: nothing in
`core/` composes narrative extraction, the judge, validation, and persistence today.
This change assumes that seam lands first. The branch stack mirrors the spec stack.

**Affected packages.** `store` gains the claim grain, the natural-key verdict read
that does not exist today (the only current read is
`read_verdicts_for_session(session_id)`, which returns every verdict for a session),
its first `Clock` dependency, and its connection configuration. `core` gains the
lookup-claim-score-finalize sequence. `render` gains the reused and
claimed-elsewhere shapes. `judge` is untouched — reuse is a decision made before the
backend is reached, and `judge` must not learn that a cache exists.

**No new runtime dependency.** Claims are SQLite rows under the existing
`sqlite3` usage. The dependency set stays closed per ADR 0002.

**Exit codes are unchanged.** A lost claim is an outcome value, not an error, per
ADR 0005's precedent that a stale snapshot "is a decision to skip a replacement, not
a failure, so it is a return value rather than an exception". Nothing here maps to a
new code, and 5 continues to mean a judge failure.

**Store-wide risk.** WAL and the lock-wait timeout touch every write path, not just
verdicts. The store is a disposable cache rebuildable from `.claude/`, which is what
makes this affordable, but the ingest paths need to be exercised under the new
journal mode rather than assumed unaffected.

**Deferred.** Batch scoring across a window (#28), including the model-candidate
health and attempt budget that moved there during scouting. Modeled scores in the
windowed report (#29).
