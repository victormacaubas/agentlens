## Purpose

Defines how callers request a deterministic subagent report, select its window,
filter its scope, and consume its output without invoking an LLM.

## ADDED Requirements

### Requirement: CLI help is successful control flow

The CLI SHALL use root-command dispatch and SHALL treat help as a successful
request rather than an exception.

#### Scenario: Root help is requested
- **WHEN** the caller runs `agentlens --help`
- **THEN** the CLI lists its subcommands, writes no traceback, and exits 0

#### Scenario: Subcommand help is requested
- **WHEN** the caller runs `agentlens session --help` or `agentlens report --help`
- **THEN** the CLI shows that subcommand's options, writes no traceback, and
  exits 0

### Requirement: Report command discovers before aggregating

The command SHALL accept `agentlens report`, discover and ingest sound subagent
sources, and then build the report from the resulting store state.

#### Scenario: New subagent transcript exists
- **WHEN** `agentlens report` runs and discovery finds a sound subagent
  transcript not yet stored
- **THEN** the transcript is ingested and its spawn is eligible for the
  requested report window in the same run

#### Scenario: Main-session transcript exists
- **WHEN** the source tree contains both main-session and subagent transcripts
- **THEN** the report ingests and covers only subagent sessions

### Requirement: Exactly one window selector is accepted

The report command SHALL require one of `--since`, `--window`, or `--from`
with `--to`, and SHALL reject ambiguous combinations.

#### Scenario: Relative duration is supplied
- **WHEN** the caller runs `agentlens report --since 7d`
- **THEN** the report covers the seven-day duration ending at the resolved
  current instant

#### Scenario: Named calendar window is supplied
- **WHEN** the caller runs `agentlens report --window this-week`
- **THEN** the report covers the current local-calendar week through the
  resolved current instant

#### Scenario: Explicit range is supplied
- **WHEN** the caller supplies `--from <date> --to <date>`
- **THEN** the report covers the half-open range beginning at `--from` and
  ending at `--to`

#### Scenario: Selectors conflict
- **WHEN** the caller combines more than one window selector form or supplies
  only one side of an explicit range
- **THEN** the command rejects the invocation with the configuration-error exit
  code and writes no store or report artifact

### Requirement: Reports can filter by agent type

The report command SHALL accept an optional `--agent <name>` filter and SHALL
apply it to both the current and prior comparison windows.

#### Scenario: Agent filter is present
- **WHEN** the caller requests one agent type
- **THEN** spawn rows and aggregates for other agent types are absent from the
  report

#### Scenario: Agent filter has no matches
- **WHEN** no spawn in the current window matches the requested agent type
- **THEN** the command succeeds with an empty deterministic report rather than
  treating zero results as an error

### Requirement: Machine-readable output stays isolated

The command SHALL accept `--format json`, writing the JSON document and nothing
else to standard output while sending diagnostics to the diagnostic stream.

#### Scenario: JSON report emits diagnostics
- **WHEN** a JSON report discovers skipped or unchanged sources
- **THEN** standard output remains one parseable JSON document and diagnostics
  appear only on the diagnostic stream

#### Scenario: Resolved arguments are logged
- **WHEN** either CLI workflow starts successfully
- **THEN** its resolved arguments are logged once at INFO level on the
  diagnostic stream without adding content to standard output

### Requirement: Report exit codes preserve the CLI taxonomy

The report command SHALL return the existing public exit codes: 0 for success,
1 for an unexpected failure, 2 for configuration errors, 3 for source errors,
and 4 for store errors.

#### Scenario: Report workflow fails
- **WHEN** report execution raises a configuration, source, store, or unexpected
  failure
- **THEN** the process exits with the corresponding public code and keeps
  diagnostics off standard output

### Requirement: Default output overwrites a stable artifact

Without an explicit output format, the command SHALL write the deterministic
JSON report to a stable path derived from the resolved window and agent filter,
overwriting the prior artifact for the same scope.

#### Scenario: Same report scope runs twice
- **WHEN** the same window selector and agent filter are reported twice
- **THEN** one current artifact exists for that scope rather than two
  timestamped copies

### Requirement: Dry run performs no writes

The command SHALL accept `--dryrun`, compute the report from existing and
newly parsed in-memory facts, and write neither the store nor a report artifact.

#### Scenario: Dry run discovers new subagents
- **WHEN** `--dryrun` discovers sound subagent transcripts absent from the store
- **THEN** those facts appear in the computed report, the store remains
  unchanged, no report artifact is written, and diagnostics name the writes
  that were skipped

### Requirement: Deterministic report never invokes the judge

The report command SHALL produce Phase 2 output without constructing or calling
a judge backend.

#### Scenario: Report contains unscored spawns
- **WHEN** the selected window contains subagent spawns with no verdicts
- **THEN** the report succeeds with deterministic facts and no score, verdict,
  or fix fields
