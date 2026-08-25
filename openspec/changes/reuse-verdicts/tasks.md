## 1. The store's concurrency configuration

- [x] 1.1 Configure `Store` for concurrent writers and inject `Clock` into it. Enable
  WAL journaling, set an explicit `busy_timeout` rather than relying on the implicit
  five-second default, and make `clock` a required constructor argument — never
  defaulted, per CLAUDE.md — updating `cli.py` as the composition root and every other
  call site. Cover: the journal mode and lock-wait bound being the configured values
  after open; a reader proceeding while a writer holds the store; two writers
  contending and the loser waiting the bound rather than failing at once; the existing
  ingest paths producing equivalent deterministic rows under the new journal mode and
  staying idempotent across a repeated run; and a store created by a build without WAL
  opening cleanly. Verify the ingest idempotency assertion runs against the real
  `upsert_batch` path rather than a verdict-only path, since this setting is store-wide
  and the deterministic writers are what it puts at risk.

## 2. The claim grain and the cache lookup

- [x] 2.1 Add the claim to `models` and its grain to `store`: schema, row mapping, and
  acquire, release, and read operations keyed on the session, judge-input hash, and rubric
  version a verdict shares, plus the model the caller *requested* rather than the concrete
  one a verdict carries, since acquisition happens before the envelope exists. Each claim
  carries an owner and an expiry instant. Acquisition runs in a `BEGIN IMMEDIATE`
  transaction so the liveness check and the write are one decision. Cover: acquiring an
  unclaimed identity; two connections racing one identity and exactly one acquiring it;
  an identity whose claim has expired being acquirable; the same owner re-entering its
  own live claim and proceeding rather than being told it is held; release making an
  identity immediately available rather than leaving a successor to wait out the lease;
  two different identities for one spawn claimed independently; the table being created
  on first use against a store that predates it; and reading a claim for an identity
  that has none. Verify the race with two real connections rather than a mocked
  sequence, since atomicity is the one property of this grain that a sequential test
  cannot observe, and verify reads address columns by name.

- [x] 2.2 Add the verdict read by full natural key to `store`, which does not exist
  today — the only current read is `read_verdicts_for_session`, returning every verdict
  for a session. Cover: an exact match returning the stored verdict with every field
  including provenance, cost, and `scored_at`; a miss on each of the four key
  components independently; a session holding several verdicts where only one matches;
  and a session with no verdicts at all.

## 3. The scoring sequence

- [x] 3.1 Add the scoring outcome to `models` — scored, reused, claimed-elsewhere, and
  failed, carrying this run's cost and, where one exists, the verdict and whether it is
  behind the spawn's current input. Build the sequence in `core`: render the prompt and
  hash it, look up the verdict, claim the identity, call the injected judge outside any
  transaction, validate, re-render from source to compare the hash, then commit the
  verdict and release the claim. Cover: a hit returning the stored verdict with no
  judge call and zero cost; a miss on a changed input, a bumped rubric version, and a
  differing resolved model each calling the judge; a requested alias never short-
  circuiting a call, because whether it still resolves to the stored concrete model is
  only knowable from an envelope; a live claim held elsewhere yielding
  claimed-elsewhere with nothing spent and no judge call; an expired claim not blocking;
  a rejected verdict still reporting spend and still releasing the claim; a judge
  failure releasing the claim and leaving the deterministic facts intact; and an input
  that changed during the call being committed under the hash the judge was given and
  reported as behind. Verify against the fake judge with no patching, assert the fake
  records zero invocations on the reuse and claimed-elsewhere paths, and assert no
  transaction is open across the call by observing that a second connection can write
  while the fake judge is mid-call. Note that the recheck must re-render from source
  rather than from the narrative already in memory, which would compare the prompt
  against itself.

- [x] 3.2 Extend `--dryrun` to the reuse and claim paths. Cover: a dry run whose
  identity has a stored verdict reporting that it would be reused and would spend
  nothing; a dry run whose identity is unscored reporting the identity it would claim
  and score without acquiring a claim, calling the judge, or writing a row; and a dry
  run leaving no claim behind that a later real run would trip over. Verify the store
  is byte-identical before and after a dry run.

## 4. The surfaces

