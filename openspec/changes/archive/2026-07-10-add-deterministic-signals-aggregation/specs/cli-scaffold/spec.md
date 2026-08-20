## ADDED Requirements

### Requirement: Ingest subcommand

The system SHALL provide an `agentlens ingest` subcommand that walks `projects/**`, resolves every main session and subagent run via discovery, parses each, and upserts the full session grain into the store. It SHALL be idempotent and SHALL accept `--claude-home` and an optional `--limit N`.

#### Scenario: Bulk ingest populates the store

- **WHEN** a user runs `agentlens ingest --claude-home <path>` against a tree with several sessions
- **THEN** every discovered session is parsed and upserted, and the command exits with status 0

#### Scenario: Ingest is idempotent

- **WHEN** `agentlens ingest` is run twice against the same tree with no new sessions
- **THEN** the second run adds no rows and leaves existing rows unchanged

#### Scenario: Limit bounds the first run

- **WHEN** a user runs `agentlens ingest --limit 10`
- **THEN** at most 10 sessions are ingested in that invocation

## MODIFIED Requirements

### Requirement: Report subcommand skeleton

The system SHALL provide an `agentlens report` subcommand that accepts window flags (`--agent`, `--since`, `--from`, `--to`) and produces real deterministic aggregation over the store — windows, prior-window deltas, low-volume guard, spawns-not-sessions counting, and the intra-session parent lens — emitting the deterministic slice of the verdict JSON. It SHALL read exclusively from the store and SHALL NOT ingest.

#### Scenario: Report produces deterministic numbers

- **WHEN** a user runs `agentlens report --since 7d` against a populated store
- **THEN** the command emits window rollups and per-session counts (no LLM scores) and exits with status 0

#### Scenario: Report does not ingest on read

- **WHEN** `agentlens report` runs while uningested sessions exist on disk
- **THEN** the report reflects only the store's contents and ingests nothing
