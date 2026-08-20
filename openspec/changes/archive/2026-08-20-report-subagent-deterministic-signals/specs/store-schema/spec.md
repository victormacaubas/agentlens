## ADDED Requirements

### Requirement: Session rows persist deterministic reporting context

The `fact_session` table SHALL retain the raw agent identifier, effective
agent-definition identity when available, qualified parent identity, spawn
start time, task-prompt length, and distinct fired-skill count for each
subagent spawn.

#### Scenario: Enriched subagent is stored and read
- **WHEN** an enriched subagent session is upserted and read back
- **THEN** every deterministic reporting-context value matches the parsed
  source facts

### Requirement: Agent-definition versions are queryable

The store SHALL persist one catalog row per versioned agent definition,
including its scope, source project where applicable, configuration, declared
tools, and declared skills.

#### Scenario: Definition is edited
- **WHEN** a new content version of an existing scoped definition is ingested
- **THEN** the new content identity is queryable and sessions older than its
  observed modification time do not claim that version

### Requirement: Session-skill bridge has session-skill grain

The `bridge_session_skill` table SHALL hold at most one row per qualified
session and skill name, with independent declared, available, and fired values.
Declared and available SHALL preserve an unknown state; fired SHALL remain
boolean.

#### Scenario: Session is reingested with changed skill evidence
- **WHEN** a newer sound snapshot changes the resolved skill states for a
  session
- **THEN** that session's bridge rows are replaced atomically and no obsolete
  or duplicate skill rows remain

### Requirement: Report windows are queryable without model output

The store SHALL return subagent spawn rows and deterministic agent rollups for
a current half-open start-time range and an equal-length prior range without
joining modeled verdict data.

#### Scenario: Current and prior ranges contain spawns
- **WHEN** a caller queries resolved current and prior bounds
- **THEN** the result includes each qualifying current-window spawn and the
  deterministic values needed to compare agent rollups across both ranges

#### Scenario: Main-session rows exist in a future-compatible store
- **WHEN** the store contains a row whose session kind is `main`
- **THEN** the Phase 2 report query excludes it from subagent spawn rows and
  aggregates

### Requirement: Context changes refresh derived session facts

The store SHALL use the derivation fingerprint and newest shaping-input
observation time to update deterministic context without conflating it with the
transcript content revision.

#### Scenario: Sidecar changes after initial ingest
- **WHEN** a newer sound derivation changes sidecar-backed facts while the
  transcript content is unchanged
- **THEN** the session and dependent skill rows reflect the newer derivation
  without duplicating tool-event rows

#### Scenario: Older derivation arrives
- **WHEN** an incoming derivation was observed before the derivation already
  stored for that session
- **THEN** the stored session, tool-event, and skill rows remain untouched

### Requirement: Added store data remains reproducible

Every added session field, agent-definition row, and session-skill row SHALL be
regenerable by re-reading subagent transcripts, sidecars, agent definitions,
and skill inventories.

#### Scenario: Store is rebuilt
- **WHEN** the store is deleted and the same subagent source tree is ingested
  again
- **THEN** the added deterministic rows and values are equivalent to those in
  the deleted store
