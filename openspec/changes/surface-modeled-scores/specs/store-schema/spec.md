## MODIFIED Requirements

### Requirement: Report windows are queryable without model output

The store SHALL return subagent spawn rows and deterministic agent rollups for
a current half-open start-time range and an equal-length prior range without
joining modeled verdict data. A read that returns verdict data SHALL be a
separate read, so that no deterministic figure the store computes can be derived
from a verdict.

#### Scenario: Current and prior ranges contain spawns
- **WHEN** a caller queries resolved current and prior bounds
- **THEN** the result includes each qualifying current-window spawn and the
  deterministic values needed to compare agent rollups across both ranges

#### Scenario: Main-session rows exist in a future-compatible store
- **WHEN** the store contains a row whose session kind is `main`
- **THEN** the report query excludes it from subagent spawn rows and aggregates

#### Scenario: Window holds scored spawns
- **WHEN** a caller queries window bounds over spawns that hold verdicts
- **THEN** the deterministic spawn rows and rollups returned are identical to
  those returned when the same spawns hold no verdicts

## ADDED Requirements

### Requirement: Verdicts are readable for many sessions in one read

The store SHALL return the stored verdicts for a set of qualified session
identifiers in a single read, so that presenting modeled scores over a window
does not require one read per spawn. The read SHALL return every verdict held
for those sessions, across rubric versions, judge models, and judge-input
hashes, leaving cohort selection and judge-input matching to the caller.

#### Scenario: Caller reads verdicts for a window's spawns
- **WHEN** a caller supplies the qualified session identifiers of a window's
  spawns
- **THEN** one read returns every verdict stored for those sessions, and a
  session with no verdicts contributes no rows rather than a placeholder

#### Scenario: A session holds verdicts in several cohorts
- **WHEN** a session holds verdicts under two rubric versions and two judge
  models
- **THEN** all of them are returned, each carrying the rubric version, concrete
  judge model, and judge-input hash needed to tell them apart

#### Scenario: No session identifiers are supplied
- **WHEN** a caller supplies an empty set of session identifiers
- **THEN** the read returns no rows rather than every verdict in the store
