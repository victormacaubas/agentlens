## 1. The store's concurrency configuration

- [ ] 1.1 Configure `Store` for concurrent writers and inject `Clock` into it. Enable
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

- [ ] 2.1 Add the claim to `models` and its grain to `store`: schema, row mapping, and
  acquire, release, and read operations keyed on the same four-tuple as a verdict,
  carrying an owner and an expiry instant. Acquisition runs in a `BEGIN IMMEDIATE`
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

- [ ] 2.2 Add the verdict read by full natural key to `store`, which does not exist
  today — the only current read is `read_verdicts_for_session`, returning every verdict
  for a session. Cover: an exact match returning the stored verdict with every field
  including provenance, cost, and `scored_at`; a miss on each of the four key
  components independently; a session holding several verdicts where only one matches;
  and a session with no verdicts at all.

## 3. The scoring sequence

- [ ] 3.1 Add the scoring outcome to `models` — scored, reused, claimed-elsewhere, and
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

- [ ] 3.2 Extend `--dryrun` to the reuse and claim paths. Cover: a dry run whose
  identity has a stored verdict reporting that it would be reused and would spend
  nothing; a dry run whose identity is unscored reporting the identity it would claim
  and score without acquiring a claim, calling the judge, or writing a row; and a dry
  run leaving no claim behind that a later real run would trip over. Verify the store
  is byte-identical before and after a dry run.

## 4. The surfaces

- [ ] 4.1 Add the reused, claimed-elsewhere, and behind-current-input shapes to
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

- [ ] 4.2 Mint the owner token once per invocation in `cli.py` and add it to the
  resolved-argument line. Cover: the token appearing in the startup line alongside
  whether scoring was requested and which model; two invocations logging different
  tokens; the token carrying no hostname, username, or path; and the line being emitted
  once on the diagnostic stream so stdout stays parseable. Verify by calling the
  parsing function and `main` directly rather than through flag strings.

## 5. Contracts, documentation, and the merge gate

- [ ] 5.1 Verify the import contracts still hold with reuse in place: `judge` reaching
  neither `store` nor `core`, so no backend can learn a cache exists; `store` importing
  `Clock` from `models` and not from `utils`; and nothing `sqlite3`-shaped appearing in a
  signature `core` exposes now that claims cross the boundary. Verify by asserting
  `lint-imports` reports BROKEN when a `store` import is temporarily added to
  `judge/cli_backend.py`, then removing it — the contract is only trustworthy once it has
  been seen to fail.

- [ ] 5.2 Amend `docs/agentlens-design.md`'s caching section to match what was built:
  name WAL, the explicit lock-wait bound, and `BEGIN IMMEDIATE` as the mechanism behind
  "atomic expiring claims", state that the lease derives from the judge timeout, and
  correct the section's implication that a floating alias can be resolved without a call.
  Record in this change's `design.md` that no new ADR was written and why, so the
  omission reads as a decision. Verify the described mechanism matches the pragmas the
  tests in 1.1 assert on.

- [ ] 5.3 Run `make check` once for the whole change and confirm the full gate passes:
  tests, typing, lint, and every import contract.
