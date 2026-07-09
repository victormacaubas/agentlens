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

### Requirement: Report subcommand skeleton

The system SHALL provide an `agentlens report` subcommand as a stub that accepts window flags (e.g. `--agent`, `--since`) and exits cleanly, with full aggregation deferred to a later phase.

#### Scenario: Report subcommand is wired

- **WHEN** a user runs `agentlens report --since 7d`
- **THEN** the command executes without error and exits with status 0 (aggregation output is a later phase)

### Requirement: End-to-end empty pipeline

The system SHALL run the full parse pipeline end-to-end against an empty or absent input and produce a valid store without error.

#### Scenario: Empty pipeline writes a valid store

- **WHEN** the pipeline runs with no sessions to ingest
- **THEN** the store file is created with all tables present and the command exits with status 0
