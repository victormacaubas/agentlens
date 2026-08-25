## ADDED Requirements

### Requirement: Claim grain and key

The store SHALL hold at most one claim per verdict identity, keyed by the same
session, judge-input hash, rubric version, and judge model identifier that key a
verdict, and each claim SHALL record its owner and its expiry instant.

#### Scenario: One claim per identity

- **WHEN** a claim is acquired for an identity that is already claimed and unexpired
- **THEN** no second claim row exists for that identity

#### Scenario: Two identities are claimed independently

- **WHEN** the same spawn is claimed under two different rubric versions
- **THEN** two claim rows exist, one per identity, and neither blocks the other

#### Scenario: Claim reads go by name

- **WHEN** a claim is written and read back
- **THEN** its identity, owner, and expiry all match what was written, and the read
  addresses columns by name rather than by position

### Requirement: Claims are separate from verdicts

Claim state SHALL live in its own grain, and no verdict row SHALL be written to
represent an unfinished or unscored identity.

#### Scenario: A claimed but unscored identity has no verdict row

- **WHEN** an identity is claimed and its judge call has not yet returned
- **THEN** no row exists for that identity in the verdict grain

#### Scenario: Verdict fields admit no placeholder

- **WHEN** the verdict grain is inspected
- **THEN** every score field is required, and no value stands in for a score that has
  not been produced

Rationale: a claim exists before a verdict does. Representing one as a partly filled
verdict row would require the score columns to admit absence, and a nullable score is
a score that can read as real when it is not.

### Requirement: The store is configured for concurrent writers

The store SHALL be opened in a mode that permits a reader to proceed while a writer
is active, SHALL wait a bounded and explicitly configured time for a contended lock
rather than relying on an implicit default, and SHALL acquire its write lock at the
start of a transaction whose first act is a read that a later write depends on.

#### Scenario: A read is not blocked by an in-progress write

- **WHEN** one process is writing to the store
- **THEN** another process can read from it without waiting for that write to commit

#### Scenario: Contention waits rather than failing immediately

- **WHEN** two processes contend for the store's write lock
- **THEN** the one that did not get it waits up to the configured bound before
  reporting a failure, rather than failing at once

#### Scenario: A read-then-write decision cannot be raced

- **WHEN** two processes concurrently run a transaction that reads whether an identity
  is claimed and then writes a claim if it is not
- **THEN** exactly one of them writes a claim

Rationale: with a deferred transaction, both processes take a read lock, both see the
identity unclaimed, and the second write fails on upgrade — which the first attempt
reports as a store error rather than as a lost race. Taking the write lock up front is
what makes the check and the write one decision.

#### Scenario: Deterministic writes survive the configuration change

- **WHEN** transcripts are ingested under the store's concurrency configuration
- **THEN** the deterministic rows produced are equivalent to those produced before it,
  and re-running the same ingest remains idempotent

Rationale: this configuration is store-wide and touches every writer, not just the
scoring path. The ingest paths need to be exercised under it rather than assumed
unaffected.

### Requirement: Claim expiry is evaluated against injected time

Whether a claim is live SHALL be determined from the time source the run was given
rather than from the host clock read directly.

#### Scenario: Expiry is testable without waiting

- **WHEN** a claim's liveness is evaluated under a time source positioned after its
  expiry
- **THEN** the claim is treated as expired, with no real time having elapsed

Rationale: claim expiry is the fourth use site the `Clock` seam was justified on and
the only one never built. A claim mechanism that read the host clock directly could
only be tested by sleeping through a lease.
