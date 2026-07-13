# 7. `score` is a separate command; `report` never triggers LLM calls

Status: Accepted

## Context

Verdict data could be introduced into the pipeline in two ways:

- **`report` triggers scoring** — when `agentlens report` finds unscored sessions in the window, it calls the judge before rendering.
- **Separate `score` command** — `agentlens score` is the only entry point that calls the judge. `report` reads verdicts from the store opportunistically when they exist.

`report` is currently fast (milliseconds), read-only, and free. Adding LLM calls changes its character: it becomes slow (seconds to minutes), stateful (writes to the store), and costly (dollars for large windows). A user running `report` for a quick check would unexpectedly pay for scoring.

## Decision

**`agentlens score` is the only command that calls the LLM judge and writes to `fact_verdict`.** `agentlens report` reads verdict data opportunistically — sessions with verdicts include scores in the output; sessions without verdicts appear with deterministic data only. `report` never triggers a judge call, even if unscored sessions exist in the window.

The pipeline is explicit: `ingest` → `score` → `report`.

## Consequences

- **`report` stays fast, free, and read-only.** A user can run it at any time without incurring cost or triggering side effects.
- **Scoring is always an explicit opt-in.** The user chooses when to pay for verdicts. `score` shows estimated cost and requires confirmation before starting (overridable with `--no-confirm`).
- **Reports are valid without verdicts.** A newly ingested window can be reported immediately; verdict data enriches subsequent reports.
- **Future CI integration targets `score`, not `report`.** Any automated scoring pipeline should call `agentlens score` on a schedule, not `agentlens report`.
