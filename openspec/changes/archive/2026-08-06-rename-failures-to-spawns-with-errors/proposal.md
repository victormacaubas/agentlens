## Why

`report` prints lines like `implementer: 56 spawns, 29 failures`. The number is computed as `SUM(CASE WHEN n_errors > 0 OR final_report_flagged_partial = 1 THEN 1 ELSE 0 END)` — it counts spawns that hit at least one tool error or self-reported partial work. A spawn with one recoverable `Grep` miss among forty clean calls is counted identically to one that genuinely stalled.

"Failures" reads as *the invocation failed*. So the headline number on the tool's primary output overstates: 29 of 56 `implementer` spawns look broken when most of them completed their work and logged a recoverable error along the way. In a tool whose whole claim is telling you whether an agent got better or worse, a metric that reads 5x worse than reality is not a wording preference — it teaches the reader to distrust the report.

The confusion compounds because `n_errors` already exists as a separate field holding the raw error-event total. Two adjacent numbers, `n_failures` and `n_errors`, mean different things at different grains, and neither name says which.

## What Changes

- `AgentAggregate.n_failures` and `ParentLensRow.n_failures` are renamed to `n_spawns_with_errors`, naming the grain (spawns) and the condition (they had errors) explicitly. The computation is unchanged.
- The terminal renderer prints `29 had errors` instead of `29 failures`.
- **BREAKING (output):** the `report --json` payload key `n_failures` becomes `n_spawns_with_errors`, in both the `agents` and `parent_lens` arrays, and in the `delta` map's keys. Any consumer reading `n_failures` must be updated. No stored data changes; the store has no such column.
- The `windowed-reporting` spec's parent-lens requirement and its scenario are reworded to match, and the aggregate requirement gains an explicit statement of what the metric counts, so the next reader does not have to derive it from SQL.

Deliberately **not** changed: the metric stays a single number rather than splitting tool-errors from self-reported-partial. Those are genuinely different signals and splitting them is worth doing, but it is a substantive reporting change rather than a rename, so it belongs in its own proposal. This change makes the existing number honest about what it is.

## Capabilities

### New Capabilities
<!-- none -->

### Modified Capabilities
- `windowed-reporting`: the parent-lens and aggregate requirements name the metric `n_spawns_with_errors` and state its definition (spawns with at least one tool error or a self-reported partial completion), rather than calling it a failure count.

## Impact

- Code: `src/agentlens/reporting/queries.py` (both dataclasses, both SQL aliases, the `_DELTA_FIELDS` tuple, the two `to_verdict_json`-side dict literals), `src/agentlens/reporting/rendering.py` (two output lines).
- Specs: `windowed-reporting` delta.
- Tests: `tests/unit/test_reporting.py`.
- Store: no change. `n_failures` was never a column; it is computed at query time from `fact_session.n_errors` and `fact_session.final_report_flagged_partial`.
- Consumers: anything parsing `report --json`. The project has no external consumers today, so the break is contained.
