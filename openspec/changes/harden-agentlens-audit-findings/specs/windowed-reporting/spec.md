## MODIFIED Requirements

### Requirement: Emit deterministic verdict-JSON slice

The system SHALL emit one deterministic row per qualified spawn in the selected window,
including scored and unscored spawns, together with agent and parent rollups derived from
those rows. A report that includes modeled scores SHALL select and name one explicit
comparable verdict cohort: rubric version, concrete judge model, and current judge-input
identity policy. It SHALL attach at most one verdict from that cohort to each spawn and
SHALL compute score aggregates only from those attached verdicts.

The payload SHALL retain `n_spawns_with_errors` in agent aggregates, parent rows, and delta
keys. It SHALL expose no `n_failures` alias. Running a report SHALL read the store only.

#### Scenario: Deterministic numbers without scores

- **WHEN** a report runs against a populated store with no selected-cohort verdicts
- **THEN** every spawn appears with deterministic data and no modeled score

#### Scenario: Verdicts included when present

- **WHEN** a spawn has one verdict in the selected cohort
- **THEN** its session row includes that verdict once and the agent average counts the spawn once

#### Scenario: Incomparable verdicts are excluded

- **WHEN** a spawn also has verdicts from another rubric or concrete model
- **THEN** those verdicts do not affect the selected payload or score aggregates

#### Scenario: Mixed scored and unscored

- **WHEN** a window contains 10 spawns, 6 scored in the cohort and 4 unscored
- **THEN** all 10 session rows appear and only the 6 scored rows carry verdict data

#### Scenario: Aggregate reconciles to session rows

- **WHEN** multiple same-type spawns appear in the window
- **THEN** agent and parent counts and selected-cohort averages reconcile to the emitted session rows

#### Scenario: JSON names the cohort

- **WHEN** JSON output includes verdict data
- **THEN** the payload names the rubric version and concrete judge model used for comparison

#### Scenario: JSON output includes verdicts

- **WHEN** JSON output is requested for a window containing selected-cohort verdicts
- **THEN** each scored session row includes its dimension scores and suggested fixes

#### Scenario: Report does not ingest

- **WHEN** new uningested transcripts exist on disk
- **THEN** the report reflects only stored rows and writes nothing

#### Scenario: Spawns-with-errors key is named for what it counts

- **WHEN** JSON output is emitted
- **THEN** aggregates and deltas use `n_spawns_with_errors` and no `n_failures` key

## ADDED Requirements

### Requirement: Comparable verdict cohort is explicit

The report command or report-building contract SHALL require or deterministically resolve
one rubric version and concrete judge model when modeled scores are requested. It SHALL
not choose a verdict by insertion order or average scores across cohorts.

#### Scenario: Multiple verdict cohorts exist

- **WHEN** the store contains multiple rubric/model cohorts for the requested window
- **THEN** the report selects one named cohort or asks the caller to disambiguate

#### Scenario: Reversed insertion order

- **WHEN** identical cohorts are inserted in different orders in two stores
- **THEN** reports for the same selected cohort produce identical payloads and aggregates
