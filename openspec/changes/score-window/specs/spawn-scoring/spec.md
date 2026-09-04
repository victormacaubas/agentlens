## MODIFIED Requirements

### Requirement: Scoring is requested explicitly, one spawn at a time

Scoring SHALL happen only when a caller asks for it. A run SHALL score spawns one at
a time rather than concurrently, and SHALL produce at most one verdict per spawn.

#### Scenario: Scoring is not requested

- **WHEN** a spawn is analyzed without scoring being requested
- **THEN** no judge call is made, no verdict is produced, and the run's observable
  behavior is unchanged from a run made before scoring existed

Rationale: the deterministic path is free and the scored path is not. A default that
spends money on a command users already run for facts is a trap.

#### Scenario: Scoring is requested for one spawn

- **WHEN** scoring is requested for a spawn that has been ingested successfully and no
  verdict exists for its identity
- **THEN** at most one judge call succeeds for it and exactly one verdict is recorded

#### Scenario: Scoring is requested for a window of spawns

- **WHEN** scoring is requested for a window rather than for one spawn
- **THEN** each qualifying spawn in it is scored, and each has at most one verdict
  recorded for its identity

Rationale: what widened is how many spawns one request covers. What did not widen is
the per-spawn invariant, which is what every later comparison depends on.

#### Scenario: Spawns are scored one at a time

- **WHEN** a run covers more than one spawn needing a judge call
- **THEN** no two judge calls are in flight at once

Rationale: a run's spend ceiling is checked between calls, so calls that overlap
could each pass the check and collectively blow through it. Sequential calls are what
makes the ceiling mean anything.

#### Scenario: Scoring is requested twice for the same unchanged spawn

- **WHEN** the same spawn is scored again with an unchanged judge input, rubric
  version, and resolved model
- **THEN** the second run makes no judge call, spends nothing, and reports the verdict
  the first run recorded rather than replacing it

Rationale: this reverses the interim behavior scoring shipped with, where a second run
paid again. The stored shape is unchanged — the second run still produces no second
row — so what moved is cost, which is what the interim scenario was written to make
easy to change.

### Requirement: An unusable judge fails fast and names the cause

When the judge cannot be used, the run SHALL stop promptly with a message
identifying which cause applies, and SHALL NOT silently continue as though scoring
had been declined. A run covering many spawns SHALL reach that stop through its own
failure bound rather than by ending on the first failed call, so that one spawn's
failure is not mistaken for an unusable judge.

#### Scenario: Judge is not installed

- **WHEN** the judge cannot be found
- **THEN** the run reports that it could not be found, names what it looked for, and
  exits with the judge failure code

#### Scenario: Judge is not authenticated

- **WHEN** the judge is present but reports that it is not authenticated
- **THEN** the run reports that authentication is the cause, distinguishes it from
  the judge being absent, and exits with the judge failure code

#### Scenario: Credential lookup fails

- **WHEN** the judge is present and configured but its credential lookup fails
- **THEN** the run reports that cause specifically rather than reporting it as not
  authenticated

Rationale: a helper that cannot reach its secret store is an operator problem
unrelated to agentlens, and reporting it as "not logged in" sends the reader to fix
the wrong thing.

#### Scenario: A failed scoring attempt does not discard deterministic work

- **WHEN** a spawn is ingested successfully and its scoring attempt then fails
- **THEN** the deterministic facts for that spawn remain recorded and the failure is
  reported as a scoring failure rather than an ingest failure

#### Scenario: One spawn's failure is not an unusable judge

- **WHEN** a spawn fails during a run over many spawns while the judge answers for
  others
- **THEN** the run does not report the judge as unusable, does not exit with the judge
  failure code, and reports that spawn as failed

Rationale: an unusable judge and an unlucky spawn produce the same exception at the
call site. What separates them is whether the next spawn also fails, which is
knowable only by trying it.

#### Scenario: An unusable judge is named once, not per spawn

- **WHEN** a run stops because the judge is unusable
- **THEN** the cause is reported as the run's stop reason rather than repeated as a
  distinct failure for every spawn it did not attempt
