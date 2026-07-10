## ADDED Requirements

### Requirement: Derive fact_session per spawn

The system SHALL derive one `fact_session` row per spawn, keyed by the per-spawn `session_id` (the `agent_id` for subagents), combining event-derived tool counts with transcript-read usage, turn, and duration fields. Four spawns of the same `agent_type` in one parent session SHALL produce four rows.

#### Scenario: One row per spawn, not per agent type

- **WHEN** a parent session fans out four spawns of the same `agent_type`
- **THEN** the store contains four distinct `fact_session` rows, each keyed by its own per-spawn `session_id`

#### Scenario: Tool counts derived from fact_tool_event

- **WHEN** a session's `fact_session` row is derived
- **THEN** `n_tool_calls`, `n_reads`, `n_edits`, `n_writes`, `n_bash`, `n_files_touched`, `n_errors`, and `n_permission_denials` are computed by aggregating that session's `fact_tool_event` rows

#### Scenario: Identity and lineage persisted

- **WHEN** a subagent session is aggregated
- **THEN** its `fact_session` row records `agent_id`, `agent_type`, `name_source`, `session_kind`, `parent_session_id`, and `spawn_tool_use_id` as resolved by the parser

### Requirement: Read usage, turns, and duration from the transcript

The system SHALL populate `fact_session` usage fields (`input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_creation_tokens`), `n_turns`, and `duration_sec` by reading the transcript directly — summing `message.usage` across assistant records — rather than aggregating `fact_tool_event`, because usage is a turn-level fact absent from tool events. Missing usage fields SHALL default to zero and SHALL NOT abort ingest.

#### Scenario: Token usage summed across turns

- **WHEN** a transcript has multiple assistant turns each carrying `message.usage`
- **THEN** `fact_session` records the sum of `input_tokens`, `output_tokens`, `cache_read_tokens`, and `cache_creation_tokens` across all turns

#### Scenario: Turn count and duration recorded

- **WHEN** a session is aggregated
- **THEN** `n_turns` equals the count of assistant records and `duration_sec` equals the span between the first and last record timestamps

#### Scenario: Missing usage is tolerated

- **WHEN** an assistant record has no `usage` object or omits a usage field
- **THEN** that contribution is treated as zero and the session is still aggregated without error

### Requirement: Duplicate tool-call count

The system SHALL compute `n_duplicate_tool_calls` as the session-wide count of `(tool_name, input_hash)` occurrences beyond the first for each distinct pair. It SHALL be recorded as a raw count with no interpretation.

#### Scenario: Session-wide duplicates counted

- **WHEN** a session issues the same tool with the same input hash three times
- **THEN** `n_duplicate_tool_calls` for that pair contributes two to the session total

#### Scenario: Distinct calls contribute nothing

- **WHEN** every tool call in a session has a distinct `(tool_name, input_hash)`
- **THEN** `n_duplicate_tool_calls` is zero

### Requirement: Final-report partial marker

The system SHALL record a raw boolean `final_report_flagged_partial`, true only when the final assistant text block matches a small fixed marker set indicating incomplete work. It SHALL NOT be treated as an authoritative completion verdict.

#### Scenario: Partial marker detected

- **WHEN** the final assistant text contains a configured partial marker (e.g. an unchecked checkbox or a "blocked"/"couldn't" phrase)
- **THEN** `final_report_flagged_partial` is true

#### Scenario: Clean completion not flagged

- **WHEN** the final assistant text contains no partial marker
- **THEN** `final_report_flagged_partial` is false regardless of `stop_reason`

### Requirement: Populate conformed dimensions

The system SHALL backfill `dim_date` and `dim_tool` from ingested sessions so windows and tool slices can join against them.

#### Scenario: dim_date backfilled from session timestamps

- **WHEN** sessions are ingested spanning several dates
- **THEN** `dim_date` contains a row per observed date with its `year`, `month`, `day`, and `iso_week`

#### Scenario: dim_tool backfilled from observed tools

- **WHEN** sessions reference a set of tool names
- **THEN** `dim_tool` contains a row per distinct `tool_name` seen
