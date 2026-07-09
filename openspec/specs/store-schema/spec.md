# Store Schema

## Purpose

Defines the SQLite store schema — tables, columns, and grain — created from DDL on first run, with store location resolution and a documented verdict-JSON shape contract for downstream phases.

## Requirements

### Requirement: SQLite store creation from DDL

The system SHALL create a SQLite store from DDL on first run, containing all dimensional tables: `fact_tool_event`, `fact_session`, `dim_agent`, `dim_date`, `dim_tool`, `bridge_session_skill`, and `fact_verdict`. Tables not populated by this change SHALL be created empty for schema stability.

#### Scenario: Store is created on first run

- **WHEN** the pipeline runs and no store file exists
- **THEN** the store file is created and every required table exists in it

#### Scenario: All tables defined even when unpopulated

- **WHEN** the store is created
- **THEN** `fact_session`, `dim_date`, `dim_tool`, `bridge_session_skill`, and `fact_verdict` exist as empty tables alongside the populated `fact_tool_event` and `dim_agent`

### Requirement: fact_tool_event grain

The `fact_tool_event` table SHALL store one row per `tool_use`/`tool_result` pair, with columns for `session_id`, `seq` (order within session), `tool_name`, `is_error`, `denial_kind`, `ts`, `input_hash`, and `output_bytes`.

#### Scenario: Table exposes the finest grain columns

- **WHEN** the store schema is inspected
- **THEN** `fact_tool_event` includes `session_id`, `seq`, `tool_name`, `is_error`, `denial_kind`, `ts`, `input_hash`, and `output_bytes`

### Requirement: dim_agent definition dimension

The `dim_agent` table SHALL be keyed on `agent_type` and store `name`, `model`, `effort`, `declared_tools`, `declared_skills`, and `definition_hash`, resolving flat (`<name>.md`) and nested (`<name>/<name>.md`) agent definitions at both project and user level.

#### Scenario: dim_agent captures definition identity

- **WHEN** an agent definition is ingested
- **THEN** `dim_agent` records its `agent_type`, `model`, `declared_tools`, `declared_skills`, and a `definition_hash` that changes when the definition changes

### Requirement: Store location resolution

The system SHALL resolve the store location to a configurable path, defaulting to `~/.cache/agentlens/`, and SHALL NOT write inside any `.claude/` directory.

#### Scenario: Default store location

- **WHEN** no store path is configured
- **THEN** the store is created under `~/.cache/agentlens/`

#### Scenario: Read-only against .claude

- **WHEN** the pipeline runs for any input
- **THEN** no file inside a `.claude/` directory is created or modified

### Requirement: Verdict-JSON shape contract

The system SHALL define a documented verdict-JSON shape stub — per-dimension scores, overall, evidence quotes, suggested fixes, and judge run-cost fields — as a fixed contract for downstream phases, without populating it in this change.

#### Scenario: Verdict shape is documented

- **WHEN** a developer inspects the verdict contract
- **THEN** the shape defines per-dimension scores, an overall score, evidence, suggested fixes, and judge-cost fields (`judge_cost_usd`, `judge_input_tokens`, `judge_output_tokens`)
