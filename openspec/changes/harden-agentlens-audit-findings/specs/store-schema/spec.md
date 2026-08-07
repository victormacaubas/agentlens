## MODIFIED Requirements

### Requirement: fact_tool_event grain

The `fact_tool_event` table SHALL store one row per `tool_use`/`tool_result` pair,
keyed within a qualified session, with columns for `session_id`, `seq`, `tool_name`,
`is_error`, `denial_kind`, `ts`, whole-input `input_hash`, normalized-path
`file_path_hash` when the tool addresses a file, and `output_bytes`.

#### Scenario: Table exposes the finest grain columns

- **WHEN** the store schema is inspected
- **THEN** `fact_tool_event` includes both `input_hash` for duplicate-call identity and `file_path_hash` for file identity

### Requirement: dim_agent definition dimension

The agent-definition dimension SHALL preserve versioned definitions keyed by agent type,
scope, source-project identity when project-scoped, and `definition_hash`. It SHALL store
`name`, `model`, `effort`, `declared_tools`, and `declared_skills`. Each subagent session
SHALL reference the effective definition version selected at ingest, using project scope
before user scope for the same agent type.

#### Scenario: Definition versions remain attributable

- **WHEN** sessions run under definition hashes A and B
- **THEN** both definitions remain queryable and each session references the version active for that run

#### Scenario: dim_agent captures definition identity

- **WHEN** an agent definition is ingested
- **THEN** the dimension records its type, scope, project identity, model, tools, skills, and definition hash

#### Scenario: Project definition overrides user definition

- **WHEN** a project and the user scope define the same agent type
- **THEN** sessions from that project reference its definition while sessions from other projects use their own project definition or the user fallback

### Requirement: Store location resolution

The system SHALL resolve the store location to a configurable path, defaulting to
`~/.cache/agentlens/`. It SHALL validate the physical path, including resolved symlink
ancestors, and SHALL NOT create or modify a database or sidecar inside any `.claude`
directory. A newly created store and its SQLite sidecars SHALL be readable and writable
only by the owning user.

#### Scenario: Default store location

- **WHEN** no store path is configured
- **THEN** the store is created under `~/.cache/agentlens/`

#### Scenario: Symlink into .claude is rejected

- **WHEN** a configured lexical path resolves through a symlink into a `.claude` directory
- **THEN** store creation fails before writing any file there

#### Scenario: Read-only against .claude

- **WHEN** any store path is resolved or opened
- **THEN** no database or sidecar inside a physical `.claude` directory is created or modified

#### Scenario: Store permissions are private

- **WHEN** a store is created under a permissive process umask
- **THEN** the database and any SQLite sidecars grant no group or other access

### Requirement: fact_session deterministic columns

The `fact_session` table SHALL expose a qualified session grain with source identity
(`session_id`, `raw_session_id`, `source_project`, `session_kind`, `source_revision`,
`judge_input_hash`), definition identity (`agent_id`, `agent_type`,
`agent_definition_id`, `name_source`), lineage (`spawn_depth`,
`parent_session_id`, `spawn_tool_use_id`, `task_description`), event-derived counts,
transcript-read usage, duration, UTC `session_date`, and deterministic context fields.
Every column SHALL remain a count, identifier, timestamp-derived value, hash, or raw
boolean; none SHALL encode a verdict.

#### Scenario: Qualified identity columns are present

- **WHEN** the schema is inspected
- **THEN** raw source IDs, qualified session identity, source revision, judge-input hash, and effective definition identity are distinct columns

#### Scenario: Renamed and demoted columns present

- **WHEN** `fact_session` is inspected
- **THEN** it contains `n_duplicate_tool_calls` and `final_report_flagged_partial`, and contains neither `n_retry_loops` nor `claimed_status`

#### Scenario: Deterministic columns carry no verdict

- **WHEN** the `fact_session` columns are inspected
- **THEN** none encodes a scored judgment

## ADDED Requirements

### Requirement: Verdicts bind to exact judge input

Each verdict SHALL record the stable hash of the exact prepared judge input it scored.
Cache matching and verdict persistence SHALL require `session_id`, rubric version,
concrete judge model, and judge-input hash to match. Persisting a verdict SHALL fail if
the session's current judge-input hash changed after scoring began.

#### Scenario: Changed transcript invalidates cached verdict

- **WHEN** a session is re-ingested with changed judge input under the same raw identity
- **THEN** its prior verdict remains historical but does not satisfy the unscored query for the new input hash

#### Scenario: Concurrent input change rejects stale score

- **WHEN** the session input changes between judge-view creation and verdict persistence
- **THEN** the stale verdict is not attached to the newer session revision

### Requirement: Session-grain replacement enforces identity

Every child event and skill record supplied for a session-grain replacement SHALL carry
the same qualified session identity as the parent record. The store SHALL validate this
invariant before deleting or inserting any row.

#### Scenario: Mismatched child identity

- **WHEN** a replacement for session A contains an event or skill row for session B
- **THEN** the operation fails before mutation and both existing grains remain unchanged

### Requirement: Window query indexes

The schema SHALL provide indexes that support the report's session-kind, UTC date-range,
agent-type, and parent-session filters without scanning the complete session fact table.

#### Scenario: Window query uses an index

- **WHEN** the report queries a bounded date range in a populated store
- **THEN** SQLite can select the relevant session rows through a supporting index