- [x] 4.1 Add the reused, claimed-elsewhere, and behind-current-input shapes to
  `render`. Cover: a reused row carrying the full verdict plus a reuse marker and the
  original `scored_at`; the summary naming this run's cost as zero in dollars and
  tokens while stating the verdict was reused; the original judge cost never being
  presented as what this run spent; a claimed-elsewhere spawn carrying no verdict,
  score, or fix key at all rather than nulls, and naming the reason in the summary; the
  four outcomes being distinguishable from each other without inferring from cost,
  since reused and claimed-elsewhere both spent zero and only one has a verdict; a
  verdict behind its current input being marked at read time rather than from a stored
  flag; and evidence and fix text containing control characters, newlines, and
  shell-shaped text still never reaching the summary. Verify no rendered surface emits
  anything shaped like a patch, diff, or runnable command, and that analyzed-agent usage
  still carries no currency figure anywhere.

- [x] 4.2 Mint the owner token once per invocation in `cli.py` and add it to the
  resolved-argument line. Cover: the token appearing in the startup line alongside
  whether scoring was requested and which model; two invocations logging different
  tokens; the token carrying no hostname, username, or path; and the line being emitted
  once on the diagnostic stream so stdout stays parseable. Verify by calling the
  parsing function and `main` directly rather than through flag strings.

## 5. Contracts, documentation, and the merge gate

- [x] 5.1 Verify the import contracts still hold with reuse in place: `judge` reaching
  neither `store` nor `core`, so no backend can learn a cache exists; `store` importing
  `Clock` from `models` and not from `utils`; and nothing `sqlite3`-shaped appearing in a
  signature `core` exposes now that claims cross the boundary. Verify by asserting
  `lint-imports` reports BROKEN when a `store` import is temporarily added to
  `judge/cli_backend.py`, then removing it — the contract is only trustworthy once it has
  been seen to fail.

- [x] 5.2 Amend `docs/agentlens-design.md`'s caching section to match what was built:
  name WAL, the explicit lock-wait bound, and `BEGIN IMMEDIATE` as the mechanism behind
  "atomic expiring claims", state that the lease derives from the judge timeout, and
  correct the section's implication that a floating alias can be resolved without a call.
  Record in this change's `design.md` that no new ADR was written and why, so the
  omission reads as a decision. Verify the described mechanism matches the pragmas the
  tests in 1.1 assert on.

- [x] 5.3 Run `make check` once for the whole change and confirm the full gate passes:
  tests, typing, lint, and every import contract.

## 6. Structure-review fix list

From `.structure-review/2026-08-25-reuse-verdicts/review.md`, in the report's own order of
leverage. Finding 5 — splitting `store/operations.py` — is deliberately not here: it is
pre-existing debt first raised in the 2026-08-19 review and predates this change.

- [x] 6.1 Separate the pre-call claim key from the concrete verdict key, so `judge_model`
  stops meaning two things. A claim is acquired before the call, when only the string the
  caller asked for exists, so its key can never carry a concrete identifier; the current
  shared `VerdictIdentity` therefore both breaks CLAUDE.md's rule that `judge_model` always
  means the resolved identifier and lets one run claim under `sonnet` while another claims
  under `claude-sonnet-5` and both pay. Add a distinct claim identity keyed on the requested
  model, rename the request field to match, and rename the claim table's column. Cover: an
  alias request and a concrete request for one spawn producing two distinct claim identities
  that do not coordinate, asserted rather than left implicit; a claim round-tripping under
  the renamed column; and the concrete-model verdict lookup still missing for an alias.

- [x] 6.2 Add a failure-path test for finalization's shared transaction. Nothing currently
  fails if the verdict upsert and the claim release stop sharing one transaction, so the
  boundary the design calls load-bearing is unprotected. Cover: a forced abort on the claim
  delete leaving the verdict unstored and the claim intact, observed through a fresh
  connection.

- [x] 6.3 Move this run's judge usage onto the scoring outcome as one value, so neither
  renderer re-derives it. Both surfaces currently rebuild the same rule from the status plus
  the verdict's historical token fields, which a third surface would copy again and which
  cannot represent a failed call that spent tokens before failing. Cover: both surfaces
  reporting the same usage without inspecting the status, and a reused verdict's historical
  cost still never presented as this run's.

- [x] 6.4 Give the scoring lifecycle its own owner in a responsibility-named `core` module.
  The seven-step sequence is one 138-line, seven-argument, thirteen-branch helper with
  cleanup state threaded through it, so a test of one transition has to construct every
  collaborator. Keep `analyze_session` as the caller and keep the request separate from the
  injected judge seam. Cover: the existing scoring behavior unchanged through the public
  surface.

- [x] 6.5 Re-run `make check` and confirm the full gate still passes.

