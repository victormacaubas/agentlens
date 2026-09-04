## 1. The run over a window

- [x] 1.1 Add the run outcome to `models` — the four per-spawn statuses already exist,
  so what is new is the run-level shape: counts per status, the run's own aggregated
  judge usage, a stop reason, and how many spawns went unattempted. Build the run in a
  responsibility-named `core` module that resolves the window, ingests, reads the
  window's spawns from the store, pairs each back to its source bundle on `session_id`,
  orders them oldest first, and calls the existing `SpawnScoringRun` per spawn. Cover: a
  window of unscored spawns each getting exactly one verdict; a window with no spawns
  and a window whose filter matches nothing both succeeding with everything at zero
  rather than erroring; a single-spawn window; a window already fully scored reporting
  every spawn reused with no judge call and nothing spent; a window mixing reusable and
  unscored spawns sending only the latter to the judge; four spawns of one agent type in
  one parent session counting as four rather than one; a spawn whose start falls outside
  the window being neither scored nor counted; counts summing to the number of spawns
  covered; and the same window scored twice in a row producing no second verdict row.
  Verify against the fake judge with no patching, assert the fake's invocation count on
  the reuse paths is zero, and assert the oldest-first order directly rather than
  relying on the query's incidental ordering — the order decides what a ceiling-stopped
  run completed.

## 2. Surviving a spawn that fails

- [x] 2.1 Capture a judge failure as that spawn's outcome and continue the run, leaving
  `SpawnScoringRun` raising as it does today so `session --file` keeps its fail-fast
  exit. This is what makes `ScoringStatus.FAILED` reachable for the first time. Carry
  the spend of a call that completed and then failed local validation out to the run, so
  its accrual is not understated — on the error, in builtins, so `errors` keeps
  importing nothing in-project. Cover: a spawn failing mid-window with the spawns after
  it still attempted; a failed spawn's deterministic facts remaining recorded and the
  failure reported as a scoring failure rather than an ingest failure; a failed spawn
  holding no claim afterwards, so the next run may attempt it rather than reporting it
  claimed elsewhere; a failed spawn recording no verdict and reading as unscored later;
  a rejected verdict's already-spent cost appearing in the run's total; and a run whose
  every spawn failed still reporting counts rather than raising. Verify the claim
  invariant through a fresh connection rather than the run's own return value, and
  verify `session --file` still exits with the judge failure code on a judge failure —
  that requirement is live and this task must not move it.

## 3. Bounding the run

- [x] 3.1 Add the per-spawn attempt budget for a judge that could not be reached, above
  the judge seam so `JudgeBackend.score` stays one invocation and a fake cannot disagree
  with the real backend about how many calls happened. No backoff. Cover: a call that
  fails to reach the judge and then succeeds on a further attempt reporting the spawn as
  scored with exactly one verdict; every allowed attempt failing, reported as failed
  with the cause naming exhaustion rather than the underlying error alone; a verdict
  rejected by local validation never being retried and its spend still reported; a spawn
  scored after failed attempts holding exactly one verdict for its identity; and the
  budget applying per spawn rather than being shared, so one spawn's retries do not
  starve the next. Verify the fake records the expected number of invocations for each
  case, since attempt counting is the whole behavior and is invisible in the outcome.

- [x] 3.2 Add the consecutive-failure bound that stops a run whose judge is unusable,
  and report the cause once as the run's stop reason rather than as a failure per
  unattempted spawn. Cover: a large window against an absent judge stopping at the bound
  instead of attempting every spawn, and naming that the judge could not be found;
  failures separated by a success not reaching the bound, so the run covers the whole
  window; a stopped run keeping every verdict it recorded, reporting the counts it
  accumulated, and reporting how many spawns it did not attempt; and a run over a window
  smaller than the bound still stopping cleanly rather than falling off the end. Verify
  that one success resets the counter, which is the property separating an unusable judge
  from an unlucky window and the only reason this bound is safe to set low.

- [x] 3.3 Accrue completed-call spend against the run ceiling and refuse to start a
  further call once it is reached, defaulting the ceiling to $2.00 with
  `--max-run-cost-usd` overriding it. Cover: a run reaching its ceiling with spawns
  remaining, naming the ceiling as the stop reason and reporting the unattempted count;
  the verdicts recorded before the stop remaining recorded and being reused by a later
  run rather than paid for twice; a final call taking the reported total past the ceiling
  by no more than one call's own spend bound, with the reported figure being what was
  actually spent rather than a capped number; reused spawns contributing nothing to the
  accrual, so a window of reuses runs to completion under any ceiling; and a ceiling
  below the per-call bound still scoring exactly one spawn rather than raising. Verify
  the accrual includes a rejected verdict's spend from 2.1, since a ceiling that leaks on
  the failure path is the one that matters.

