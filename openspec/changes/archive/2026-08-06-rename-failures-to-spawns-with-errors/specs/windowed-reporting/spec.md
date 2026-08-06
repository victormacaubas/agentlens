## MODIFIED Requirements

### Requirement: Intra-session parent lens

The system SHALL provide a per-parent-session rollup grouping spawns by `parent_session_id`, summarizing fan-out and health (spawn count, spawns with errors, denials) for that parent.

The spawns-with-errors metric SHALL be named `n_spawns_with_errors` wherever it is exposed, in both the rendered output and the JSON payload, and SHALL NOT be called a failure count. It counts spawns in which at least one tool call errored or which self-reported partial completion. It is therefore a per-spawn indicator that something is worth investigating, not a count of runs that failed to complete, and it is distinct from `n_errors`, which totals error events rather than spawns.

#### Scenario: Parent fan-out summarized

- **WHEN** a parent session fanned out four subagents of which one hit a tool error and one hit denials
- **THEN** the parent lens reports 4 spawns, 1 with errors, and 1 denial for that `parent_session_id`

#### Scenario: A recoverable error is not reported as a failed run

- **WHEN** a spawn completes its task but logged one recoverable tool error along the way
- **THEN** it is counted in `n_spawns_with_errors` and is not described in the output as a failure

### Requirement: Emit deterministic verdict-JSON slice

The system SHALL emit the deterministic slice of the verdict JSON — per-session counts and window rollups — and SHALL include verdict scores (from `fact_verdict`) when they exist for sessions in the window. Verdict inclusion SHALL be opportunistic: sessions without verdicts still appear with deterministic data only.

The spawns-with-errors metric SHALL appear in the payload under the key `n_spawns_with_errors`, in the per-agent aggregates, in the parent-lens rows, and among the prior-window delta keys.

#### Scenario: Deterministic numbers without scores

- **WHEN** `report --since 7d` runs against a populated store with no verdicts
- **THEN** it emits per-session counts and window rollups with no LLM score fields

#### Scenario: Verdicts included when present

- **WHEN** `report --since 7d` runs and some sessions have verdicts in the store
- **THEN** those sessions include their verdict scores and suggested_fixes in the output

#### Scenario: Mixed scored and unscored

- **WHEN** a window contains 10 sessions, 6 scored and 4 unscored
- **THEN** all 10 appear in the report; the 6 scored ones include verdict data, the 4 unscored show deterministic data only

#### Scenario: JSON output includes verdicts

- **WHEN** `report --since 7d --json` runs with scored sessions
- **THEN** the JSON output includes a `verdicts` key per session containing dimension scores and suggested_fixes

#### Scenario: Report does not ingest

- **WHEN** `report` runs and new uningested sessions exist on disk
- **THEN** the report reflects only what is already in the store and does not ingest the new sessions

#### Scenario: Spawns-with-errors key is named for what it counts

- **WHEN** the JSON payload is emitted
- **THEN** the per-agent aggregates, the parent-lens rows, and the delta keys expose `n_spawns_with_errors`, and no key named `n_failures` appears
