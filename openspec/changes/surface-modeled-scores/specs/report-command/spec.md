## ADDED Requirements

### Requirement: Reports can select a verdict cohort

The report command SHALL accept an optional cohort selector that names one
rubric version and one concrete judge model in a single value, and SHALL treat
the rubric version as the portion preceding the first separator so that a judge
model containing the separator still parses. A malformed selector SHALL be
rejected with the configuration-error exit code.

#### Scenario: Cohort selector is supplied
- **WHEN** the caller names a rubric version and a concrete judge model
- **THEN** the report presents modeled values from that cohort only

#### Scenario: Judge model contains the separator
- **WHEN** the named concrete judge model itself contains the separator
  character
- **THEN** the rubric version is taken from the portion before the first
  separator and the remainder is taken as the judge model

#### Scenario: Selector is malformed
- **WHEN** the caller supplies a cohort selector with no separator, or with an
  empty rubric version or judge model
- **THEN** the command fails with the configuration-error exit code and writes
  no store or report artifact

### Requirement: The report reads verdicts and never invokes the judge

The report command SHALL read stored verdicts in order to present modeled scores
and SHALL NOT construct or call a judge backend. Producing a report SHALL
therefore incur no judge spend and SHALL require no judge credentials, whether
or not the window contains scored spawns.

#### Scenario: Report covers scored spawns
- **WHEN** the selected window contains spawns with verdicts the report presents
- **THEN** the report succeeds without invoking a judge, and agentlens's own
  judge spend for that cohort is reported as already-incurred cost rather than
  cost incurred by this run

#### Scenario: Report contains unscored spawns
- **WHEN** the selected window contains subagent spawns with no verdicts
- **THEN** the report succeeds with deterministic facts and no fabricated score,
  verdict, or fix fields, without invoking a judge to fill the gap

#### Scenario: No judge is installed or authenticated
- **WHEN** the report runs on a machine where the judge is missing or
  unauthenticated
- **THEN** the report still succeeds, because no code path it takes reaches a
  judge backend

### Requirement: Dry run presents modeled scores without writing

Under `--dryrun` the command SHALL present the same modeled values it would
present in a normal run, and SHALL write neither the store nor a report
artifact. Reading verdicts SHALL NOT be treated as a write.

#### Scenario: Dry run covers scored spawns
- **WHEN** `--dryrun` runs over a window containing scored spawns
- **THEN** the computed report contains the same cohort, modeled scores, and
  modeled rollups as a normal run over the same window, and the store and
  artifact are unchanged

## REMOVED Requirements

### Requirement: Deterministic report never invokes the judge

**Reason**: Superseded by this change. The requirement conflated two things: that
the report never calls a judge, which still holds and is now carried by "The
report reads verdicts and never invokes the judge", and that the report produces
no modeled output, which this change reverses. Keeping the original wording would
forbid reading a stored verdict, which is not the invariant it existed to
protect.

**Migration**: None for callers. The report's cost and authentication behavior is
unchanged: it still spends nothing and needs no judge credentials. Callers that
depended on the output containing no modeled fields are covered by the migration
note on `report-output`'s removed "Output is deterministic-only".
