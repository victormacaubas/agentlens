# Session Parser Specification

## Purpose

Turning one agent transcript and its metadata sidecar into the typed records the
store holds: a qualified identity that cannot collide across projects, a snapshot
that is provably whole, one record per tool invocation, and one record per spawn.

## Requirements

### Requirement: Session identity is qualified, not raw

A session's key SHALL be derived from the owning project, the session kind, and
the raw transcript ID together. The three components SHALL be retained alongside
the derived key.

#### Scenario: Same transcript yields the same key

- **WHEN** the same unchanged transcript is parsed twice
- **THEN** both parses produce an identical derived key

#### Scenario: Same raw ID in two projects yields two keys

- **WHEN** two different projects each contain a transcript carrying the same raw
  transcript ID
- **THEN** the two parses produce different derived keys, and each record reports
  its own owning project

#### Scenario: Components remain available for display

- **WHEN** a session record is read back
- **THEN** the raw transcript ID, the owning project, and the session kind are all
  available without reversing the derived key

Rationale: the derived key is not human-readable, so every display path and error
message needs the original components.

### Requirement: A snapshot is rejected unless it is provably whole

The parser SHALL record the source file's revision, meaning its modification time,
its size, and a hash of its contents, before reading and SHALL verify that
revision after reading.

#### Scenario: File changed during the read

- **WHEN** the source file's revision after the read differs from the revision
  before it
- **THEN** the run fails with the source-error exit code, names the soundness rule
  that was violated, and writes nothing to the store

#### Scenario: Revision travels with the record

- **WHEN** a session record is persisted
- **THEN** the revision observed at parse time is stored with it, so a later run
  can tell whether its own snapshot is newer

### Requirement: One record per tool invocation

A tool invocation and its matching result SHALL be represented as a single record,
ordered within its session.

#### Scenario: Invocation with a result

- **WHEN** the transcript contains a tool invocation followed by its matching result
- **THEN** one record holds the tool name, an input fingerprint, a normalized file
  identity where the tool acted on a file, the timestamp, whether the result was
  an error, any typed permission-denial reason, and the result size

#### Scenario: Invocation with no result

- **WHEN** the transcript contains a tool invocation that never received a result,
  because the run was interrupted or abandoned
- **THEN** a record is still produced, its result fields are empty, and the parse
  is not treated as a failure

Rationale: an abandoned call is a signal about how the run went. Dropping it would
hide the interruption and undercount the work attempted.

#### Scenario: Total invocations need no filtering

- **WHEN** the tool-invocation records for a session are counted
- **THEN** the count of all records equals the number of tool invocations in that
  session

### Requirement: One session record per spawn

Each parsed transcript SHALL produce exactly one session record representing that
single agent run, never one per agent type.

#### Scenario: Volume and health are derived from the invocations

- **WHEN** a session record is produced
- **THEN** it carries the number of turns, the number of tool invocations, counts
  per tool category, the number of distinct files touched, the number of errors,
  the number of permission denials, the number of repeated identical invocations,
  the run duration, and the token counts including cache reads

#### Scenario: Identity and task fields come from the sidecar

- **WHEN** a metadata sidecar is present
- **THEN** the session record carries the agent type, the task description, the
  spawning tool-use reference, and the nesting depth taken from that sidecar

Rationale: several of these fields are not aggregations of any tool invocation, so
the session record is not a pure rollup of the invocation records.

### Requirement: Name resolution records which source won

The agent type SHALL be resolved once per session through the ordered chain of
the metadata sidecar, distinct assistant-record attribution, the parent
record's spawning subagent invocation, and the raw-agent-identifier fallback.
That spawning invocation SHALL be recognized under either tool name Claude Code
has written for it, `Agent` or the historical `Task`, because the logs being read
can span versions.
The record SHALL state which link supplied the answer and SHALL mark conflicting
non-fallback values as ambiguous.

#### Scenario: Sidecar is authoritative

- **WHEN** a metadata sidecar supplies an agent type
- **THEN** that value is used and the record states that the sidecar was the
  source

#### Scenario: Attribution supplies the name

- **WHEN** the sidecar has no agent type and assistant records contain one
  distinct attribution-agent value
- **THEN** that value is used and the record states that assistant attribution
  was the source

#### Scenario: Parent task supplies the name

- **WHEN** neither the sidecar nor assistant attribution supplies an agent type
  and the spawning parent invocation names a subagent type
- **THEN** that value is used and the record states that the parent task was the
  source

#### Scenario: Sources conflict

- **WHEN** available non-fallback name sources provide conflicting agent types
- **THEN** the record retains a deterministic value, marks the name source as
  ambiguous, and the session is not dropped

#### Scenario: No source available

- **WHEN** no sidecar, assistant attribution, or parent task can supply an agent
  type
- **THEN** a value derived from the transcript's own raw agent identifier is
  used, the record states that this fallback was the source, and the session is
  not dropped

Rationale: a session with an unknown or disputed agent type is still worth
analyzing. Dropping it would silently shrink every count that includes it.

### Requirement: Parse health is counted, not hidden

Records the parser cannot understand SHALL be counted and reported rather than
silently discarded or allowed to abort an otherwise sound read.

#### Scenario: Some lines are unreadable

- **WHEN** a transcript contains lines that cannot be parsed, alongside lines that
  can
- **THEN** the unreadable lines are skipped, their count is reported with the
  session, and the sound portion is still ingested

#### Scenario: Nothing usable in the file

- **WHEN** a transcript yields no usable records at all, or lacks the data needed
  to establish an identity
- **THEN** the run fails with the source-error exit code and writes nothing to the
  store

Rationale: an unsound parse is a failure, not a partial result. Surfacing it as a
value a caller may forget to check would let a half-read snapshot reach the store.

### Requirement: Session records carry deterministic reporting context

Each parsed subagent session SHALL retain its raw agent identifier, effective
agent-definition identifier when available, qualified parent-session
identifier, spawn start time, task-prompt length, and distinct fired-skill
count.

#### Scenario: Modern subagent has sidecar and definition
- **WHEN** a subagent transcript has a metadata sidecar and an effective agent
  definition
- **THEN** its session record carries the raw agent identifier, effective
  definition identity, qualified parent identity, earliest usable transcript
  timestamp, task-description length, and fired-skill count

#### Scenario: Definition cannot be resolved
- **WHEN** a subagent transcript is sound but has no effective agent definition
- **THEN** the session record remains valid with no agent-definition identity

### Requirement: Parent metadata does not require main-session ingestion

The parser SHALL derive a subagent's qualified parent identifier from its
project and path and MAY inspect the parent transcript for name-resolution
evidence without persisting that main session.

#### Scenario: Parent transcript is available
- **WHEN** the sidecar does not name the agent type and the parent transcript
  contains the spawning subagent invocation
- **THEN** the parser may use that invocation's subagent type in name resolution
  while persisting only the subagent session

#### Scenario: Parent transcript is unavailable
- **WHEN** the parent transcript cannot be inspected
- **THEN** the subagent remains ingestible through the remaining name-resolution
  links

### Requirement: Derivation identity covers every shaping input

The parser SHALL produce a deterministic derivation fingerprint from the sound
transcript, sidecar, applicable definition and skill evidence, and any parent
record used for name resolution.

#### Scenario: Sidecar changes while transcript is unchanged
- **WHEN** a sidecar field changes but the transcript content does not
- **THEN** the session's transcript revision remains the same and its derivation
  fingerprint changes

#### Scenario: Context inputs are unchanged
- **WHEN** the transcript and every input that shapes derived facts are
  unchanged
- **THEN** repeated parsing produces the same derivation fingerprint
