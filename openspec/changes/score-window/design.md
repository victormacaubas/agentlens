## Context

See `proposal.md` — Why. What shapes the approach is that almost everything this
change needs already exists and sits on the wrong side of a seam.

- Window resolution, bulk discovery, and the windowed spawn read are built, tested,
  and used only by `report`, which an import contract keeps judge-free.
- The per-spawn scoring lifecycle is built with reuse and claim coordination, and used
  only by `session`, one `--file` at a time. `ScoringStatus` already carries
  `{SCORED, REUSED, CLAIMED_ELSEWHERE, FAILED}` because #27 built that shape for this
  change; `FAILED` is dead code today.
- A judge call's cost is only readable from its response envelope, and
  `SpawnScoringRun` raises when the envelope omits it. Nothing estimates cost ahead of
  a call, and no aggregate ceiling exists — only the per-call `--max-budget-usd`,
  defaulting to $0.50, and a 120s wall-clock limit, both constructor arguments on
  `ClaudeCliJudge` per ADR 0009.
- No retry or fallback logic exists anywhere. #26's design called retries speculative
  for want of evidence of transient failure modes.
- There is no concurrency anywhere in the codebase. The claim mechanism exists for
  races between separate invocations, not for in-process parallelism.

One constraint decides more of the design than any other: **the reuse key includes
`judge_input_hash`, which only exists once a spawn's projection has been rendered.**
No SQL can filter a window down to unscored spawns, so the worklist cannot be a
query.

## Goals / Non-Goals

**Goals:**

- Join the two existing halves without duplicating either. The run composes
  `SpawnScoringRun` per spawn rather than reimplementing scoring.
- Keep the `JudgeBackend` Protocol at exactly one call, so a fake can never disagree
  with the real backend about how many calls happened.
- Keep `session --file` behaving exactly as it does today, including its fail-fast
  exit on a judge failure.

**Non-Goals:**

- No new store query, no store schema change, and no verdict data joined into
  `store/reporting.py`.
- No change to the per-call time or spend bounds, or to where they live.
- No concurrency, and no model fallback across a candidate chain.
- No change to how verdicts are rendered for a single spawn; the run gets its own
  thin summary, not a document.

## Decisions

### The run lives in `core` and composes the per-spawn run

A new `core` module owns the loop, the worklist, the retry budget, the breaker, and
the ceiling. Per spawn it calls the existing `SpawnScoringRun.score`.

*Alternative considered:* widen `SpawnScoringRun` to take a collection. Rejected
because it would put batch policy inside the object that owns one spawn's claim
lifecycle, and every single-spawn test would then be a batch test with a
one-element list.

### Retries live above the judge seam

`JudgeBackend.score` stays one invocation. The run loop decides whether to call
again.

*Alternative considered:* retry inside `ClaudeCliJudge`. Rejected on the same
grounds ADR 0009 used to keep bounds off the Protocol: a backend that retries
internally makes "one call" unobservable, and the fake would have to grow a retry
policy to stay honest. It would also hide retries from the run's own spend accrual.

### The loop catches `JudgeError`; `SpawnScoringRun` keeps raising

`ScoringStatus.FAILED` is constructed by the loop, not by `SpawnScoringRun`.

*Alternative considered:* make `SpawnScoringRun.score` return `FAILED` instead of
raising. Rejected because `session --file` must keep exiting 5 on a judge failure —
that is a live requirement in `spawn-scoring`, and changing it would be a silent
behavior change to a command this slice is not about. Batch tolerance is the batch's
policy, so the batch owns it.

Claim release on failure needs nothing new: #27's `try/finally` already releases the
claim on any exception, which is what makes the spec's *"a failed spawn holds no
claim"* scenario hold.

### Failure classification is behavioral, not taxonomic

`JudgeResponseError` is never retried. `JudgeUnavailableError` is retried up to a
per-spawn attempt budget. Beyond that, the run tolerates a bounded number of
*consecutive* spawn failures and then stops.