## 7. Re-review fix list

From `.structure-review/2026-08-25-re-review.md`. The re-review confirmed 6.2, 6.3, and 6.4
fixed and left one finding partially fixed plus the carried standing debt, all of which is
closed here.

- [x] 7.1 Remove the duplicate `VerdictIdentity`. `models/identity.py` already declared one;
  section 2 added a second, identical copy to `models/claims.py`, so the codebase carried two
  types of the same name and shape. Keep the one in the identity module and leave `claims.py`
  owning only claim vocabulary.

- [x] 7.2 Keep requested aliases out of `VerdictIdentity`, whose docstring promises a concrete
  model. The pre-call probe now goes through a read named for what it does — matching a
  requested model against the concrete `judge_model` a verdict is keyed on — so an alias never
  inhabits the concrete-key type. Both reads share one query. Cover: a concrete request
  hitting and an alias missing against the same stored verdict.

- [x] 7.3 Reconcile the two declarations that still called the claim key the verdict's same
  four-tuple, in `docs/agentlens-design.md` and in task 2.1 above.

- [x] 7.4 Split `store/operations.py`, which had reached 674 lines and 25 top-level functions
  across four concerns. Now four responsibility-named modules — `sessions.py`,
  `agent_definitions.py`, `verdicts.py`, `reporting.py` — with `operations.py` removed. The
  batch write reaches the definition catalog through a transaction-free `catalog_definition`,
  so enrolling it in the caller's transaction stays explicit. This closes debt first raised in
  the 2026-08-19 review.

- [x] 7.5 Replace positional SQLite row access in tests with the store's public surface, in
  `test_core_ingest_run.py` and `test_cli_session.py`. Counting and field assertions now go
  through `read_spawns_in_window`, `read_session`, `read_agent_definition`, and
  `read_verdicts_for_session`, so reordering a column cannot break a test.

- [x] 7.6 Consolidate the five diverged `_write_transcript` copies into canonical
  `write_transcript` and `write_sidecar` builders in `tests/factories.py`. The copies had
  drifted to four different signatures, which is the drift the one-builder rule exists to
  prevent. `test_core_ingest_run.py` keeps a one-line delegator because its fixture is a
  single user record rather than a tool-invocation pair.

- [x] 7.7 Replace the remaining multi-column positional unpack in `test_cli_session.py`, where
  a raw eight-column `SELECT` was destructured by position, so reordering the select list
  silently reassigned every assertion. Now reads the spawn's fields by name off the stored
  `FactSession`. The single-column `COUNT(*)` and `sqlite_master` reads in `test_store.py` and
  `test_store_verdicts.py` are left alone: they predate this change and a one-column result
  carries no column-order dependency.

- [x] 7.8 Run `make check` and confirm the full gate passes.

## 8. Concurrent store opens

- [x] 8.1 Fix `Store.__enter__` failing under concurrent opens, found by running two scoring
  runs against one store rather than by any test. `__enter__` issued
  `PRAGMA journal_mode = WAL` unconditionally, and SQLite runs no busy handler for a
  journal-mode change, so `busy_timeout` did not apply and the statement failed at once with
  "database is locked" whenever another connection held the database. Because scoring opens the
  store about four times per spawn, two concurrent runs could end with one raising `StoreError`
  and exiting 4 — misreporting the coordination this change exists to provide as a store
  failure, and contradicting the `store-schema` requirement that contention waits the
  configured bound rather than failing immediately.

  `_ensure_journal_mode` now reads the mode first and switches only when the file is not
  already in WAL. The mode is a persistent property of the file, so only the connection that
  creates the store ever writes it and every later open returns immediately. A contended switch
  waits out the same `busy_timeout` the rest of the store honors, so a loser of the creation
  race accepts the winner's result instead of failing.

  **Coverage and its limit, stated rather than implied.** The read-first half has a
  deterministic regression test: `test_repeated_concurrent_opens_of_an_existing_store_all_succeed`
  and `test_four_writers_creating_one_store_at_once_all_open_it` both fail against the
  unconditional switch. The bounded-retry half does **not** have one. It is only reproducible
  through concurrent scoring, where it failed roughly one run in three without the retry and
  zero times in three runs with it; a barrier-synchronised open is too coarse to hit the window
  because the mode read itself staggers the threads. The retry is kept on that evidence. If a
  later change wants to remove it, reproduce with two concurrent `analyze_session` calls over
  one store rather than trusting the unit tests, which will stay green either way.
