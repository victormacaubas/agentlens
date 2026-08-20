# Subagent Discovery Specification

## Purpose

Finds every subagent spawn available to a report and ingests each sound source
without treating main-session transcripts as reportable runs.

## Requirements

### Requirement: Discovery finds subagent transcripts across project trees

The system SHALL discover every `agent-*.jsonl` transcript under a project's
`subagents` directories and SHALL qualify each result with its owning project
and raw subagent identifier.

#### Scenario: Multiple projects contain subagent runs
- **WHEN** discovery scans Claude project roots containing subagent transcripts
  in more than one project
- **THEN** it returns every subagent transcript with an identity qualified to
  its own project

#### Scenario: Main-session transcript is encountered
- **WHEN** discovery encounters a project-level main-session JSONL file
- **THEN** it does not return or ingest that file as part of the subagent report

### Requirement: Discovery ingests every sound subagent snapshot

A report run SHALL parse and upsert each discovered subagent transcript before
it queries the report window.

#### Scenario: New and unchanged transcripts are discovered
- **WHEN** discovery finds one new transcript and one transcript already stored
  at the same source revision
- **THEN** the new spawn is persisted, the unchanged spawn is not duplicated,
  and both are eligible for the report window

#### Scenario: One discovered transcript is unsound
- **WHEN** one discovered transcript changes during its read
- **THEN** that snapshot does not replace stored data and the diagnostic output
  identifies the rejected source without writing a partial snapshot

### Requirement: Source trees remain read-only

Discovery and bulk ingestion SHALL NOT create, modify, move, or delete any file
under a user's `.claude/` directory.

#### Scenario: Bulk report completes
- **WHEN** a report discovers and ingests any number of subagent transcripts
- **THEN** all files and directories under `.claude/` remain unchanged

### Requirement: Qualified parent identity is retained as metadata

Each discovered subagent SHALL retain the qualified main-session identifier
derived from its project and parent-session path, even though the corresponding
main-session transcript is not ingested.

#### Scenario: Subagent path contains a parent session
- **WHEN** a subagent transcript is discovered beneath a parent-session
  directory
- **THEN** its stored session facts include the qualified parent identifier
  using the same project and the `main` session kind