*Alternative considered:* subtype `JudgeUnavailableError` into fatal (missing binary,
expired credential) and transient (timeout, subprocess failure), and branch on the
type. Rejected because the taxonomy would then encode a guess about which causes
repeat, and it would miss any cause nobody anticipated — rate limiting, for one,
arrives as an envelope error we do not retry at all. A consecutive-failure bound
learns the same fact from evidence: an unusable judge fails the next spawn too.

The cost of the looser rule is that a missing binary is retried before the run gives
up. Those failures are instant, so the run still stops in well under a second.

### No backoff between attempts

The retried modes are a wall-clock timeout and a subprocess failure. After a 120s
timeout the wait has already happened.

*Alternative considered:* fixed or exponential backoff. Rejected because it needs a
sleep seam that does not exist: time enters this project through `Clock`, a Protocol
earns its place by having two implementations, and seams are never defaulted. Adding
one for a delay no observed failure mode needs is speculative in exactly the way #26
warned about. If a retried mode later turns out to need spacing, the seam gets added
then, with a reason.

### The ceiling is a stop signal, and says so

The run accrues completed-call cost and refuses to start another call once accrued
spend has reached the ceiling. A run can therefore exceed its ceiling by at most one
call's own spend bound.

*Alternative considered:* a hard guarantee, refusing to start a call whose per-call
bound would not fit in the remaining budget. Rejected because it makes the ceiling
unusable at small values — with a $0.50 per-call bound, a $0.40 run ceiling would
score nothing and need a `ConfigError` to explain itself. The looser rule scores one
spawn and reports what it cost, which is what someone setting a small ceiling wants.

*Alternative considered:* bound the run by a spawn count instead. Rejected because
spawn count is not what the operator is trying to control, and per-spawn cost varies
with transcript length.

### A rejected verdict's cost must reach the run

A judge call that completes and then fails local validation has already been paid
for. Today that cost is logged and discarded when `JudgeResponseError` propagates, so
a run's accrual would under-count and its ceiling would leak.

The decision is to carry the spent amount on the error, expressed in builtins
(`float` and two `int`s) so `errors` keeps importing nothing in-project.

*Alternative considered:* accept the leak and document it. Rejected because
`spawn-scoring` already requires that cost is reported for a call that produced no
usable verdict, and a run whose reported spend excludes rejections contradicts the
scenario in this change's own spec that the reported figure is what was actually
spent.

*Alternative considered:* have `SpawnScoringRun` return a `FAILED` outcome carrying
the usage. Rejected for the reason above — it changes `session --file`.

### The worklist is the windowed store read, paired back to source bundles

Discovery and parsing produce source bundles; the existing windowed read decides
which spawns are in scope; the two are joined on `session_id`. Reuse stays a
per-spawn decision inside `SpawnScoringRun`, exactly where #27 put it.

*Alternative considered:* filter the parsed bundles by their own start timestamps and
skip the store read. Rejected because the store is the source of truth for a spawn's
derived facts, and re-deriving window membership in a second place invites the two to
disagree about a boundary.

*Alternative considered:* a new store query joining `fact_session` to `fact_verdict`
to pre-filter unscored spawns. Rejected because it cannot work — the reuse key needs
a rendered projection — and because it would put modeled data into the module whose
rule is that verdict data is never joined there.

### Spawns are scored oldest first

The worklist is ordered by start time, ascending.

*Alternative considered:* leave the order to the query. Rejected because the order
decides which spawns get scored when a run stops at its ceiling, and an operator
running the same window twice should get the rest of it rather than a reshuffle. A
stated order also makes a partial run's result reproducible in a test.

### The run's spend ceiling and the per-call bound are both set at the composition root

`cli.py` constructs the backend with its per-call bound and passes the same figure
into the run's request, which needs it to compute the dry-run upper bound.

*Alternative considered:* have `core` read the per-call default from `judge`.
Technically allowed by the layer map, but it reaches past the seam for a value the
composition root already holds, and it would let the two disagree if a caller ever
constructs the backend differently.

