## ADDED Requirements

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
