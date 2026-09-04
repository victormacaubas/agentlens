# Verdict Claims Specification

## Purpose

Coordinating concurrent scorers over a single verdict identity, so two processes
scoring the same spawn pay for it once: what a claim is, who owns one, how long it
lives, what the loser of a race does, and how a claim abandoned by a crashed process
becomes available again.

## Requirements

### Requirement: A claim is acquired before the judge is called

A scorer SHALL acquire a claim before invoking the judge, and SHALL NOT invoke the
judge for an identity it does not hold a claim on.

A claim's identity is keyed on the model the caller **requested**, not the concrete
model a verdict is keyed on. Acquisition happens before the call, and the concrete
identifier is only observable in the response envelope, so a pre-call key cannot
carry it. Two scorers coordinate only when they requested the same model string;
`store-schema` states the accepted consequence of that.

#### Scenario: Claim precedes the call

- **WHEN** a spawn is scored for an identity that no live verdict and no live claim
  exists for
- **THEN** a claim on that identity is recorded before any judge call is made

#### Scenario: Claim acquisition is atomic

- **WHEN** two scorers attempt to claim the same identity concurrently
- **THEN** exactly one of them holds the claim and the other observes it as held,
  with no interleaving in which both proceed to call the judge

Rationale: this is the whole point of the mechanism. A claim that can be acquired
twice is a claim that costs money without buying anything.

### Requirement: A claim names its owner and expires

A claim SHALL record which scorer holds it and the instant it ceases to be
authoritative, and that instant SHALL be derived from the bound on how long a judge
call may run rather than fixed independently of it.

#### Scenario: Claim carries an owner distinct from another scorer's

- **WHEN** two scorers each claim a different identity in the same store
- **THEN** each claim records an owner value, and the two owner values differ

#### Scenario: Expiry cannot precede the longest possible call

- **WHEN** a claim is acquired
- **THEN** the instant it expires is later than the latest instant the judge call it
  guards could still be running

Rationale: a claim that expires while its own judge call is still in flight invites a
second scorer to pay for work already underway, which is the failure the claim exists
to prevent.

### Requirement: Encountering a live claim is a skip, not a failure

A scorer that finds a live claim held by another owner SHALL leave the spawn
unscored, SHALL NOT invoke the judge, SHALL NOT wait for the claim to clear, and
SHALL report the outcome as claimed elsewhere rather than as an error.

#### Scenario: Second scorer finds the identity claimed

- **WHEN** a scorer attempts an identity that another owner holds a live claim on
- **THEN** no judge call is made, nothing is spent, no verdict is written, and the
  outcome reports the spawn as claimed elsewhere

#### Scenario: Claimed elsewhere is not an error exit

- **WHEN** a run's only spawn is skipped because it was claimed elsewhere
- **THEN** the run exits successfully, and does not exit with the judge failure code

Rationale: a lost race is the mechanism working, not a fault. Reporting it as a judge
failure would make a correctly coordinated pair of runs look broken to a script that
branches on exit codes.

### Requirement: An abandoned claim becomes available again

A claim whose expiry has passed SHALL NOT prevent another scorer from acquiring the
identity, and no manual intervention SHALL be required to release it.

#### Scenario: Crashed scorer does not wedge an identity

- **WHEN** a scorer acquires a claim and terminates without releasing it, and the
  claim's expiry then passes
- **THEN** a later scorer acquires the same identity and proceeds to call the judge

#### Scenario: Expired claim is distinguishable from a live one

- **WHEN** a scorer encounters a claim whose expiry has passed
- **THEN** it treats the identity as available rather than as claimed elsewhere

### Requirement: An owner reclaims its own live claim

A scorer that encounters a live claim it already owns SHALL proceed rather than skip.

#### Scenario: Same owner re-enters

- **WHEN** a scorer encounters a live claim whose owner is itself
- **THEN** it proceeds with the identity rather than reporting it as claimed elsewhere

Rationale: without this, a single invocation that reaches the same identity twice
deadlocks against itself and reports its own claim as a competitor.

### Requirement: No transaction spans the judge call

The claim SHALL be durably recorded before the judge call begins, and no database
transaction SHALL remain open for the duration of that call.

#### Scenario: Store is writable during a judge call

- **WHEN** a judge call is in flight for a claimed identity
- **THEN** another process can read and write unrelated rows in the store without
  waiting for that call to finish

Rationale: a judge call takes tens of seconds. Holding a write transaction across it
would block every unrelated writer for the duration and turn one slow call into a
store-wide stall.

### Requirement: A claim does not outlive its work

A claim SHALL be released once the identity it guards has been resolved, whether the
judge call produced a verdict, produced an unusable one, or failed.

#### Scenario: Claim released after a verdict is stored

- **WHEN** a judge call returns a valid verdict and it is committed
- **THEN** the claim on that identity is no longer live

#### Scenario: Claim released after a failed call

- **WHEN** a judge call fails or its verdict is rejected as unusable
- **THEN** the claim on that identity is no longer live, and a later scorer is not
  made to wait out the full expiry before retrying

Rationale: leaving a claim to expire after a fast, clean failure makes a transient
error look like an outage for the length of the lease.
