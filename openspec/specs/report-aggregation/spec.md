# Report Aggregation Specification

## Purpose

Turns stored subagent spawn facts into reproducible current-window rollups,
equal-length prior comparisons, and guarded trend signals.

## Requirements

### Requirement: Window membership uses spawn start time

A subagent spawn SHALL belong to a report window according to its earliest
usable transcript timestamp, stored as its start time.

#### Scenario: Spawn ends after the window
- **WHEN** a spawn starts inside the selected window and ends after the window's
  upper bound
- **THEN** it belongs to the selected window

#### Scenario: Spawn starts before the window
- **WHEN** a spawn starts before the selected window and continues into it
- **THEN** it does not belong to the selected window

### Requirement: Resolved ranges are half-open

Every report range SHALL include its lower bound and exclude its upper bound so
adjacent windows cannot count one spawn twice.

#### Scenario: Spawn starts on a boundary
- **WHEN** one spawn starts exactly at the current window's lower bound and
  another starts exactly at its upper bound
- **THEN** the first belongs to the current window and the second does not

### Requirement: Calendar windows use the machine local timezone

Named calendar selectors SHALL resolve their calendar boundaries in the machine
local timezone and SHALL retain unambiguous instants for querying and output.

#### Scenario: This week is requested
- **WHEN** the caller selects `this-week`
- **THEN** the lower bound is the start of the current local-calendar week and
  the upper bound is the resolved current instant

### Requirement: Prior window has equal duration

The prior comparison window SHALL end where the current window begins and SHALL
have the same elapsed duration as the current window.

#### Scenario: Seven-day current window
- **WHEN** the current range is `[now-7d, now)`
- **THEN** the prior range is `[now-14d, now-7d)`

### Requirement: Aggregates group subagent spawns by agent type

The system SHALL produce one rollup per agent type in the current window and
SHALL label its population as a count of spawns.

#### Scenario: One parent produced four same-type subagents
- **WHEN** four distinct spawns of one agent type fall in the current window
- **THEN** that agent rollup reports `4 spawns` rather than one session or one
  agent

### Requirement: Rollups expose deterministic volume and health

Each agent rollup SHALL report its spawn count and deterministic aggregates for
turns, invocations, tool-category activity, distinct files, errors, denials,
repeated invocations, duration, token usage, cache-read proportion, parse
health, and fired-skill counts. Additive metrics SHALL include window totals and
per-spawn averages. Cache-read proportion SHALL be computed from the summed
input-side token fields rather than by averaging spawn percentages.

#### Scenario: Agent has several spawns
- **WHEN** an agent type has multiple spawns in the current window
- **THEN** its rollup exposes the population count, totals, per-spawn averages,
  and weighted cache-read proportion derived from those spawn rows

### Requirement: Comparable metrics carry prior-window deltas

For each per-spawn average and weighted proportion with enough observations in
both windows, the system SHALL report the current value, prior value, and signed
change. Window totals remain visible but SHALL NOT drive directional trends.

#### Scenario: Both windows have sufficient observations
- **WHEN** an agent type meets the trend threshold in the current and prior
  windows
- **THEN** each comparable metric includes its current value, prior value, and
  signed delta

#### Scenario: Agent exists only in the current window
- **WHEN** an agent type has no prior-window spawns
- **THEN** its current values and spawn count remain visible and no trend
  direction is claimed

### Requirement: Low-volume trends are suppressed

The system SHALL use a configurable `min_sessions_for_trend` threshold whose
default is 5 and SHALL suppress trend indicators when either comparison window
has fewer qualifying spawns than the threshold.

#### Scenario: Current window is below threshold
- **WHEN** an agent has four current-window spawns and the threshold is five
- **THEN** the report shows raw current values and `4 spawns`, labels the trend
  as `insufficient_data`, and emits no directional indicator

#### Scenario: Prior window is below threshold
- **WHEN** the current window meets the threshold but the prior window does not
- **THEN** the report shows current and available prior values but labels the
  trend as `insufficient_data` and emits no directional indicator

### Requirement: Zero-result windows remain valid

An empty current window SHALL produce an empty deterministic result with its
resolved bounds rather than an error or fabricated aggregate.

#### Scenario: No subagent starts in the current window
- **WHEN** a valid report window contains zero qualifying subagent spawns
- **THEN** the report succeeds with zero spawn rows, zero agent rollups, and the
  resolved current and prior bounds
