## Purpose

Scoring a window's worth of spawns in one run: which spawns a run covers, how it
survives a spawn that fails, what bounds its retries and its spend, and what its
counts mean to a reader deciding whether to run it again.

## ADDED Requirements

### Requirement: A run covers the qualifying spawns in its window

A scoring run SHALL cover every subagent spawn whose start falls inside the resolved
window and matches the run's filters, SHALL reuse a stored verdict wherever the
spawn's identity resolves to one, and SHALL call the judge only for the rest.

#### Scenario: A window of unscored spawns is scored

- **WHEN** a run is requested over a window containing spawns for which no reusable
  verdict exists
- **THEN** each of those spawns is scored, and a verdict is recorded for each

#### Scenario: A window with no spawns is not an error

- **WHEN** a run is requested over a window containing no spawns, or none matching its
  filters
- **THEN** the run succeeds, reports that it covered nothing, and calls no judge

Rationale: this is the path a new user hits on a first run, before any transcript
exists in the window they guessed at. Treating it as a failure teaches them the tool
is broken.

#### Scenario: The same window is scored twice

- **WHEN** a run is requested over a window that has already been scored, with no
  spawn's judge input, rubric version, or resolved model changed
- **THEN** every spawn is reported as reused, no judge call is made, and the run
  spends nothing

#### Scenario: A window mixes scored and unscored spawns

- **WHEN** a window contains both spawns with reusable verdicts and spawns without
- **THEN** only the spawns without are sent to the judge, and the run's outcome
  distinguishes the two groups

#### Scenario: A spawn outside the window is left alone

- **WHEN** a spawn's start falls outside the resolved window
- **THEN** it is neither scored nor counted, whether or not it already has a verdict

### Requirement: One spawn's failure does not end the run

A spawn whose scoring fails SHALL be recorded as a failed spawn and the run SHALL
continue with the spawns that remain, rather than the failure ending the invocation.

#### Scenario: A judge failure mid-window

- **WHEN** scoring one spawn in a window fails while the judge remains usable for
  others
- **THEN** that spawn is reported as failed, the spawns after it are still attempted,
  and the run reports both the failure and what it went on to score

#### Scenario: A failed spawn keeps its deterministic facts

- **WHEN** a spawn's scoring fails during a run
- **THEN** its deterministic facts remain recorded, and the failure is reported as a
  scoring failure rather than an ingest failure

#### Scenario: A failed spawn holds no claim

- **WHEN** a spawn's scoring fails during a run
- **THEN** nothing is left holding that spawn's identity, and a later run is free to
  attempt it

Rationale: a batch that leaks a claim per failure makes its own retry impossible.
The next run would report every previously failed spawn as claimed elsewhere until
the leases expired.

#### Scenario: A failed spawn records no verdict

- **WHEN** a spawn's scoring fails during a run
- **THEN** no verdict is recorded for it, and a later run treats it as unscored

### Requirement: A run reports what happened to every spawn it covered

A run's outcome SHALL distinguish spawns that were scored, spawns whose stored
verdict was reused, spawns skipped because another scorer held them, and spawns whose
scoring failed, with a count for each. Every count SHALL be a count of spawns.

#### Scenario: Counts are reported per outcome

- **WHEN** a run covering spawns with mixed results completes
- **THEN** its outcome carries a separate count for scored, reused, skipped, and
  failed spawns, and those counts sum to the number of spawns covered

#### Scenario: Counts are spawns, not sessions

- **WHEN** a run covers a window in which one parent session spawned the same agent
  type several times
- **THEN** each spawn is counted separately, and no count collapses them by session or
  by agent type

Rationale: four `implementer` spawns in one parent session are four rows. A surface
reporting "1 session" for work that cost four judge calls misstates both the coverage
and the spend.

#### Scenario: The run reports its own spend

- **WHEN** a run completes
- **THEN** it reports the dollar cost and token counts of its own judge calls,
  aggregated across the spawns it scored

#### Scenario: A reused spawn adds nothing to the spend

- **WHEN** a run reuses a stored verdict for a spawn
- **THEN** that spawn contributes nothing to the run's reported cost

### Requirement: An unreachable judge is retried within a bounded budget

A spawn whose judge call fails because the judge could not be reached SHALL be
retried up to a bounded number of attempts, and exhausting that budget SHALL be
reported as a failure naming exhaustion rather than retried further.

#### Scenario: A transient failure is retried and succeeds

