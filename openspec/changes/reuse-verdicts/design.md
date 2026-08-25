## Context

See `proposal.md` — Why. This section records only the state that shapes the approach.

**Stacked on `score-single-spawn`.** Its sections 5 and 6 are open, so the scoring run
this change extends does not exist yet: nothing in `core/` composes narrative
extraction, the judge, validation, and persistence. The pieces below it are done —
`models`, the `judge` package, and `fact_verdict` with its upsert.

**What exists to build on.** The verdict natural key is
`(session_id, judge_input_hash, rubric_version, judge_model)`, a composite primary
key on `fact_verdict`, with no foreign key to the session row. `judge_input_hash` is
the SHA-256 of the exact rendered prompt. `JudgeBackend.score(prompt, *, model)`
returns a `JudgeResponse` whose `resolved_model` comes from the single key of the
envelope's `modelUsage`.

**Three gaps this change lands in.** There is no read of a verdict by its natural key
— the only one is `read_verdicts_for_session(session_id)`, returning every verdict for
a session. The store sets no pragmas at all: no WAL, no `busy_timeout`, no `timeout=`
on `sqlite3.connect`, and transactions open with a bare `BEGIN`, which is deferred.
And `Clock` has never been injected into `store`, though ADR 0004 named expiring
verdict claims as one of the four sites justifying that seam.

**Documented but unspecified.** The design doc and ADR 0004 commit to "atomic expiring
claims" existing. Neither names a mechanism — no journal mode, no lock-wait bound, no
lease policy. Filling that in is this document's main job.

## Goals / Non-Goals

**Goals:**

- Establish which hash means "what the judge saw", and make the finalization check use
  only that one.
- Specify the claim mechanism concretely enough that its atomicity is testable rather
  than assumed.
- Keep the store's concurrency configuration a stated property rather than a set of
  incidental defaults.
- Keep reuse invisible to `judge`, so no backend can disagree with another about
  whether a call happened.

**Non-Goals:**

- No new ADR. ADR 0003 and ADR 0004 already commit to the guarantee; this document
  supplies the mechanism they left open. If a second capability later needs the same
  concurrency primitives, that is when the store's concurrency model earns an ADR of
  its own.
- No forced re-score. There is no flag to spend money on an identity that already has
  a verdict. Deliberate cache invalidation is what `rubric_version` is for.
- No waiting on a contended claim. Skip is the only loss behavior.
- No cross-invocation memory of anything. A run's owner token, and any judgment it
  formed about a model candidate, die with it. Candidate health belongs to #28.

## Decisions

### Finalization compares `judge_input_hash`, not a session-level hash

ADR 0003 says finalization "rechecks the session's current input hash". Three hashes
could answer to that phrase:

| Hash | Where | Covers |
|---|---|---|
| `judge_input_hash` | verdict key; SHA-256 of the rendered prompt | exactly what the judge saw |
| `revision_content_hash` | `fact_session` | the transcript file's bytes |
| `derivation_fingerprint` | `fact_session` | transcript plus sidecar, agent definition, skill inventory, name resolution |

**Decision: re-render the narrative at finalization and compare `judge_input_hash`.**

The rendered prompt is built from the spawn narrative — task prompt, assistant
messages, tool events. So `derivation_fingerprint` moves when an agent definition or
skill inventory changes, none of which the judge reads; rechecking it would report
verdicts as behind for inputs that never reached the model, and re-spend on them.
`revision_content_hash` has the mirror-image fault: the prompt is a bounded projection
with elision, so a transcript edit inside an elided region leaves the prompt
byte-identical and the verdict exactly as valid as it was.

*Alternative considered:* compare `revision_content_hash`, which is already stored and
needs no re-render. Rejected because it is wrong in the direction that costs money —
it reports valid verdicts as stale — and the re-render is cheap next to the judge call
it guards.

### A failed recheck stores the verdict under the hash the judge saw

Because the verdict's identity contains `judge_input_hash` and the row carries no
foreign key to `fact_session`, a verdict written under the hash the judge was shown
cannot be read as a verdict of the newer input. A later request resolving to the new
hash misses and re-scores. **The identity is the guarantee.**

