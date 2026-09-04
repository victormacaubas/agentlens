## Purpose

Defines how callers request a scoring run over a window, select and filter its scope,
preview its cost, and consume its outcome, including the exit code a run with failed
spawns produces.

## Requirements

### Requirement: Scoring a window is requested by its own command

The CLI SHALL accept `agentlens score` as the way to score a window, and the
deterministic report command SHALL remain incapable of invoking the judge.

#### Scenario: A scoring run is requested

- **WHEN** the caller runs `agentlens score` with a window selector
- **THEN** the spawns in that window are scored, and the run's outcome is reported

#### Scenario: Reporting still never scores

- **WHEN** the caller runs the report command over a window containing unscored spawns
- **THEN** no judge is constructed or called, and the report succeeds with
  deterministic facts alone

Rationale: the deterministic path is free and this one is not. Keeping them as
separate commands means no one spends money by running the command they already run
for facts.

#### Scenario: Scoring discovers before it scores

- **WHEN** a scoring run is requested and discovery finds a sound subagent transcript
  not yet stored
- **THEN** the transcript is ingested and its spawn is eligible for scoring in the
  same run

### Requirement: Exactly one window selector is accepted

The score command SHALL require one of `--since`, `--window`, or `--from` with
`--to`, and SHALL reject ambiguous combinations, resolving the window the same way
the report command does.

#### Scenario: Relative duration is supplied

- **WHEN** the caller runs `agentlens score --since 7d`
- **THEN** the run covers the seven-day duration ending at the resolved current
  instant

#### Scenario: Named calendar window is supplied

- **WHEN** the caller runs `agentlens score --window this-week`
- **THEN** the run covers the current local-calendar week through the resolved
  current instant

#### Scenario: Explicit range is supplied

- **WHEN** the caller supplies `--from <date> --to <date>`
- **THEN** the run covers the half-open range beginning at `--from` and ending at
  `--to`

#### Scenario: Selectors conflict

- **WHEN** the caller combines more than one window selector form, supplies only one
  side of an explicit range, or supplies none
- **THEN** the command rejects the invocation with the configuration-error exit code,
  calls no judge, and writes nothing

#### Scenario: A window resolves identically across commands

- **WHEN** the same selector is given to the score command and to the report command
- **THEN** both resolve the same window bounds

Rationale: a reader comparing a report against the run that scored it must not have
to wonder whether the two commands read `--since 7d` the same way.

### Requirement: Scoring runs are scoped and directed by flags

The score command SHALL accept an optional `--agent <name>` filter, a
`--judge-model` selecting which model is asked, a `--max-run-cost-usd` setting the
run's spend ceiling, and a `--store` selecting the store, and SHALL log its resolved
arguments once at startup.

#### Scenario: Agent filter is present

- **WHEN** the caller requests one agent type
- **THEN** spawns of other agent types are not scored and are not counted

#### Scenario: Agent filter has no matches

- **WHEN** no spawn in the window matches the requested agent type
- **THEN** the run succeeds having covered nothing, rather than treating zero results
  as an error

#### Scenario: Resolved arguments are logged

- **WHEN** a scoring run starts successfully
- **THEN** its resolved arguments, including the window, the filter, the requested
  model, and the ceiling, are logged once on the diagnostic stream without adding
  content to standard output

Rationale: a scheduled run that behaves oddly is explained by that one line, and a
run that spends money is exactly the kind whose arguments a reader will later
question.

### Requirement: Machine-readable output stays isolated

The command SHALL accept `--format json`, writing the run outcome and nothing else to
standard output while sending progress and diagnostics to the diagnostic stream.

#### Scenario: JSON outcome stays parseable while a run reports progress

- **WHEN** a run emits progress for each spawn it scores and finishes with a JSON
  outcome
- **THEN** standard output remains one parseable document and every progress line
  appears only on the diagnostic stream

#### Scenario: Progress carries identifying context

- **WHEN** a run reports progress for a spawn
- **THEN** the line names the spawn and its agent type, so interleaved runs remain
  readable

#### Scenario: Failures are reported without ending output

- **WHEN** the run's spawns include failures
- **THEN** each failure and its cause appear on the diagnostic stream, and the counts
  on standard output remain the machine-readable record of them

### Requirement: A completed run succeeds even when spawns failed

The command SHALL exit 0 when a run covered its window, whatever mix of outcomes its
spawns produced, and SHALL exit with the judge failure code when a run stopped
because the judge was unusable.

#### Scenario: A run completes with some spawns failed

- **WHEN** a run covers its whole window and some spawns failed
- **THEN** the process exits 0, and the failed count in the outcome is what a caller
  branches on

Rationale: one spawn failing must not sink the batch, and an exit code that reports
it as a total failure is the same thing in a different costume. Exit codes are a
public contract organized by error family, so "completed with failures" does not earn
a new one.

#### Scenario: A run aborted by an unusable judge fails

- **WHEN** a run stops because its consecutive-failure bound was reached
- **THEN** the process exits with the judge failure code and names the cause

#### Scenario: A run stopped at its ceiling succeeds

- **WHEN** a run stops at its spend ceiling
- **THEN** the process exits 0 and the outcome names the ceiling as the stop reason

Rationale: reaching a ceiling the caller set is the bound working, not a failure. The
caller decides whether to raise it and run again.

#### Scenario: Other failures preserve the taxonomy

- **WHEN** a scoring run raises a configuration, source, store, or unexpected failure
- **THEN** the process exits with the corresponding public code and keeps diagnostics
  off standard output

### Requirement: Dry run previews a scoring run without spending

The command SHALL accept `--dryrun`, report the spawns it would score and an upper
bound on their cost, and neither call the judge nor write to the store.

#### Scenario: Dry run over an unscored window

- **WHEN** `--dryrun` covers a window whose spawns have no reusable verdicts
- **THEN** the count that would be scored and the cost upper bound are reported, no
  judge process is started, and diagnostics name the writes that were skipped

#### Scenario: Dry run reports reuse at no cost

- **WHEN** `--dryrun` covers a window whose spawns already have reusable verdicts
- **THEN** those spawns are reported as reuses contributing nothing to the bound
