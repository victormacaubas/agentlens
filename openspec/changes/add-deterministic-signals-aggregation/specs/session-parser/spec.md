## ADDED Requirements

### Requirement: Extract usage, turns, and duration

The system SHALL extract, from each transcript, per-turn token usage (summed across assistant records' `message.usage`), the assistant turn count, and the session duration (first-to-last timestamp span), returning them on the parsed session for aggregation. Missing or malformed usage SHALL contribute zero and SHALL NOT abort parsing.

#### Scenario: Usage summed and returned

- **WHEN** a transcript has assistant records carrying `message.usage`
- **THEN** the parsed session carries summed `input_tokens`, `output_tokens`, `cache_read_tokens`, and `cache_creation_tokens`, the assistant turn count, and the duration

#### Scenario: Absent usage does not abort

- **WHEN** an assistant record omits `usage` or a usage field
- **THEN** that contribution is zero and parsing completes normally

### Requirement: Extract skill-fire signals

The system SHALL extract skill-fire signals from a transcript: the skill name from any `isMeta:true` record carrying `<skill-format>true` and a `<command-name>`, and the skill name from any `Skill` tool_use. These signals feed `bridge_session_skill.fired`.

#### Scenario: Injection marker yields a fired skill name

- **WHEN** a transcript contains an `isMeta:true` record with `<skill-format>true` and `<command-name>code-audit</command-name>`
- **THEN** the parser reports `code-audit` as a fired skill

#### Scenario: Skill tool_use yields a fired skill name

- **WHEN** a transcript contains a `Skill` tool_use naming a skill in its input
- **THEN** the parser reports that skill as fired

## MODIFIED Requirements

### Requirement: Idempotent ingest

The system SHALL upsert sessions by `session_id` so that re-running ingest adds only genuinely new sessions and never duplicates existing rows. The upsert SHALL cover the full session grain — `fact_session`, `fact_tool_event`, and `bridge_session_skill` — so a re-ingested session replaces its rows in every table, not only `fact_tool_event`.

#### Scenario: Re-run adds no duplicates

- **WHEN** the pipeline ingests the same session twice
- **THEN** the store contains exactly one set of rows for that session in every table after the second run

#### Scenario: New sessions added on re-run

- **WHEN** the pipeline runs again after new sessions appear
- **THEN** only the new sessions are ingested and prior rows are unchanged

#### Scenario: Full grain replaced on re-ingest

- **WHEN** a session already present is ingested again
- **THEN** its `fact_session`, `fact_tool_event`, and `bridge_session_skill` rows are all replaced with the freshly parsed set
