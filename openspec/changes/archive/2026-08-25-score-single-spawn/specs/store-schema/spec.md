## ADDED Requirements

### Requirement: Verdict grain and key

The `fact_verdict` table SHALL hold one row per scored identity, uniquely identified
by the session together with the judge-input hash, the rubric version, and the
concrete judge model identifier.

#### Scenario: One spawn scored under two models

- **WHEN** the same spawn is scored twice with the same input and rubric version but
  the envelope reports a different concrete model each time
- **THEN** two verdict rows exist, one per model, and neither has replaced the other

Rationale: verdicts from different concrete models are not comparable, so collapsing
them would silently average across incomparable things.

#### Scenario: Re-scoring the same identity replaces the row

- **WHEN** a spawn is scored again with an unchanged input hash, rubric version, and
  resolved model
- **THEN** the existing verdict row for that identity is replaced rather than
  duplicated

#### Scenario: Rubric version changes

- **WHEN** the same spawn and input are scored under a new rubric version
- **THEN** a separate verdict row exists for the new version and the earlier row
  remains

### Requirement: Measured and modeled data stay in separate tables

Modeled verdict data SHALL live only in `fact_verdict`, and no deterministic table
SHALL carry a score, verdict, or fix field.

#### Scenario: Deterministic rows are unchanged by scoring

- **WHEN** a spawn is scored
- **THEN** its session and tool-invocation rows are unchanged, and no score or
  verdict value has been written into either

Rationale: a deterministic fact must never be computable from model output. Keeping
the tables apart is what makes that structural rather than a matter of care.

### Requirement: A verdict records its own provenance and cost

Each verdict row SHALL record which of its fields are locally derived and which are
untrusted model output, together with the dollar cost and token counts of the judge
call that produced it.

#### Scenario: Verdict round-trips with provenance intact

- **WHEN** a verdict is written and read back
- **THEN** its scores, evidence, fixes, provenance markings, rubric version, judge
  model, dollar cost, and token counts all match what was written

### Requirement: Verdicts are regenerable but not free

Verdict rows SHALL be reproducible by re-scoring the same source under the same
rubric version and model, and deleting the store SHALL remain safe in the sense that
nothing unrecoverable is lost.

#### Scenario: Store is deleted after scoring

- **WHEN** the store is deleted and the same transcript is ingested and scored again
- **THEN** the deterministic rows are equivalent to those the deleted store held, and
  a verdict is produced again at the cost of another judge call

Rationale: the store stays a rebuildable cache, but rebuilding modeled rows spends
money where rebuilding measured rows does not. That asymmetry is why verdict reuse
becomes worth building next, and it is not a reason to make the store authoritative.
