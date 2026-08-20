# Session Parser

## Purpose

Discovers Claude session logs and agent definitions under the user's `.claude/` tree, parses tool events, resolves subagent parent lineage and agent names, and ingests sessions idempotently into the store.

## Requirements

### Requirement: Discover session logs and agent definitions

The system SHALL discover main sessions (`projects/**/*.jsonl`), subagent runs (`projects/**/<sid>/subagents/agent-*.jsonl`) with their `.meta.json` sidecars, and agent definitions (`.claude/agents/**`) under the user's `.claude/` tree.

#### Scenario: Subagent run and sidecar are paired

- **WHEN** discovery encounters `subagents/agent-<id>.jsonl`
- **THEN** it pairs it with the sibling `agent-<id>.meta.json` when present

#### Scenario: Main sessions are discovered

- **WHEN** discovery scans `projects/**/*.jsonl` at the top level of a project folder
- **THEN** main session files are found and marked for ingest as `session_kind = main`

### Requirement: Parse tool events into fact_tool_event

The system SHALL parse each session's `tool_use`/`tool_result` content blocks into `fact_tool_event` rows, capturing `tool_name`, `is_error`, `denial_kind` (from `toolDenialKind`), timestamp, an input hash, and output size, ordered by `seq`.

#### Scenario: Tool events are recorded in order

- **WHEN** a subagent transcript contains multiple tool calls
- **THEN** each produces one `fact_tool_event` row with a monotonic `seq` reflecting its order in the session

#### Scenario: Errors and denials are captured

- **WHEN** a `tool_result` has `is_error` true or a `toolDenialKind` is present
- **THEN** the corresponding `fact_tool_event` row records `is_error` and `denial_kind` accordingly

#### Scenario: Malformed lines are skipped, not fatal

- **WHEN** a JSONL line is malformed or an unknown record type
- **THEN** the parser skips it and continues without aborting the session ingest

### Requirement: Resolve parent lineage

The system SHALL resolve subagent parent lineage from the filesystem path and `.meta.json`: `parent_session_id` from the `<sid>` folder containing the `subagents/` directory, and `spawn_tool_use_id` from the sidecar's `toolUseId`.

#### Scenario: Parent session derived from path

- **WHEN** a subagent run at `projects/<proj>/<sid>/subagents/agent-<id>.jsonl` is ingested
- **THEN** its `parent_session_id` is `<sid>`

#### Scenario: Spawn tool use id from sidecar

- **WHEN** the `.meta.json` contains a `toolUseId`
- **THEN** the run's `spawn_tool_use_id` is set to that value for joining to the parent `Task` block

### Requirement: Guarded name resolution

The system SHALL resolve each session's agent name exactly once using a fallback chain — (1) `.meta.json` `agentType`, (2) `attributionAgent` from assistant records, (3) parent `Task` `subagent_type` via `spawn_tool_use_id`, (4) `agent_id` hash — recording the winning source in `name_source` and never dropping a session.

#### Scenario: Authoritative meta wins

- **WHEN** a `.meta.json` `agentType` is present
- **THEN** it is used as the agent name and `name_source` records the meta source

#### Scenario: Fallback to hash never drops a session

- **WHEN** no meta, attribution, or parent Task name is available
- **THEN** the `agent_id` hash is used and the session is still ingested

#### Scenario: Conflicting names flagged ambiguous

- **WHEN** the resolution chain yields conflicting distinct names
- **THEN** the session is flagged `ambiguous` rather than silently picking one

### Requirement: Tag session kind

The system SHALL tag each ingested session with `session_kind` (`subagent` or `main`). Main sessions SHALL be stored without lineage and SHALL NOT be scored in this change.

#### Scenario: Main session stored without lineage

- **WHEN** a main session is ingested
- **THEN** it is recorded with `session_kind = main` and no parent lineage fields

### Requirement: Idempotent ingest

The system SHALL upsert sessions by `session_id` so that re-running ingest adds only genuinely new sessions and never duplicates existing rows. The upsert SHALL cover the full session grain — `fact_session`, `fact_tool_event`, and `bridge_session_skill` — so a re-ingested session replaces its rows in every table, not only `fact_tool_event`. The system SHALL persist those rows and their `dim_date` and `dim_tool` backfills in one transaction per session. If any write fails, the system SHALL roll back every write for that session while retaining successful writes for other sessions.

#### Scenario: Re-run adds no duplicates

- **WHEN** the pipeline ingests the same session twice
- **THEN** the store contains exactly one set of rows for that session in every table after the second run

#### Scenario: New sessions added on re-run

- **WHEN** the pipeline runs again after new sessions appear
- **THEN** only the new sessions are ingested and prior rows are unchanged

#### Scenario: Full grain replaced on re-ingest

- **WHEN** a session already present is ingested again
- **THEN** its `fact_session`, `fact_tool_event`, and `bridge_session_skill` rows are all replaced with the freshly parsed set

#### Scenario: Failed re-ingest preserves the prior session version

- **WHEN** any session-grain or dimension write fails while re-ingesting an existing session
- **THEN** every row derived from the session remains at its previously committed version

#### Scenario: Failed first ingest leaves no partial session

- **WHEN** any session-grain or dimension write fails while ingesting a new session
- **THEN** the store contains no partially persisted rows derived from that session

#### Scenario: One failed target does not roll back other sessions

- **WHEN** one target fails during a bulk ingest containing other valid targets
- **THEN** the failed target is rolled back and the other targets remain successfully committed

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
