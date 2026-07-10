# 3. The deterministic layer emits counts and booleans, never verdicts

Status: Accepted

## Context

agentlens keeps measured facts (what happened) separate from modeled judgments (how good it was) — deterministic tables versus `fact_verdict`, never mixed. Phase 2 stress-tested that principle at the column level, because two `fact_session` columns inherited from the Phase 1 DDL quietly encoded judgment while pretending to be facts.

- **`claimed_status` (complete | partial).** The intent was a deterministic read of whether the agent finished. But against the real corpus, *every* subagent transcript ends `stop_reason: end_turn` — including runs that reported partial or blocked work. `stop_reason` carries no completion signal. The only deterministic thing available is keyword-matching the final report text, which is a fragile guess, not a fact. "Did it actually finish (vs. merely claim to)?" is precisely the honesty/task-completion axis the Phase 3 judge exists to score.
- **`n_retry_loops`.** The name promises a detector — "the agent got stuck in a loop." But the corpus shows *zero* consecutive-identical tool calls across all ten subagent transcripts; session-wide duplicate `(tool_name, input_hash)` pairs exist but are rare (0–5) and mostly legitimate (re-Read after Edit). A field called `n_retry_loops` set to a small integer invites the reader to interpret it as a stuckness verdict the data does not support.

Both fields share one failure mode: **a deterministic column that renders a judgment reads as authoritative when it is a guess.** That erodes the whole measured/modeled split — a downstream consumer trusts `claimed_status = partial` as ground truth, when it is a heuristic the judge should have owned and weighed.

## Decision

**The deterministic layer emits counts, identifiers, timestamp-derived values, and raw booleans — never a scored or interpreted verdict. Verdicts belong to the judge (`fact_verdict`).**

Applied to the two offending columns:

- **`n_retry_loops` → `n_duplicate_tool_calls`.** Renamed to name the *measurement*, not an interpretation. Defined precisely: the session-wide count of `(tool_name, input_hash)` occurrences beyond the first, for each distinct pair (not consecutive-only — that would be all-zero on real data). It is a raw count handed to the judge, which decides whether a given count means "stuck."
- **`claimed_status` (complete | partial) → `final_report_flagged_partial` (boolean).** Demoted from a verdict to a raw marker: true iff the final assistant text matches a small, documented marker set (unchecked checkbox, "partial", "blocked", "couldn't"/"could not", "unable to"). It records *a signal was present in the text*, not *the work was incomplete*. The completion verdict is the judge's.

The general rule for future deterministic columns: if naming a column honestly requires a verb of judgment ("stuck", "failed", "incomplete", "good"), it is a verdict — move it to the judge and leave behind only the raw count or boolean the judge reads.

## Consequences

- **The measured/modeled boundary is enforced at the column level, not just the table level.** It is no longer enough to be in a deterministic table; a deterministic column must also be judgment-free. This is the sharper, more testable form of the principle.
- **Phase 3 owns more.** Completion and stuckness judgments move to the judge, which is where the design doc's rubric (task completion, honesty, efficiency) already put them. The deterministic layer feeds the judge clean inputs (`n_duplicate_tool_calls`, `final_report_flagged_partial`) rather than pre-empting its call.
- **Store schema changed with no migration.** The rename and the column-type change ship as DDL edits; the store is a disposable cache under `~/.cache/agentlens/`, re-ingestable from `.claude/` at any time, so no data-migration path exists or is needed. A pre-Phase-2 store must be recreated (delete the cache file or point `--store` at a fresh path).
- **`final_report_flagged_partial` will mislabel sometimes.** Keyword matching yields false positives/negatives. That is acceptable *because it is explicitly a raw marker, not a verdict* — the judge is the authority and can disagree with the marker. The marker set is kept small and documented so its behavior is legible.
- **Complements [0002](0002-fact-session-not-pure-rollup.md).** That ADR governs *where a deterministic fact is read from*; this one governs *what may be a deterministic fact at all*. Together they define the deterministic layer's contract for the phases that follow.
