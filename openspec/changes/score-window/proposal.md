## Why

`report` discovers and ingests a window's worth of subagent transcripts, but nothing
scores what it finds. Scoring is one `session --file` call at a time, so a corpus can
only be scored by hand, one invocation per spawn. Until a window can be scored in one
pass, the aggregate report has nothing modeled to aggregate and the rubric cannot be
calibrated against real spread.

Both halves of the capability already exist and nothing joins them. Window
resolution, bulk discovery, and the windowed spawn read are built and exercised, but
only by `report`, which never touches the judge. The scoring lifecycle, with reuse
and claim coordination, is built but only by `session`, one file at a time. This
change writes the run that reads a window and scores what it finds.

## What Changes

- A new `agentlens score` command takes the same window selectors as `report`
  (`--since` / `--window` / `--from`+`--to`, mutually exclusive) and scores every
  spawn in the window that qualifies and has no reusable verdict.
- A scoring run becomes a first-class thing with its own outcome: counts of scored,
  reused, skipped, and failed spawns, the run's own judge spend, and a stop reason.
- A judge failure on one spawn stops sinking the invocation. It is captured as that
  spawn's outcome and the run continues.
- `ScoringStatus.FAILED` becomes reachable. It was added by #27 for this change and
  is currently dead code, along with the branch in `render/summary.py` that presents
  it.
- Bounded per-spawn retries are introduced for a judge that is unreachable, together
  with a consecutive-failure circuit breaker that aborts a run whose judge is
  unusable rather than grinding through the window. A verdict rejected by local
  validation is never retried.
- A run accrues its judge spend against a ceiling and stops before starting another
  call once the ceiling is reached, reporting what was scored and what was left.
- `--dryrun` reports the spawn count that would be scored and an upper bound on what
  it could cost, without calling the judge.
- **BREAKING** for spec readers, not for users: the existing requirement *"Scoring is
  requested explicitly, one spawn at a time"* no longer holds as written, and
  *"An unusable judge fails fast and names the cause"* currently mandates exiting on
  the first judge failure, which is what this change stops doing per spawn.

## Capabilities

### New Capabilities

- `window-scoring`: scoring a window's worth of spawns in one run. Which spawns
  qualify, how the run captures a per-spawn outcome instead of failing, the retry
  budget and the circuit breaker, the run's cost ceiling and stop reasons, and what
  the run's counts mean.
- `score-command`: the `agentlens score` command surface. Window selectors, filters,
  the dry-run bound, the split between the machine-readable outcome on stdout and
  diagnostics on stderr, and the exit code a run with failures produces.

### Modified Capabilities

- `spawn-scoring`: *"Scoring is requested explicitly, one spawn at a time"* becomes
  explicit-request plus a per-spawn invariant, since a request now covers many
  spawns. *"An unusable judge fails fast and names the cause"* moves its stop from
  the first failed call to the run level, so the named causes survive but a single
  spawn's failure no longer ends the invocation.

## Impact

Code:

- `core`: a new run module owning the loop, the worklist, the retry budget, the
  breaker, and the ceiling. It composes the existing `SpawnScoringRun` per spawn
  rather than replacing it.
- `models/scoring.py`: a run outcome type and a stop-reason enum alongside the
  existing `ScoringStatus` and `ScoringOutcome`.
- `cli.py`: the new command, its flags, and its place in the single exit-code map.
- `render`: the run summary and its JSON form.

Deliberately unaffected:

- **No store schema change and no new store query.** The reuse key includes
  `judge_input_hash`, which only exists once a spawn's projection has been rendered,
  so no SQL can filter a window to unscored spawns. The worklist is the existing
  windowed spawn read paired with its source bundles, and the reuse decision stays
  per-spawn in `core` where #27 put it. `store/reporting.py` keeps its rule that
  verdict data is never joined into a report's deterministic figures.
- **No change to the `JudgeBackend` Protocol.** `score` stays one call, and retries
  live above the seam in `core`. A backend that retried internally would make the
  fake able to disagree with the real one about how many calls happened.
- **No new runtime dependency, and no concurrency.** The run is sequential.

Dependencies: #27 (`reuse-verdicts`), complete. Out of scope: how modeled scores
appear in the report document (#4, #29), and scoring main sessions, which needs a
different rubric and is deferred to v2.