*Alternative considered:* widen the Protocol so the run can ask the backend for its
bound. Rejected by ADR 0009's reasoning about bounds on the Protocol.

### The run ceiling is defaulted, unlike the seam it bounds

`--max-run-cost-usd` defaults to $2.00 and the flag overrides it. That is four times
the per-call bound, so a default run scores a handful of spawns and then stops.

*Alternative considered:* require the flag, on the reasoning that seams are never
defaulted and scoring is never on by default. Rejected because the rule it appeals to
is about injected collaborators, not about operator bounds — a defaulted seam
silently constructs a real judge in a test, while a defaulted ceiling silently
*stops*. The failure modes point in opposite directions, and requiring the flag would
make the safest invocation the most tedious one.

*Alternative considered:* default to unbounded. Rejected because the first thing
anyone does is run it over a window larger than they meant, and an unbounded default
makes that mistake expensive rather than annoying.

### This change writes an ADR

#26 recorded "no retry policy" as a deliberate decision, and this change overturns it
while adding a bound that has no precedent in the codebase. That goes in
`docs/adr/0010`, not only in this change's `design.md`, because a later reader
deciding whether to add a retry somewhere else needs the reasoning without first
finding out which change introduced it.

### Two capabilities, not three

`window-scoring` owns run semantics; `score-command` owns the CLI surface including
its output and exit codes.

*Alternative considered:* a third `score-output` capability, mirroring the split
between `report-command` and `report-output`. Rejected because that split exists so
the report *document*'s format can be specified independently, and a run's outcome is
a thin summary of counts rather than a document.

## Risks / Trade-offs

**The delta is written against `reuse-verdicts`' unsynced requirement text.** #27 is
complete but neither archived nor synced, and its delta modifies the very requirement
this change modifies again. → Archive or sync `reuse-verdicts` before this change is
archived, so the `MODIFIED` base matches what is in the main spec. The requirement
name is unchanged here, which is what makes the ordering recoverable rather than a
conflict.

**A run can exceed its ceiling.** By at most one call's own spend bound, by
construction. → The overshoot is specified rather than hidden, and the reported
figure is the real one.

**The dry-run bound will look alarmingly large.** Multiplying spawn count by a $0.50
per-call bound overstates a realistic run by a wide margin. → It is labeled as an
upper bound rather than an estimate. A calibrated estimate from stored verdicts was
rejected for this slice: it is empty on a first run, which is the path a new user
actually hits.

**A long window is slow.** Sequential calls against a 120s timeout mean a large
window can run for many minutes with no way to speed it up. → The ceiling and the
window selectors are the controls, and a partial run's verdicts are reused by the
next one, so an interrupted run loses nothing. Parallelism is reconsidered once batch
throughput is observable, which is also the condition #27 put on its own open
question about retaining released claims.

**The breaker can stop a healthy run.** A window whose first several spawns all
happen to fail for unrelated reasons aborts a run that would have succeeded. → The
bound counts consecutive failures only, so any single success resets it, and a
stopped run keeps every verdict it recorded.

## Migration Plan

No data migration: the store is a disposable cache and no schema changes. Two
ordering constraints:

1. Archive or sync `reuse-verdicts` before archiving this change.
2. `ScoringStatus.FAILED` and the branch presenting it in `render/summary.py` become
   reachable for the first time. Anything asserting the single-spawn surface never
   produces `FAILED` was asserting an accident, not a behavior.

## Open Questions

Concrete values, following the precedent from #26 where the timeout and per-call
spend ceiling were settled during implementation without changing specs. The specs
name the bounds; they do not name the numbers.

- The per-spawn attempt budget. Starting point: three attempts.
- The consecutive-failure bound. Starting point: three spawns.

The default run ceiling was an open question and is now decided above at $2.00, since
whether the flag is required changes both the CLI surface and whether a
configuration-error path exists.
