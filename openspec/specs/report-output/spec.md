# Report Output Specification

## Purpose

Defines the versioned deterministic document and artifact that represent a
subagent report window without scores or other modeled output.

## Requirements

### Requirement: Window report is self-describing

The JSON report SHALL include its schema version, timezone-aware UTC generation
timestamp, resolved current and prior bounds, original window selector, applied
agent filter, and trend threshold.

#### Scenario: Report document is read independently
- **WHEN** a consumer reads a saved report document
- **THEN** it can identify the document version and reproduce the report scope
  without access to command-line arguments

### Requirement: Every qualifying subagent spawn has a typed row

The report SHALL contain one deterministic row for every qualifying current-
window subagent spawn, including spawns with no effective definition or no
skill evidence.

#### Scenario: Spawn has incomplete optional context
- **WHEN** a qualifying subagent has no effective definition and no
  session-skill rows
- **THEN** its spawn row remains present with required measured fields and
  explicit empty optional context

### Requirement: Spawn rows expose deterministic facts

Each spawn row SHALL include qualified and raw identity, agent and definition
identity, qualified parent metadata, task and start time, volume and health
facts, token usage, parse health, and resolved skill states.

#### Scenario: Spawn row is inspected
- **WHEN** a consumer reads a current-window spawn row
- **THEN** it can distinguish that spawn from same-type spawns and inspect all
  deterministic facts used by agent rollups

### Requirement: Agent rollups carry population and trend status

The report SHALL include one rollup per covered agent type with current and
prior deterministic values, spawn populations, signed deltas where permitted,
and an explicit trend status.

#### Scenario: Agent meets trend threshold
- **WHEN** an agent has enough current and prior spawns
- **THEN** its rollup contains current values, prior values, signed deltas, and
  a comparable trend status

#### Scenario: Agent lacks enough observations
- **WHEN** either comparison window falls below the trend threshold
- **THEN** the rollup retains raw values and populations, marks the trend
  `insufficient_data`, and contains no directional trend indicator

### Requirement: Output is deterministic-only

The Phase 2 report SHALL omit score, verdict, evidence, suggested-fix, judge
model, rubric, and judge-cost fields.

#### Scenario: No judge has run
- **WHEN** a deterministic report covers unscored subagent spawns
- **THEN** the document is complete for its Phase 2 schema without defaulting
  or fabricating any modeled field

### Requirement: Report artifact has a stable scope-derived path

The default report artifact SHALL use a stable filename derived from the
resolved selector and agent scope and SHALL overwrite the previous artifact for
that scope.

#### Scenario: All-agent seven-day report repeats
- **WHEN** the all-agent `7d` report runs more than once
- **THEN** one current JSON artifact exists for that scope and contains the
  latest deterministic report