## 4. The command and its surfaces

- [x] 4.1 Add the `score` command to `cli.py` as the composition root: the window
  selectors declared as a mutually exclusive group rather than hand-validated, `--agent`,
  `--judge-model`, `--max-run-cost-usd`, `--store`, `--format json`, and `--dryrun`. It
  constructs the judge with its per-call bound and passes that same figure into the run's
  request, which needs it for the dry-run bound. Exit 0 when a run covered its window
  whatever its spawns did, exit with the judge failure code when a run stopped on the
  consecutive-failure bound, and exit 0 when a run stopped at its ceiling. Cover: each
  window selector form resolving the same bounds the report command resolves for the same
  input; conflicting, half-supplied, and absent selectors all rejected with the
  configuration-error code before any judge is constructed; an agent filter excluding
  other agent types from both the scoring and the counts; a filter matching nothing
  succeeding; a completed run with failed spawns exiting 0 with the failed count as what
  a caller branches on; a breaker-stopped run exiting with the judge failure code and
  naming the cause; a ceiling-stopped run exiting 0; and the resolved arguments —
  window, filter, requested model, ceiling — logged once on the diagnostic stream.
  Verify by calling the parsing function and `main` directly rather than through flag
  strings, and verify the exit-code mapping is still the single one in `cli.py` rather
  than a second copy for this command.

- [x] 4.2 Add the run summary to `render` in both the thin terminal form and the JSON
  form, keeping stdout to the machine-readable surface and everything about how the run
  went on the diagnostic stream. Cover: the four statuses distinguishable from each other
  without inferring from cost, since reused and claimed-elsewhere both spent zero; counts
  named as spawns on every surface; the run's own spend in dollars and tokens while no
  analyzed-agent usage carries a currency figure anywhere; a stop reason and unattempted
  count present when a run stopped and absent rather than null when it completed; a
  zero-coverage run rendering as covered-nothing rather than as an empty document; a run
  emitting per-spawn progress still leaving stdout as one parseable document; each
  progress line naming the spawn and its agent type so interleaved runs stay readable;
  and evidence or fix text carrying control characters, newlines, and shell-shaped text
  never reaching either surface unescaped or unmarked. Verify nothing rendered is shaped
  like a patch, diff, or runnable command. Note that `render/summary.py` already has a
  branch for `FAILED` that has never executed; check it against what 2.1 actually
  produces rather than assuming it was written correctly against a dead path.

- [x] 4.3 Extend `--dryrun` to the run, reporting the count it would score, the count it
  would reuse, and an upper bound on cost derived from the per-call and run bounds,
  presented as a bound rather than an estimate. Cover: a dry run over an unscored window
  reporting both counts with no judge process started; a dry run over a fully reused
  window reporting the reuses as contributing nothing to the bound; the bound being the
  smaller of the spawn count times the per-call bound and the run ceiling plus one
  call's bound; and a dry run writing neither a verdict nor a claim, with the diagnostic
  stream naming the writes it skipped. Verify the store is byte-identical before and
  after, and verify the bound is never presented with wording that reads as a prediction.

## 5. Contracts, the ADR, and the merge gate

- [x] 5.1 Verify the import contracts still hold with the run in place: `judge` reaching
  neither `store` nor `core`, so retries above the seam cannot leak into a backend;
  `store` gaining no import of `judge`, since no verdict data joins the reporting module;
  and the new `core` module exposing nothing `sqlite3`- or `subprocess`-shaped in a
  signature. Add a contract for the new module if the existing ones do not already cover
  it. Verify by asserting `lint-imports` reports BROKEN when the forbidden import is
  temporarily added, then removing it — the contract is only trustworthy once it has been
  seen to fail.

- [x] 5.2 Write `docs/adr/0010` recording the retry policy, the consecutive-failure
  bound, and the run-level spend ceiling, stating plainly that it supersedes #26's "no
  retry policy" and why a batch of spawns is the evidence that decision was waiting for.
  Record that classification was rejected in favor of a behavioral bound, that the
  ceiling is a stop signal rather than a guarantee, and that the run is sequential
  because a ceiling checked between calls is meaningless if calls overlap. Amend
  `docs/agentlens-design.md` where it describes scoring as one spawn per invocation, and
  amend `docs/ARCHITECTURE.md`'s ADR index. Verify the ADR's stated bounds match the
  values the tests in section 3 assert on.

- [x] 5.3 Run `make check` once for the whole change and confirm the full gate passes:
  tests, typing, lint, and every import contract.
