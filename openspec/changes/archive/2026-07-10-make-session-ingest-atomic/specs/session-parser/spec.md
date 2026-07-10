## MODIFIED Requirements

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
