## MODIFIED Requirements

### Requirement: Command-line entry point

The system SHALL provide an `agentlens` command-line entry point, installable and runnable via `uvx agentlens` and `pipx install agentlens`, exposing `session`, `ingest`, `report`, and `score` subcommands.

#### Scenario: Invoking the top-level command

- **WHEN** a user runs `agentlens` with no arguments or `--help`
- **THEN** the CLI prints usage listing the `session`, `ingest`, `report`, and `score` subcommands and exits with status 0