- **WHEN** a judge call for one spawn fails because the judge did not respond, and a
  further attempt succeeds
- **THEN** that spawn is reported as scored, and exactly one verdict is recorded for it

#### Scenario: The attempt budget is exhausted

- **WHEN** every attempt allowed for one spawn fails to reach the judge
- **THEN** that spawn is reported as failed, the failure states that the attempts were
  exhausted, and no further attempt is made for it

#### Scenario: A rejected verdict is never retried

- **WHEN** the judge answers for a spawn but the returned verdict is rejected as
  unusable
- **THEN** no further attempt is made for that spawn, it is reported as failed, and
  the cost already spent is still reported

Rationale: a verdict that fails local validation is a bug in the rubric, the prompt,
or the model's conformance, and none of those get better on a second try. Retrying it
spends money to report a bug as a flake, and the standards this project inherits
forbid it.

#### Scenario: Retries do not multiply verdicts

- **WHEN** a spawn is scored after one or more failed attempts
- **THEN** exactly one verdict exists for its identity

### Requirement: A run whose judge is unusable stops rather than grinding through the window

A run SHALL bound the number of consecutive spawn failures it tolerates, and on
reaching that bound SHALL stop, report the cause, and name the spawns it did not
attempt.

#### Scenario: An absent judge stops the run early

- **WHEN** a run is requested over a large window and the judge cannot be found
- **THEN** the run stops after its consecutive-failure bound is reached rather than
  attempting every spawn, and reports that the judge could not be found

Rationale: a missing binary or an expired credential fails identically for every
spawn in the window. Attempting four hundred of them produces four hundred identical
failures and one confused reader.

#### Scenario: Scattered failures do not stop the run

- **WHEN** a run's failures are separated by spawns that scored successfully
- **THEN** the consecutive-failure bound is not reached and the run covers the whole
  window

#### Scenario: A stopped run reports what it completed

- **WHEN** a run stops on its consecutive-failure bound partway through a window
- **THEN** the verdicts already recorded remain recorded, the run reports the counts
  it accumulated before stopping, and it reports how many spawns it did not attempt

### Requirement: A run's judge spend is bounded

A run SHALL accrue the cost of its completed judge calls against a configured
ceiling, SHALL not start a further call once accrued spend has reached that ceiling,
and SHALL report the ceiling as the reason it stopped.

#### Scenario: A run stops at its ceiling

- **WHEN** a run's accrued judge spend reaches its ceiling while spawns remain
- **THEN** no further judge call is started, the run reports the ceiling as its stop
  reason, and it reports how many spawns it did not attempt

#### Scenario: A stopped run's work is kept

- **WHEN** a run stops at its ceiling
- **THEN** every verdict recorded before the stop remains recorded, and a later run
  reuses them rather than paying again

#### Scenario: The ceiling can be exceeded by at most one call

- **WHEN** a run's final call costs more than the ceiling had remaining
- **THEN** the run's reported spend may exceed the ceiling by no more than one call's
  own spend bound, and the reported figure is what was actually spent

Rationale: a call's cost is only knowable from its response, so a ceiling checked
between calls cannot be a guarantee. Reporting the real figure and naming the bound's
looseness is honest; reporting a capped number would not be.

#### Scenario: Reused spawns are not charged against the ceiling

- **WHEN** a run reuses stored verdicts for many spawns
- **THEN** none of that reuse counts against the ceiling, and the run continues to the
  spawns that need scoring

### Requirement: A dry run reports its scope and a cost bound without calling the judge

A dry run SHALL report how many spawns it would score, SHALL report an upper bound on
what scoring them could cost, SHALL construct no judge invocation, and SHALL write
neither a verdict nor a claim.

#### Scenario: A dry run reports what it would score

- **WHEN** a dry run is requested over a window containing spawns without reusable
  verdicts
- **THEN** it reports the count of spawns it would score and separately the count it
  would reuse, and no judge process is started

#### Scenario: The cost figure is a bound, not a prediction

- **WHEN** a dry run reports cost
- **THEN** the figure is presented as an upper bound derived from the run's spend
  bounds, and it is not presented as an estimate of what the run would actually cost

Rationale: cost is only observable in a response envelope, so no figure available
before a call is a prediction. A bound that is stated as a bound is useful; a guess
presented as a number invites a reader to budget against it.

#### Scenario: A dry run leaves the store untouched

- **WHEN** a dry run covers a window
- **THEN** no verdict and no claim is written, and the diagnostic stream names the
  writes that were skipped
