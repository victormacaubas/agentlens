# Session Command Specification

## Purpose

The `agentlens session` command surface: how a caller asks for one agent run to be
analyzed, what the command promises about the user's data, and how failures are
reported to a script rather than only to a human reader.

## Requirements

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

### Requirement: `--dryrun` writes nothing

The command SHALL accept `--dryrun`, and under it SHALL write neither the store
nor the report artifact, logging what it would have written instead.

#### Scenario: Dry run reports without writing

- **WHEN** `--dryrun` is given
- **THEN** the command still parses the transcript and prints its usual report
  content, but no store file and no report artifact are created or modified, and
  the diagnostic stream names the store row and artifact path that would have
  been written

### Requirement: Scoring is opt-in on the session command

The command SHALL accept a flag that requests a modeled verdict for the spawn it is
analyzing, and SHALL NOT score anything unless that flag is given.

#### Scenario: Flag absent

- **WHEN** `agentlens session --file <path>` runs without the scoring flag
- **THEN** the run ingests and reports as before, no judge is invoked, and nothing
  is spent

#### Scenario: Flag present

- **WHEN** the scoring flag is given for a readable transcript
- **THEN** the run ingests the transcript, requests one verdict for it, and reports
  the verdict alongside the deterministic facts

#### Scenario: Resolved arguments are logged

- **WHEN** the command starts
- **THEN** the diagnostic stream records once, as JSON, whether scoring was
  requested and which model was requested for it

Rationale: a scored run costs money, so a scripted or scheduled invocation that
spent unexpectedly must be explainable from its own log line.

### Requirement: `--dryrun` does not invoke the judge

Under `--dryrun`, the command SHALL NOT call the judge and SHALL NOT write a
verdict, reporting instead what it would have scored and written.

#### Scenario: Dry run with scoring requested

- **WHEN** both `--dryrun` and the scoring flag are given
- **THEN** no judge call is made, nothing is spent, no verdict row is written, and
  the diagnostic stream names the spawn that would have been scored, the model that
  would have been requested, and the verdict identity that would have been written

Rationale: `--dryrun` is the flag a user reaches for to find out what a command will
do. A dry run that spends money to tell them would be the one surprise the flag
exists to prevent.

### Requirement: Failures are distinguishable by exit code

The command SHALL exit with a code that identifies the family of failure, so a
calling script can branch without parsing text output.

#### Scenario: Exit code per failure family

- **WHEN** the command fails
- **THEN** it exits 2 for an unusable invocation, 3 for a source that cannot be
  read soundly, 4 for a store failure, 5 for a judge failure, and 1 for any failure
  that escaped those families

#### Scenario: Judge failure is distinguishable from source and store failure

- **WHEN** scoring was requested and the judge could not be used or answered
  unusably
- **THEN** the command exits 5, and does not report the failure as a source or store
  failure

Rationale: exit codes are a public contract that scripts branch on. Reporting a
judge failure as a source failure would send a caller to check the transcript for a
problem that is not there.

#### Scenario: Success

- **WHEN** the command completes its work
- **THEN** it exits 0

### Requirement: The scorer's owner identity is logged

The diagnostic stream SHALL record the owner value this run uses to claim verdict
identities, once, in the same startup line that records the resolved arguments.

#### Scenario: Owner appears in the resolved-argument line

- **WHEN** the command starts with scoring requested
- **THEN** the resolved-argument line on the diagnostic stream carries the owner value
  this run will claim with, alongside whether scoring was requested and which model

#### Scenario: Two runs log different owners

- **WHEN** two invocations start
- **THEN** the owner values they log differ

Rationale: when a run reports a spawn as claimed elsewhere, the only way to find out
which process is holding it is to match the owner in the store against the owner a
process logged at startup. Without the line, a stuck claim is anonymous and the
operator's only recourse is to wait out the lease.

#### Scenario: The owner does not identify the machine or the user

- **WHEN** the owner value is logged
- **THEN** it carries no hostname, username, or path

Rationale: the diagnostic stream is pasted into issues and CI logs. An owner needs to
be unique and matchable, which does not require it to describe the host.
