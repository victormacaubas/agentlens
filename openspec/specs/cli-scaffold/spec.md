# CLI Scaffold

## Purpose

Provides the `agentlens` command-line entry point, installable via `uvx` or `pipx`, with `session` and `report` subcommands that wire into the parse pipeline.

## Requirements

### Requirement: Command-line entry point

The system SHALL provide an `agentlens` command-line entry point, installable and runnable via `uvx agentlens` and `pipx install agentlens`, exposing `session` and `report` subcommands.

#### Scenario: Invoking the top-level command

- **WHEN** a user runs `agentlens` with no arguments or `--help`
- **THEN** the CLI prints usage listing the `session` and `report` subcommands and exits with status 0

#### Scenario: Running via uvx

- **WHEN** a user runs `uvx agentlens --help`
- **THEN** the package resolves and the CLI executes without requiring a prior install

### Requirement: Session subcommand skeleton

The system SHALL provide an `agentlens session` subcommand that accepts a session id or `--file <path>` and runs the parse pipeline end-to-end against the store.

#### Scenario: Session subcommand is wired

- **WHEN** a user runs `agentlens session --file <path-to-agent.jsonl>`
- **THEN** the command executes the parse pipeline, ensures the store exists, and exits with status 0

#### Scenario: Missing target is reported

- **WHEN** a user runs `agentlens session --file <nonexistent-path>`
- **THEN** the command reports the missing input and exits with a non-zero status without writing partial data

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

### Requirement: Report subcommand skeleton

The system SHALL provide an `agentlens report` subcommand that accepts window flags (`--agent`, `--since`, `--from`, `--to`) and produces real deterministic aggregation over the store — windows, prior-window deltas, low-volume guard, spawns-not-sessions counting, and the intra-session parent lens — emitting the deterministic slice of the verdict JSON. It SHALL read exclusively from the store and SHALL NOT ingest.

#### Scenario: Report produces deterministic numbers

- **WHEN** a user runs `agentlens report --since 7d` against a populated store
- **THEN** the command emits window rollups and per-session counts (no LLM scores) and exits with status 0

#### Scenario: Report does not ingest on read

- **WHEN** `agentlens report` runs while uningested sessions exist on disk
- **THEN** the report reflects only the store's contents and ingests nothing

### Requirement: End-to-end empty pipeline

The system SHALL run the full parse pipeline end-to-end against an empty or absent input and produce a valid store without error.

#### Scenario: Empty pipeline writes a valid store

- **WHEN** the pipeline runs with no sessions to ingest
- **THEN** the store file is created with all tables present and the command exits with status 0
