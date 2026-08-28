## ADDED Requirements

### Requirement: A reused verdict is reported as reused and as free

When a run reports a verdict it did not pay for, the surfaces SHALL state that the
verdict was reused, SHALL report this run's judge cost as zero, and SHALL name when
the verdict was originally produced.

#### Scenario: Reused row contents

- **WHEN** a row is read for a spawn whose verdict was reused
- **THEN** it carries the same verdict fields a freshly scored row carries, plus a
  marker that the verdict was reused and the instant the verdict was originally
  recorded

#### Scenario: This run's cost is zero, not absent

- **WHEN** a summary is printed for a run whose only verdict was reused
- **THEN** it reports this run's judge cost as zero in dollars and tokens, and it
  states that the verdict was reused rather than leaving the reader to infer it

Rationale: a surface that reports nothing at all reads as though scoring did not
happen, which is the failure this requirement exists to prevent. Naming the reuse is
what distinguishes free-because-reused from nothing-happened.

#### Scenario: The original cost is not reported as this run's

- **WHEN** a reused verdict carries the dollar cost of the judge call that originally
  produced it
- **THEN** that figure is not presented as what this run spent

Rationale: summing a reported cost across runs is the obvious thing a reader does with
it. Reporting a historical cost as current would make repeated free runs look
expensive.

### Requirement: A spawn skipped for a live claim is distinguishable

When a spawn is left unscored because another scorer holds its identity, the surfaces
SHALL report that outcome distinctly from a spawn that was never requested, a spawn
whose verdict was reused, and a spawn whose scoring failed.

#### Scenario: Claimed-elsewhere outcome is named

- **WHEN** a run leaves its spawn unscored because the identity was claimed elsewhere
- **THEN** the summary states that as the reason, and the document carries no verdict,
  score, or fix field for that spawn

#### Scenario: Four outcomes stay apart

- **WHEN** the outcomes for a scored spawn, a reused spawn, a claimed-elsewhere spawn,
  and a spawn whose scoring failed are compared
- **THEN** each is distinguishable from the other three without inspecting cost figures
  to infer which happened

Rationale: cost alone cannot tell these apart — a reused spawn and a claimed-elsewhere
spawn both spent zero, and only one of them has a verdict to show.

### Requirement: A verdict behind its current input says so

When a committed verdict was produced from a judge input the spawn no longer has, the
surfaces SHALL report it as behind the current input.

#### Scenario: Superseded verdict is marked, not hidden

- **WHEN** a run commits a verdict whose judge input changed while the call was in
  flight
- **THEN** the verdict is reported along with the fact that it is behind the spawn's
  current input, and the run's spend is reported

#### Scenario: The marking is not a stored flag

- **WHEN** the same verdict is read by a later run for which the spawn's judge input
  has changed again
- **THEN** whether it is behind is determined by comparing against the current input at
  read time, not by a value recorded on the verdict when it was written

Rationale: a stored flag would be wrong the moment the input moved again, and a
verdict that is behind today may be the current one again if an edit is reverted. The
identity already carries the input it was produced from, so the comparison is always
available.
