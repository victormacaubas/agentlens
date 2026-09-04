## Purpose

Defines how a windowed report presents modeled scores beside its deterministic
signals: which verdict cohort a report speaks for, how that cohort is chosen or
refused, how a verdict is matched to a spawn, and how untrusted judge prose is
marked when it reaches a reader.

## ADDED Requirements

### Requirement: A report speaks for exactly one verdict cohort

A cohort SHALL be identified by one rubric version together with one concrete
judge model, where the concrete judge model is the identifier resolved from the
judge's response envelope and never an alias typed at the command line. A report
that presents any modeled value SHALL name the single cohort that value came
from, and SHALL NOT combine values from two cohorts into one figure.

#### Scenario: Report presents a modeled average
- **WHEN** a report presents a modeled score or a modeled rollup
- **THEN** the document names the rubric version and the concrete judge model
  that every presented modeled value was produced under

#### Scenario: Window holds verdicts from two rubric versions for one spawn
- **WHEN** a spawn holds verdicts under two rubric versions at the same
  judge-input hash, because rubric identity is independent of judge-input
  identity
- **THEN** at most one of them contributes to the report, chosen by the named
  cohort rather than by storage order

### Requirement: A sole cohort is selected without a selector

When every verdict reachable in the report's scope belongs to one cohort, the
report SHALL select that cohort automatically and SHALL record in the document
that selection was implied rather than requested.

#### Scenario: One cohort is present and no selector is supplied
- **WHEN** the window's verdicts all share one rubric version and one concrete
  judge model, and no cohort selector is supplied
- **THEN** the report succeeds, names that cohort, and records that it was
  selected because it was the only one available

#### Scenario: One cohort is present and it is named explicitly
- **WHEN** the caller names the only cohort present
- **THEN** the report succeeds, names that cohort, and records that it was
  selected because it was requested

### Requirement: An ambiguous cohort is a configuration failure

When the report's scope holds verdicts from more than one cohort and no cohort
was named, the report SHALL fail with the configuration-error exit code and
SHALL name every cohort present together with the number of spawns each one
covers. It SHALL NOT choose among them, whether by storage order, by recency, or
by coverage, and SHALL write no report artifact.

#### Scenario: Two cohorts are present and no selector is supplied
- **WHEN** the window holds verdicts under two concrete judge models and no
  cohort selector is supplied
- **THEN** the command fails with the configuration-error exit code, names both
  cohorts and their spawn coverage, and writes no report artifact

#### Scenario: One cohort covers far more spawns than the other
- **WHEN** one cohort covers most of the window and another covers a single
  spawn, and no cohort selector is supplied
- **THEN** the command still fails rather than selecting the better-covered
  cohort, because an implied choice can change between runs as scoring
  progresses

### Requirement: A named cohort absent from the scope is a configuration failure

When the caller names a cohort that no verdict in the report's scope belongs to,
the report SHALL fail with the configuration-error exit code and SHALL name the
cohorts that are present.

#### Scenario: Caller names a cohort with no verdicts in the window
- **WHEN** the caller names a rubric version and judge model that match no
  verdict in scope, including an alias rather than a concrete judge model
- **THEN** the command fails with the configuration-error exit code and names
  the cohorts present, rather than succeeding with a report in which every spawn
  is unscored

### Requirement: A scope with no verdicts is not an error

When no verdict is reachable in the report's scope, the report SHALL succeed,
SHALL name no cohort, SHALL mark every spawn unscored, and SHALL omit modeled
rollups rather than presenting them with zeroed or defaulted values.

#### Scenario: No judge has ever run
- **WHEN** a report covers a window whose spawns hold no verdicts at all
- **THEN** the report succeeds, names no cohort, marks every spawn unscored, and
  contains no modeled rollup

#### Scenario: Agent filter excludes every scored spawn
- **WHEN** an agent filter restricts the report to spawns that hold no verdicts,
  while verdicts exist elsewhere in the store
- **THEN** cohort availability is judged within the filtered scope, so the report
  succeeds and names no cohort

### Requirement: A verdict matches a spawn on the spawn's current judge-input hash

A verdict SHALL contribute a score to a spawn only when it belongs to the named
cohort and its judge-input hash equals the hash of that spawn's current source.
Because cohort and judge-input hash together complete a verdict's identity, at
most one verdict SHALL be matchable to a spawn, without any tie being broken
after the fact.

