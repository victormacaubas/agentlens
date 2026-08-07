## MODIFIED Requirements

### Requirement: Ingest subcommand

The system SHALL provide `agentlens ingest` to discover, parse, and atomically upsert
qualified session grains. It SHALL accept `--claude-home` and an optional positive
`--limit N`. It SHALL report ingested, degraded, discovery-failed, and skipped targets.
Any skipped or failed target SHALL make the command exit non-zero after preserving and
reporting successful targets.

#### Scenario: Bulk ingest populates the store

- **WHEN** every discovered target is ingested successfully
- **THEN** the command reports the count and exits 0

#### Scenario: Ingest is idempotent

- **WHEN** ingest runs twice against an unchanged tree
- **THEN** the second run leaves the same qualified grains in the store

#### Scenario: Limit bounds the first run

- **WHEN** ingest runs with `--limit 10`
- **THEN** it discovers and attempts at most 10 targets

#### Scenario: Invalid limit is rejected

- **WHEN** `--limit` is zero or negative
- **THEN** Click reports invalid usage before opening the store

#### Scenario: Partial ingest is a command failure

- **WHEN** one target fails and another succeeds
- **THEN** the successful grain remains committed, the failed path is reported, and the command exits non-zero

## ADDED Requirements

### Requirement: Expected domain failures are actionable

The CLI SHALL translate expected store-location, judge-availability, filesystem, and
database failures into concise Click errors with non-zero status. Default execution SHALL
not print an internal traceback, while the original exception remains available as the
cause for tests and debugging.

#### Scenario: Forbidden store location

- **WHEN** the selected store resolves inside `.claude`
- **THEN** the CLI prints an `Error:` message naming the prohibition and exits non-zero

#### Scenario: Judge unavailable

- **WHEN** scoring cannot launch or authenticate the configured judge
- **THEN** the CLI prints the actionable remedy and exits non-zero

### Requirement: One package version source

The displayed `agentlens --version` value SHALL come from installed package metadata.
Package initializers SHALL remain empty and SHALL NOT hold a second hardcoded version.

#### Scenario: CLI version matches distribution metadata

- **WHEN** a release updates the package version
- **THEN** `agentlens --version` reports the same value without another source edit
