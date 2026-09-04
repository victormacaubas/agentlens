## Why

The judge can now score a whole window (#28), but nothing shows those verdicts to
a reader: `report` is deterministic-only by contract, so a corpus can be scored and
the report stays silent about it. Surfacing the scores is the last step between
Phase 3 and a report worth reading.

It has to be done without producing a number that looks meaningful and is not.
Averaging across rubric versions or across concrete judge models is exactly that
kind of number, and the ground makes it easy to produce by accident:
`judge/prompt.py::render_prompt` renders the narrative alone and carries no rubric
text, so bumping `RUBRIC_VERSION` does **not** change `judge_input_hash`. One spawn
at one input hash can therefore hold a `v1` verdict and a `v2` verdict at the same
time, both joinable, indefinitely. Cohort ambiguity is the ordinary case the first
time the rubric is bumped, not a corner case.

## What Changes

- The windowed report presents modeled scores beside the deterministic signals,
  restricted to exactly one cohort — one rubric version and one concrete judge
  model.
- A new `--cohort <rubric_version>/<judge_model>` selector on `report`. A sole
  cohort in the window is selected automatically; more than one without a selector
  fails as a `ConfigError` naming the cohorts present and their coverage; a window
  with no verdicts names no cohort and is not an error.
- Verdicts join to spawns on the spawn's current input hash. Each spawn is
  `scored`, `unscored`, or `stale` (a verdict in the cohort under a superseded input
  hash). Only `scored` reaches an average.
- Modeled agent rollups carry their own population — scored spawns, not all
  qualifying spawns — and the same low-volume trend guard at the same threshold, so
  a modeled trend and a deterministic trend can legitimately disagree.
- Evidence and suggested fixes appear per spawn in the JSON document behind the
  existing `VerdictProvenance` untrusted marker. The terminal summary renders no
  untrusted text.
- **BREAKING** `REPORT_SCHEMA_VERSION` goes 1 to 2. Existing keys keep their
  meaning; the change is additive, but a consumer pinning version 1 sees a new
  version.
- **BREAKING** The import contract "The deterministic report path never reaches the
  judge" narrows its forbidden target from all of `agentlens.judge` to the invoking
  surface (`judge.cli_backend`, `judge.invocation`). The report path gains
  `judge.prompt` and `judge.rubric`; it still never constructs or calls a backend.

## Capabilities

### New Capabilities
- `report-modeled-scores`: the modeled surface of a windowed report — cohort
  identity and selection, cohort ambiguity as a configuration failure, the
  verdict-to-spawn join on current input hash, per-spawn modeled state, modeled
  agent rollups and their trend guard, and the untrusted-content boundary for
  evidence and fixes.

### Modified Capabilities
- `report-output`: "Output is deterministic-only" is superseded. The document gains
  a cohort block, a per-spawn modeled block, and a modeled rollup, and its schema
  version advances.
- `report-command`: "Deterministic report never invokes the judge" keeps the
  "never invokes" half and drops the wording that forbids reading verdict data at
  all. The command gains `--cohort` and a `ConfigError` exit for an ambiguous or
  absent cohort.
- `store-schema`: the requirement that window and rollup reads happen "without
  joining modeled verdict data" stays true of the deterministic reads and must say
  so precisely, now that a sibling window-scoped verdict read exists.

`report-aggregation` is deliberately absent: its deterministic requirements are
unaffected, and the modeled rollup's own gating requirement belongs to the new
capability.

## Impact

**Code**

- `store/verdicts.py`: one new window-scoped read, `read_verdicts_for_sessions`,
  mirroring the existing `read_skill_signals_for_sessions` batching precedent.
  `store/reporting.py` is not touched, so its rule that verdict data is never
  joined there holds literally.
- `core/report.py`: joins the deterministic and modeled result sets in memory,
  resolves the cohort, and computes the modeled rollups. The modeled aggregation
  cannot be SQL, because its join key needs `judge.prompt.render_prompt` and
  `store` cannot import `judge`.
- `core/ingest_run.py`: `PreparedIngestBatch` gains the source-bundle map it
  currently discards.
- `core/window_scoring.py`: `_WindowWorklistBuilder` stops repeating
  discover-and-parse and consumes the batch instead. This edits code #28 just
  shipped, deliberately, because the alternative is a third copy of the same
  discovery.
- `core/spawn_scoring.py`: the existing verdict lookup is parametrized by rubric
  version instead of reading the `RUBRIC_VERSION` constant, so a report can name an
  older cohort.
- `cli/report.py`: the `--cohort` option and its parse.
- `models/report_document.py`, `models/report_aggregates.py`: the new document and
  rollup types, and `REPORT_SCHEMA_VERSION`.
- `render/document.py`: report spawn rows reuse `_build_verdict_row`, so one
  untrusted boundary exists rather than two. `render/summary.py` gains cohort and
  state counts and no untrusted text.

**Contracts**

- `pyproject.toml`: the amended import contract, asserted failing before it is
  trusted to pass.

**Dependencies**

None. No runtime dependency is added; `--format json` stays the only
machine-readable format and jinja2 stays unused.

**Not affected**

`report` still spends no money and needs no auth. The composition root never hands
`core.report` a `JudgeBackend`, and `core.ingest_run` stays inside the import
contract's `source_modules`. Analyzed token usage stays a quality signal; the only
dollar figure is agentlens's own judge spend.

**Out of scope**

The designed HTML report (Phase 5). Cross-spawn findings aggregation — which fix
recurs across a window — which is its own capability. Rubric weights: there are
none, since `overall_score` comes back from the judge directly as a sibling of the
dimension scores, so there is nothing to calibrate.