That collapses the recheck from a correctness guard into a reporting step: it tells the
operator that what they just paid for is already behind. It also means **no
`superseded` column.** A stored flag would be wrong the moment the input moved again,
and wrong in the other direction if an edit is reverted. Whether a verdict is behind is
computed at read time by comparing its `judge_input_hash` against the current one.

*Alternative considered:* discard the verdict and exit non-zero. Rejected because it
throws away a paid-for verdict to avoid a misattribution that cannot occur.

### Claims are their own grain, not columns on `fact_verdict`

A claim exists before a verdict does. Every one of `fact_verdict`'s twelve non-key
columns is `NOT NULL`, including all five scores, so representing a claim as a
partly-filled verdict row would require either making score columns nullable — which
lets a missing score read as a real one — or writing sentinels, which is worse.

The claim grain is keyed on the same four-tuple as a verdict and carries an owner and
an expiry instant. It holds no verdict data.

### Acquisition is `BEGIN IMMEDIATE`, and the store gains a concurrency configuration

Claim acquisition is a read-then-write decision: check whether a live claim exists,
write one if it does not. Under the current bare `BEGIN`, both racing processes take a
read lock, both observe the identity unclaimed, and the second write fails on lock
upgrade with `SQLITE_BUSY` — which surfaces as a store error rather than as a lost
race. Taking the write lock before the read is what makes the check and the write one
decision.

**Decision: three store-wide settings, all stated rather than defaulted.**

- **`PRAGMA journal_mode=WAL`.** A reader proceeds while a writer is active. Without
  it, one in-flight write blocks every reader, which matters more once two processes
  are expected rather than tolerated.
- **An explicit `busy_timeout`.** The implicit five-second default is a value nobody
  chose. Making it explicit makes it reviewable and tunable.
- **`BEGIN IMMEDIATE` for the acquisition transaction.** Not for every transaction —
  the deferred `BEGIN` in `upsert_session` and `upsert_batch` is correct for a
  write-only batch and taking the write lock earlier there would only widen contention.

This is store-wide and touches every writer, which is why a spec scenario covers
ingest remaining idempotent under it rather than leaving that assumed.

*Alternative considered:* an advisory lock file outside SQLite. Rejected because it
puts the coordination in a different durability domain from the thing being
coordinated, so a crash can leave the two disagreeing, and because it needs its own
staleness story that expiring rows already give for free.

### The owner is a random token minted once per invocation

Uniqueness and unforgeability are all "owner-scoped" requires. A random token needs no
OS introspection, is immune to PID reuse, and carries no hostname, username, or path
into a diagnostic stream that gets pasted into issues. It is logged once in the
resolved-argument line, which is what makes a claim held elsewhere traceable to a
process.

*Alternative considered:* `hostname + PID`. Rejected because PID reuse makes it
ambiguous without also carrying process start time, and because it leaks host detail
into logs for no gain.

### The lease is derived from the judge timeout, never set independently

The judge's wall-clock timeout is already a constructor argument on the backend. The
claim's expiry is that bound plus a margin. A fixed independent lease would drift
silently the first time the timeout is tuned, and a lease shorter than the call it
guards invites a second scorer to pay for work already in flight — the exact failure
the claim exists to prevent.

### A lost race is a return value, not an exception

ADR 0005 sets the precedent directly: a stale snapshot "is a decision to skip a
replacement, not a failure, so it is a return value rather than an exception," which is
why no `StaleSnapshotError` exists. A live claim held by another owner is the same
shape. Raising a `JudgeError` would exit 5 and tell a script that the judge failed when
the mechanism worked exactly as designed.

The scoring outcome therefore distinguishes four cases — scored, reused,
claimed-elsewhere, failed — and only the last is an error. This is also the shape #28
needs when it counts a window's spawns, so it is built once here.

### Reuse is decided in `core`, above the judge seam

The cache lookup happens in the scoring run, before a backend is reached. `judge` never
learns that a cache exists.

This is not only layering hygiene. If reuse lived in the backend, the fake and the real
implementation could disagree about whether a call happened, and the fake is what every
test asserts against — so the one thing this change must prove would be the one thing
the tests could not see.

