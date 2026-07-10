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

### Requirement: fact_session deterministic columns

The `fact_session` table SHALL expose the deterministic session grain populated by aggregation: identity and lineage (`session_id`, `agent_id`, `agent_type`, `name_source`, `session_kind`, `spawn_depth`, `parent_session_id`, `spawn_tool_use_id`, `task_description`), event-derived counts (`n_turns`, `n_tool_calls`, `n_reads`, `n_edits`, `n_writes`, `n_bash`, `n_files_touched`, `n_errors`, `n_permission_denials`, `n_duplicate_tool_calls`), transcript-read usage (`input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_creation_tokens`, `duration_sec`), and context (`task_prompt_len`, `n_skills_fired`, `final_report_flagged_partial`).

The column formerly named `n_retry_loops` SHALL be named `n_duplicate_tool_calls`, and the complete/partial `claimed_status` column SHALL be replaced by the raw boolean `final_report_flagged_partial`. Because the store is a disposable cache under `~/.cache/agentlens/`, these schema changes SHALL be applied by recreating the DDL, with no data-migration path.

#### Scenario: Renamed and demoted columns present

- **WHEN** the store schema is inspected
- **THEN** `fact_session` includes `n_duplicate_tool_calls` and a boolean `final_report_flagged_partial`, and includes neither `n_retry_loops` nor a complete/partial `claimed_status`

#### Scenario: Deterministic columns carry no verdict

- **WHEN** the `fact_session` columns are inspected
- **THEN** every column holds a count, identifier, timestamp-derived value, or raw boolean — none encodes a scored judgment

### Requirement: Conformed dimensions are populated

The `dim_date` and `dim_tool` tables SHALL be populated from ingested sessions — previously created empty — so that windowed reporting can join against them.

#### Scenario: Dimensions no longer empty after ingest

- **WHEN** sessions have been ingested into the store
- **THEN** `dim_date` and `dim_tool` contain rows derived from those sessions rather than remaining empty
