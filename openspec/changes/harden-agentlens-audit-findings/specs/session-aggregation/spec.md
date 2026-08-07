## MODIFIED Requirements

### Requirement: Derive fact_session per spawn

The system SHALL derive one `fact_session` row per qualified source session. A subagent
spawn's internal key SHALL combine source-project identity, session kind, and raw
`agent_id`; the raw ID SHALL remain available separately. Four spawns of the same
`agent_type` in one parent session SHALL produce four rows.

#### Scenario: One row per spawn, not per agent type

- **WHEN** a parent session fans out four spawns of the same `agent_type`
- **THEN** the store contains four distinct qualified session rows

#### Scenario: Same raw spawn ID in different projects

- **WHEN** two projects contain subagent transcripts with the same raw `agent_id`
- **THEN** aggregation emits two independent session rows

#### Scenario: Tool counts derived from fact_tool_event

- **WHEN** a session row is derived
- **THEN** its tool, error, denial, duplicate-call, and file-touch counts come from that qualified session's event rows

#### Scenario: Identity and lineage persisted

- **WHEN** a subagent session is aggregated
- **THEN** its row records raw and qualified identity, effective definition identity, source revision, and qualified parent lineage

## ADDED Requirements

### Requirement: Distinct files use path identity

The system SHALL compute `n_files_touched` from distinct normalized file-path hashes on
file-addressing events. It SHALL continue to compute duplicate tool calls from whole-input
hashes. Non-path arguments such as offsets, ranges, and replacement text SHALL NOT create
additional file identities.

#### Scenario: Repeated reads of one path

- **WHEN** a session reads the same normalized path with two different offsets
- **THEN** `n_files_touched` increases by one while the calls retain distinct whole-input hashes

#### Scenario: Different paths

- **WHEN** a session touches two different normalized paths
- **THEN** `n_files_touched` increases by two

### Requirement: Session date uses UTC instant

The system SHALL derive `session_date` by parsing the complete accepted timestamp,
normalizing it to UTC, and taking the UTC calendar date. Malformed or policy-invalid
timestamps SHALL produce no session date.

#### Scenario: Offset crosses UTC midnight

- **WHEN** a timestamp's written date differs from its UTC date
- **THEN** `session_date` uses the UTC date

#### Scenario: Malformed timestamp suffix

- **WHEN** a value begins with a valid date but the complete timestamp is invalid
- **THEN** aggregation records no session date