### `Clock` is injected into `Store`

The liveness predicate appears in acquisition, in the reuse lookup, and in release.
Threading a `now` parameter through each puts the same value in three signatures, and
omitting it from one silently disables expiry there.

*Alternative considered:* keep `store` clock-free and pass `now` explicitly, which is
the existing precedent — `FactVerdict.scored_at` arrives pre-stamped from `core`.
Rejected in favor of the reading ADR 0004 already committed to, which names
"expiring verdict claims (`store`)" as a `Clock` site outright. The cost is a required
constructor argument on `Store` and a mechanical update to its call sites; per
CLAUDE.md it is required and never defaulted, so no call site can silently construct
its own.

### The sequence, and where the transaction boundaries fall

```
core.score_spawn(spawn, judge, store, clock)
  |
  1. render prompt -> judge_input_hash              (no store access)
  2. TXN: read verdict by natural key               -> hit?  -> reuse, done, $0.00
  3. TXN(IMMEDIATE): read live claim, write claim    -> held? -> claimed-elsewhere, done
     COMMIT                                          <-- no transaction past here
  4. judge.score(prompt, model=...)                  (tens of seconds, no txn open)
  5. validate verdict                                (rejection still reports spend)
  6. re-render prompt -> compare hash               -> differs? -> mark behind
  7. TXN: upsert verdict under the hash from step 1, release claim
     COMMIT
```

Steps 2 and 3 are separate transactions on purpose: the lookup is read-only and should
not take a write lock on the common cache-hit path, which is the path a loop hits.

Step 3 commits before step 4 begins, which is what "no transaction spans the judge
call" means concretely. Holding a write lock across a call that takes tens of seconds
would block every unrelated writer for its duration.

## Risks / Trade-offs

**WAL is a store-wide change and this ticket is about verdicts.** → The store is a
disposable cache rebuildable from `.claude/`, so the blast radius is recoverable by
deletion. A spec scenario covers ingest staying idempotent under the new journal mode
rather than assuming it, and WAL adds `-wal` and `-shm` sidecar files next to the
database that anything cleaning up the store must now expect.

**A claim leaks if the process dies between step 3 and step 7.** → That is what the
expiry is for. The window is bounded by the lease, and the lease is bounded by the
judge timeout plus a margin. The cost of the leak is one skipped spawn for less than
one judge-call duration.

**A reused verdict may have come from different model weights than its
`judge_model` string implies.** → Inherited, not introduced: `claude-sonnet-5` carries
no date stamp, so it floats across point releases. Reuse makes the window wider, since
a stored verdict is now returned indefinitely rather than being re-bought. The gap is
recorded in `score-single-spawn` and closing it needs an identifier the envelope does
not offer. `rubric_version` remains the deliberate invalidation lever.

**A user who wants a fresh verdict for an unchanged spawn has no way to ask.** →
Accepted. Named in Non-Goals so it is a decision rather than an omission; a force flag
would be its own change with its own spend-confirmation question.

**Re-rendering the prompt twice per scored spawn.** → Cheap next to a judge call, and
the alternative is holding the first render in memory across the call, which the
recheck would then be comparing against itself rather than against the source.

## Migration Plan

No data migration. `fact_verdict` is unchanged: the claim grain is additive, and the
recheck adds no column. Existing verdicts become reusable immediately, since their
identity already carries everything a lookup needs.

The store is created on first use and rebuilt from `.claude/` when deleted, so
enabling WAL needs no migration step — but the journal mode change is visible in the
sidecar files it creates, so a store opened by an older build afterward should be
verified rather than assumed compatible.

Rollback is reverting the change: the claim grain becomes an unread table and verdicts
go back to being re-bought. Nothing written under this change is unreadable without it.

## Open Questions

- The concrete `busy_timeout` value and the claim's margin over the judge timeout. Both
  are tuning constants that do not change the specs, the approach, or the task
  breakdown, and both want a number chosen against a real contended run rather than
  guessed here.
- Whether the claim grain should retain released claims as history rather than deleting
  them. Only worth answering once #28 makes batch throughput observable; nothing in
  this change reads a released claim.
