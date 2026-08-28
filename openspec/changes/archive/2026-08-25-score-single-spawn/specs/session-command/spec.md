## ADDED Requirements

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

## MODIFIED Requirements

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
