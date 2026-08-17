## Purpose

The `agentlens session` command surface: how a caller asks for one agent run to be
analyzed, what the command promises about the user's data, and how failures are
reported to a script rather than only to a human reader.

## ADDED Requirements

### Requirement: Analyze one transcript by explicit path

The command SHALL accept `agentlens session --file <path>`, where `<path>` names a
single subagent transcript, and SHALL ingest that transcript and render a report
for it.

#### Scenario: Valid transcript with sidecar

- **WHEN** `--file` names a readable transcript that sits under a
  `.claude/projects/<project>/` tree and has a sibling metadata sidecar
- **THEN** the run persists one session record and its tool-invocation records,
  writes the report artifact, prints a summary, and exits 0

#### Scenario: Transcript without a metadata sidecar

- **WHEN** the transcript is readable but no sibling sidecar exists
- **THEN** the run still succeeds, the agent name falls back to a derived value,
  and the report records which source supplied the name

#### Scenario: Path does not exist

- **WHEN** `--file` names a path that cannot be read
- **THEN** the command reports the path it tried and exits 3, and no store or
  report file is created or modified

#### Scenario: Transcript outside a project tree

- **WHEN** the file is readable but its location does not identify an owning
  project
- **THEN** the command refuses to ingest it, states that a session identity cannot
  be qualified without an owning project, and exits 3

Rationale: a raw transcript ID is not unique across projects, so ingesting a file
whose project is unknown would create a record that can silently collide.

### Requirement: The user's `.claude/` directory is never written

The command SHALL treat everything under `.claude/` as read-only, for every code
path including failure paths.

#### Scenario: No writes to the source tree

- **WHEN** any invocation of the command completes, whether it succeeded or failed
- **THEN** no file under `.claude/` has been created, modified, moved, or deleted

### Requirement: Re-running is safe

Running the command twice on the same unchanged transcript SHALL leave the same
data as running it once.

#### Scenario: Second run on unchanged input

- **WHEN** the command runs a second time against a transcript that has not changed
- **THEN** the stored record count is unchanged, no duplicate session or
  tool-invocation records exist, and the report is regenerated with equivalent
  content

### Requirement: Failures are distinguishable by exit code

The command SHALL exit with a code that identifies the family of failure, so a
calling script can branch without parsing text output.

#### Scenario: Exit code per failure family

- **WHEN** the command fails
- **THEN** it exits 2 for an unusable invocation, 3 for a source that cannot be
  read soundly, 4 for a store failure, and 1 for any failure that escaped those
  families

#### Scenario: Success

- **WHEN** the command completes its work
- **THEN** it exits 0