#### Scenario: Spawn's source is unchanged since it was scored
- **WHEN** a spawn's current source hashes to the same judge input the stored
  verdict was produced from
- **THEN** that verdict's scores are presented for the spawn and contribute to
  the modeled rollup

#### Scenario: Spawn's source grew after it was scored
- **WHEN** a spawn's current source hashes to a different judge input than the
  stored verdict in the named cohort
- **THEN** that verdict contributes no score to any average

### Requirement: Every spawn carries an explicit modeled state

Each spawn row SHALL carry exactly one modeled state: scored when a verdict in
the named cohort matches its current judge-input hash, stale when a verdict in
the named cohort exists only under a superseded judge-input hash, and unscored
when the cohort holds no verdict for it. A spawn SHALL remain present in the
report under every one of those states, and SHALL never be given a defaulted or
fabricated score.

#### Scenario: Window mixes scored, stale, and unscored spawns
- **WHEN** a window contains spawns in all three states
- **THEN** every spawn is present with its own state, and the counts of each
  state are available to a reader

#### Scenario: Stale is distinguished from unscored
- **WHEN** a spawn's only verdict in the cohort is behind its current source
- **THEN** it reports as stale rather than as unscored, because re-scoring it and
  scoring it for the first time are different actions

#### Scenario: Unscored spawn is inspected
- **WHEN** a reader inspects an unscored spawn row
- **THEN** its deterministic facts are present and its modeled fields are
  explicitly absent rather than zero

### Requirement: Modeled rollups count only the spawns they scored

A modeled agent rollup SHALL report its own population — the spawns whose scores
it averaged — separately from the deterministic spawn population for that agent
type. A modeled average SHALL be computed over scored spawns only.

#### Scenario: Most of a window is unscored
- **WHEN** an agent type has many qualifying spawns of which few are scored
- **THEN** the modeled rollup reports the scored count as its population and the
  deterministic rollup continues to report every qualifying spawn

### Requirement: The low-volume trend guard applies to modeled scores

A modeled trend SHALL be suppressed under the same minimum-observation threshold
that suppresses a deterministic trend, evaluated against the modeled population
in each comparison window. A suppressed modeled trend SHALL retain its raw
values and populations, SHALL be marked as having insufficient data, and SHALL
carry no directional indicator.

#### Scenario: Modeled and deterministic populations disagree about sufficiency
- **WHEN** an agent type has enough qualifying spawns in both windows to compare
  deterministic values, but too few scored spawns to compare modeled ones
- **THEN** the deterministic trend is comparable while the modeled trend is
  marked insufficient, and both populations are visible so a reader can see why

#### Scenario: Modeled populations meet the threshold in both windows
- **WHEN** an agent type has enough scored spawns in the current and prior
  windows
- **THEN** its modeled rollup carries current values, prior values, signed
  deltas, and a comparable trend status

### Requirement: Judge prose is marked untrusted and never made directly applicable

Evidence, recommendations, rationale, and fix targets SHALL be presented as
untrusted model output, identified as such alongside the locally derived fields
they sit next to. No surface SHALL emit them in a form shaped for direct
application, including a patch, a diff, or a runnable command. The terminal
summary SHALL NOT render any of that text.

#### Scenario: JSON document carries evidence and fixes
- **WHEN** a reader consumes a report document containing evidence and suggested
  fixes
- **THEN** those fields are identified as untrusted model output and the scores
  and dimensions beside them are identified as locally derived

#### Scenario: Terminal summary covers scored spawns
- **WHEN** the terminal summary describes a window containing scored spawns
- **THEN** it reports the cohort, the scores, and the per-state counts, and
  renders no evidence, recommendation, rationale, or fix-target text

### Requirement: Only agentlens's own judge spend is reported in currency

A report SHALL present the judge spend agentlens itself incurred for the named
cohort over the window as a currency figure, and SHALL NOT convert the token
usage of the runs being analyzed into a currency figure.

#### Scenario: Report covers scored spawns
- **WHEN** a report presents modeled scores for a window
- **THEN** agentlens's own judge cost for that cohort appears as a currency
  figure while the analyzed spawns' token usage remains a count
