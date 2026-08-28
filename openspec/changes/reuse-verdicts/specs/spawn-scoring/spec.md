## ADDED Requirements

### Requirement: An unchanged identity is reused rather than re-scored

When a stored verdict exists for the identity a scoring request resolves to, that
verdict SHALL be returned without invoking the judge, and the run SHALL spend
nothing.

#### Scenario: Re-scoring an unchanged spawn calls no judge

- **WHEN** scoring is requested for a spawn whose judge input, rubric version, and
  resolved model all match a stored verdict
- **THEN** no judge call is made, nothing is spent, and the stored verdict is what the
  run reports

#### Scenario: A changed judge input is a miss

- **WHEN** the same spawn is scored again after its judge input changed
- **THEN** the stored verdict is not reused, the judge is called, and a verdict is
  recorded under the new identity while the earlier one remains

#### Scenario: A bumped rubric version is a miss

- **WHEN** the same spawn and unchanged judge input are scored under a rubric version
  that differs from a stored verdict's
- **THEN** the stored verdict is not reused and the judge is called

#### Scenario: Reuse is decided before the judge is reached

- **WHEN** a scoring request resolves to a stored verdict
- **THEN** no judge invocation is constructed and no judge process is started

Rationale: reuse that is decided inside the backend would make every backend
responsible for knowing a cache exists, and would make a fake backend able to
disagree with the real one about whether a call happened.

#### Scenario: The requested alias is not what decides a hit

- **WHEN** scoring is requested with a floating model alias for a spawn whose stored
  verdict was recorded under the concrete model that alias currently resolves to
- **THEN** the judge is called, because whether the alias still resolves to that
  concrete model is not knowable without calling it

Rationale: the resolved model is part of the identity and is only observable in a
response envelope. Treating the alias as a stand-in would return a verdict from a
model the caller is no longer asking for.

### Requirement: A verdict is finalized against the input the judge was shown

Before a verdict is committed, the run SHALL confirm that the judge input it was
produced from still matches the spawn's current judge input, and SHALL record the
verdict under the input the judge was actually shown regardless of the outcome of
that check.

#### Scenario: Input unchanged during the call

- **WHEN** a judge call completes and the spawn's judge input is unchanged from the
  one the call was given
- **THEN** the verdict is committed under that identity and the run reports it
  normally

#### Scenario: Input changed during the call

- **WHEN** an ingest lands while a judge call is in flight and changes the spawn's
  judge input
- **THEN** the verdict is recorded under the judge input the call was actually given,
  the run reports that the verdict is already behind the spawn's current input, and
  the spend is reported rather than discarded

Rationale: the verdict's identity contains the input hash it was produced from, so a
verdict stored under what the judge saw can never be read as a verdict of the newer
input — a later request for the new input simply misses and re-scores. The check
therefore exists to tell the operator what they paid for, not to prevent a
misattribution the identity already prevents.

#### Scenario: A change the judge could not see is not a change

- **WHEN** a spawn's source is re-ingested in a way that leaves its judge input
  byte-identical
- **THEN** the verdict is not reported as behind, because nothing the judge was shown
  moved

Rationale: the judge input is a bounded projection, so a source edit inside an elided
region, or to an input that does not feed the projection at all, leaves the verdict
exactly as valid as it was. Rechecking a broader fingerprint would report those
verdicts as stale and re-spend on them for no gain.

### Requirement: A spawn claimed by another scorer is left alone

Scoring an identity SHALL be coordinated so that concurrent scorers do not both pay
for it, and a request that loses that coordination SHALL leave the spawn unscored and
report it as such rather than failing.

#### Scenario: Concurrent requests for one identity buy one verdict

- **WHEN** two runs request scoring for the same identity at the same time
- **THEN** at most one judge call is made across both runs

#### Scenario: The skipped run says so

- **WHEN** a run leaves a spawn unscored because another scorer holds it
- **THEN** the outcome distinguishes that spawn from one that was never requested and
  from one whose scoring failed

## MODIFIED Requirements

### Requirement: Scoring is requested explicitly, one spawn at a time

Scoring SHALL happen only when a caller asks for it, and a single request SHALL
produce at most one judge call and at most one verdict.

#### Scenario: Scoring is not requested

- **WHEN** a spawn is analyzed without scoring being requested
- **THEN** no judge call is made, no verdict is produced, and the run's observable
  behavior is unchanged from a run made before scoring existed

Rationale: the deterministic path is free and the scored path is not. A default that
spends money on a command users already run for facts is a trap.

#### Scenario: Scoring is requested for one spawn

- **WHEN** scoring is requested for a spawn that has been ingested successfully and no
  verdict exists for its identity
- **THEN** exactly one judge call is made and exactly one verdict is recorded for it

#### Scenario: Scoring is requested twice for the same unchanged spawn

- **WHEN** the same spawn is scored again with an unchanged judge input, rubric
  version, and resolved model
- **THEN** the second run makes no judge call, spends nothing, and reports the verdict
  the first run recorded rather than replacing it

Rationale: this reverses the interim behavior scoring shipped with, where a second run
paid again. The stored shape is unchanged — the second run still produces no second
row — so what moved is cost, which is what the interim scenario was written to make
easy to change.
