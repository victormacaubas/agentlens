## ADDED Requirements

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

## MODIFIED Requirements

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
