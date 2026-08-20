## MODIFIED Requirements

### Requirement: Idempotent ingest

The system SHALL assign each discovered input a globally unique internal `session_id`
derived from its source-project identity, session kind, and raw Claude session or agent ID.
It SHALL retain the raw ID separately for display and lookup. A subagent's qualified
`parent_session_id` SHALL identify the main session in the same source project.

The system SHALL upsert sessions by the qualified `session_id` so that re-running ingest
never duplicates an input or allows a same-named input from another project or session kind
to overwrite it. The upsert SHALL cover the full session grain: `fact_session`,
`fact_tool_event`, and `bridge_session_skill`. It SHALL persist those rows and their
dimension backfills in one transaction per session. A failed, degraded, changed-during-read,
or stale re-ingest SHALL preserve the previously committed grain. Successful writes for
other sessions SHALL remain committed.

#### Scenario: Re-run adds no duplicates

- **WHEN** the pipeline ingests the same unchanged source input twice
- **THEN** the store contains one unchanged grain for that qualified session

#### Scenario: Colliding raw IDs remain distinct

- **WHEN** a main session and subagent share a raw ID, or two projects contain the same raw ID
- **THEN** each source input receives a distinct qualified `session_id` and retains its own grain

#### Scenario: Parent lineage stays within the source project

- **WHEN** two projects contain the same raw parent session ID
- **THEN** each subagent's qualified `parent_session_id` identifies the main session from its own project

#### Scenario: New sessions added on re-run

- **WHEN** the pipeline runs again after new sessions appear
- **THEN** the new qualified sessions are added and prior rows remain unchanged

#### Scenario: Full grain replaced on re-ingest

- **WHEN** a complete newer snapshot of an existing session is ingested
- **THEN** its session, event, and skill rows are replaced atomically with the newer grain

#### Scenario: Failed re-ingest preserves the prior session version

- **WHEN** a stored session is re-read with malformed, truncated, or non-object records
- **THEN** the prior grain remains committed and the target is reported as degraded and skipped

#### Scenario: Stale writer cannot replace newer input

- **WHEN** two ingesters parse different revisions of one source and the older revision writes last
- **THEN** the store rejects the stale revision and retains the newer complete grain

#### Scenario: Failed first ingest leaves no partial session

- **WHEN** any grain or dimension write fails while ingesting a new session
- **THEN** no row derived from that session remains in the store

#### Scenario: One failed target does not roll back other sessions

- **WHEN** one target fails during a bulk ingest containing other valid targets
- **THEN** the failed target is rolled back and the other targets remain committed

## ADDED Requirements

### Requirement: Discovery isolates filesystem failures

The system SHALL continue discovery across readable siblings when a project, transcript,
agent-definition, or skills directory is unreadable or disappears during traversal. It SHALL
report the affected path and count the discovery failure in the ingest summary. Applying an
ingest limit SHALL stop traversal after enough targets have been yielded rather than enumerate
the complete tree first.

#### Scenario: Unreadable project does not abort discovery

- **WHEN** one project directory raises an `OSError` while another remains readable
- **THEN** targets from the readable project are ingested and the failed path is reported

#### Scenario: Limit stops discovery

- **WHEN** ingest runs with a limit of 10 against more than 10 discoverable targets
- **THEN** discovery stops after selecting 10 targets and does not enumerate later projects

### Requirement: Parse health and source revision

The parser SHALL process JSONL incrementally and return records or derived facts together with
line counts, malformed/non-object counts, incomplete-final-line status, and a stable source
revision. It SHALL bound retained decoded payload state independently of transcript size. The
ingest transaction SHALL compare the parsed revision with the current and stored revisions
before replacing a grain.

#### Scenario: Malformed content is visible

- **WHEN** a transcript contains malformed or non-object lines
- **THEN** parsing continues, records the degraded counts, and does not present the snapshot as complete

#### Scenario: File changes during parsing

- **WHEN** the transcript revision changes between the start and end of a parse
- **THEN** the target is retried or skipped and no mixed snapshot is persisted

#### Scenario: Large transcript uses bounded memory

- **WHEN** a transcript contains many large records
- **THEN** parsing retains only bounded active state rather than a decoded copy of the whole file

### Requirement: Unambiguous raw-ID lookup

Raw session IDs SHALL remain a user-facing lookup convenience. A raw-ID lookup that matches
more than one qualified source SHALL fail with an ambiguity error that identifies the available
project and session-kind qualifiers.

#### Scenario: Ambiguous raw ID

- **WHEN** a user requests a raw ID present in two projects or kinds
- **THEN** the command reports the ambiguity and writes no session

### Requirement: Timestamp normalization

The parser SHALL validate complete ISO timestamps, normalize offset-aware timestamps to UTC,
and apply one documented policy to timezone-naive values. Malformed or mixed timestamp forms
SHALL NOT raise during duration extraction.

#### Scenario: Equivalent instants have equal duration behavior

- **WHEN** timestamps express equivalent instants using `Z` and explicit offsets
- **THEN** duration uses their normalized UTC instants

#### Scenario: Mixed or malformed timestamps

- **WHEN** the first and last timestamp cannot be compared under the documented policy
- **THEN** duration defaults to zero, parsing continues, and the remaining facts are preserved
