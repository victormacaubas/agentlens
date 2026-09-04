# 0010. Window-run retry policy, consecutive-failure breaker, and spend ceiling

## Status

Accepted

## Context

`score-single-spawn` (#26) recorded "no retry policy" as a deliberate decision:
*"A judge failure fails the scoring attempt. Adding retries before there is
evidence of transient failure modes would be speculative."* At the time, the
only caller was `session --file`, scoring exactly one spawn per invocation.
There was no batch to observe a failure pattern across, so there was no
evidence a retry policy could be built from, and no way to bound a run's total
spend beyond the per-call `--max-budget-usd` ceiling ADR 0009 already put on
`ClaudeCliJudge`.

`score-window` (this change) is that batch: `agentlens score` reads a whole
window and calls the judge once per unscored spawn. That reframes the
question #26 declined to answer. A window can be large, a judge can be
unreachable for reasons a single retry inside one call cannot distinguish
(a missing binary, an expired credential, a transient network failure, a rate
limit that arrives as an envelope error rather than an unreachable-backend
error), and nothing before this change stopped a run from grinding through
every spawn in an unusable state, or from spending without a ceiling of its
own beyond the per-call bound.

Three things needed deciding: whether a spawn whose judge call fails should be
retried, whether a run should give up on a judge that looks unusable rather
than attempting every spawn in the same failing state, and whether a run's
total spend should be bounded independently of the per-call bound.

## Decision

**A spawn whose judge call fails because the backend could not be reached is
retried up to three attempts, back-to-back, with no backoff.** A verdict the
judge answered but that failed local validation (`JudgeResponseError`) is
never retried — the failure is in the rubric, the prompt, or the model's
conformance, and none of those improve on a second try; retrying it spends
money to report a bug as a flake. This distinction is behavioral, not
taxonomic: no attempt is made to classify *why* the backend was unreachable
(missing binary vs. expired credential vs. a genuinely transient failure).
A missing binary is retried three times before the run gives up, which costs
under a second since each such failure is instant. Retries happen above the
`JudgeBackend` seam, in the run loop, by calling the existing per-spawn
`SpawnScoringRun` again rather than inside `ClaudeCliJudge` — the same
reasoning ADR 0009 used to keep bounds off the Protocol applies here: a
backend that retries internally makes "one call" unobservable, and a fake
judge would have to grow a retry policy of its own to stay honest about call
counts. No backoff, because the retried failure modes (a 120s wall-clock
timeout, a subprocess failure) have already either waited long enough or
failed instantly; a delay seam does not exist in this project (time enters
through `Clock`, and a Protocol earns its place by having two
implementations), and adding one for a delay no observed failure mode needs
would be exactly the speculation #26 warned against.

**A run bounds the number of *consecutive* spawn failures it tolerates at
three, and stops rather than attempting the rest of the window once that
bound is reached.** The counter increments on any spawn that ends failed
(whether from exhausting its retry budget or from an unretried rejected
verdict) and resets to zero on any spawn that ends scored; a reused or
claimed-elsewhere spawn touches neither counter at all, since neither one calls
the judge and so neither carries any signal about the judge's health. This is
a behavioral bound, not a classification of causes: it learns "the judge is
unusable" from the same evidence a human would — the next spawn failing too —
rather than from a guess about which failure causes repeat. The trade-off is
that a window whose first several spawns happen to fail for unrelated,
non-repeating reasons can abort a run that would otherwise have succeeded;
counting only *consecutive* failures is what keeps that risk survivable, since
any single success resets the counter, and a stopped run keeps every verdict
it already recorded.

**A run accrues the real cost of its completed judge calls against a
ceiling, and refuses to start scoring a further spawn once accrued spend has
reached it.** The ceiling is `--max-run-cost-usd`, defaulting to $2.00 — four
times the existing $0.50 per-call default from ADR 0009, so a default run
scores a handful of spawns before stopping. The check is a loose one: it
compares the running total against the ceiling *before* attempting the next
spawn, not before starting a call whose cost is not yet knowable (a call's
cost is only observable in its response envelope), so a run's real spend can
exceed the ceiling by at most one call's own spend bound. The reported figure
is always what was actually spent, never a number capped at the ceiling. A
reused or claimed-elsewhere spawn never advances the running total, so a
window of only-reusable spawns runs to completion under any ceiling greater
than zero regardless of size, and a ceiling smaller than one call's own bound
still lets exactly one spawn score rather than raising a configuration error.
A rejected verdict's already-spent cost (carried on `JudgeResponseError` since
a call that answered and then failed validation has already been paid for)
counts toward this accrual — a ceiling that ignored it would leak on exactly
the failure path that matters.

**The ceiling is defaulted, even though injected seams in this project never
are.** ADR 0004's rule that seams are never defaulted is about injected
collaborators — a defaulted `JudgeBackend` would let a test silently construct
the real one. `--max-run-cost-usd` is an operator bound, not a collaborator,
and the failure mode runs the other way: a defaulted seam risks a hidden real
call, but a defaulted ceiling risks nothing but an early, safe stop. Requiring
the flag would make the safest invocation the most tedious one to type; an
unbounded default would let the first mistaken `--since` cost far more than
intended, which is the mistake a new user is likeliest to make.

## Consequences

- **This supersedes #26's "no retry policy," now that a batch is the evidence
  that decision was waiting for.** A single-spawn invocation
  (`session --file`) still has no retry and still fails fast on the first
  judge error, unchanged — `SpawnScoringRun` keeps raising exactly as it did
  before this change. The retry, breaker, and ceiling exist only in the batch
  loop `agentlens score` runs, in `core/window_scoring.py`.
- **A breaker set this low can abort a healthy run on an unlucky window.**
  Accepted per the trade-off above: the bound counts consecutive failures
  only, and a stopped run's recorded work is never discarded, so re-running
  the same window after fixing the judge picks up exactly where the aborted
  run left off, at no extra cost for what already scored.
- **The run is sequential, not concurrent.** A ceiling checked between calls
  is only meaningful if calls do not overlap; nothing in this project
  introduces concurrency, so a large window scores at whatever rate one
  120-second-bounded call at a time allows. The ceiling and the window
  selectors are the only controls over how long or how expensive a run gets.
- **Classification was rejected in favor of a behavioral bound**, which means
  a future failure mode nobody has seen yet (a new kind of rate limit, a new
  CLI error shape) is handled the same way an already-known one is, without
  this policy needing to learn about it first. The cost is that no single
  spawn's failure names a specific diagnosable cause beyond what its own
  exception's message already carries; the run-level `stop_reason` names only
  *that* the judge looked unusable or *that* the ceiling was reached, not why.
- **A later change that wants per-mode backoff or a different retry budget for
  a newly observed failure pattern should add the reasoning here or in a
  successor ADR**, not by re-deciding "no retry policy" from scratch — that
  question is now closed, and the concrete numbers (three attempts, three
  consecutive failures, a $2.00 default ceiling) are what any change to this
  policy is a change *from*.
