# Windowed Reporting

## Purpose

Produces windowed rollups over the store — resolving date windows from CLI flags, computing prior-window deltas, applying a low-volume guard, counting spawns (not parent sessions), providing an intra-session parent lens, and emitting the deterministic slice of the verdict JSON. Reads the store only; never ingests.

## Requirements

### Requirement: Window resolution

The system SHALL resolve `report` window flags — `--since 7d|30d|<date>`, `--from/--to`, and `--agent <type>` — to a `[start, end)` date range and filter `fact_session` rows to that range, optionally narrowed to one `agent_type`.

#### Scenario: Relative since window

- **WHEN** a user runs `report --since 7d`
- **THEN** only sessions within the last 7 days are aggregated

#### Scenario: Agent filter narrows results

- **WHEN** a user runs `report --agent implementer --since 30d`
- **THEN** only `implementer` spawns within the window are aggregated

### Requirement: Default window and current-day shortcut

The system SHALL default to a 7-day window when `report` is run with no window flag, so the prior-window delta and low-volume guard behave meaningfully out of the box. It SHALL also provide a current-day shortcut equivalent to `--since 1d`.

#### Scenario: No flag defaults to 7 days

- **WHEN** a user runs `agentlens report` with no window flag
- **THEN** the last 7 days are aggregated and the report labels the resolved window

#### Scenario: Current-day shortcut

- **WHEN** a user runs `agentlens report --today` (equivalently `--since 1d`)
- **THEN** only the current day's spawns are aggregated

### Requirement: Prior-window delta

The system SHALL compute a prior-window delta by comparing each aggregate against the immediately preceding equal-length span.

#### Scenario: Delta against previous equal span

- **WHEN** a 7-day window is reported and the preceding 7 days contain sessions
- **THEN** each aggregate shows its change relative to that preceding 7-day span

### Requirement: Low-volume guard

The system SHALL suppress trend arrows when the window holds fewer than `min_sessions_for_trend` spawns (default 5), showing raw values and the count labeled "insufficient data" instead.

#### Scenario: Trend suppressed below threshold

- **WHEN** a window contains 3 spawns and the threshold is 5
- **THEN** the report shows raw values and the count but no trend arrows, labeled "insufficient data"

### Requirement: Count spawns, not parent sessions

The system SHALL count and label every aggregate N in spawns, not parent sessions, and SHALL use `task_description` to distinguish same-type spawns in detail views.

#### Scenario: N is spawns

- **WHEN** three parent sessions each fan out four `implementer` spawns in the window
- **THEN** the report labels `implementer` as 12 spawns, not 3 sessions

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
