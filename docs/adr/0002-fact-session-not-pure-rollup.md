# 2. fact_session is not a pure rollup of fact_tool_event

Status: Accepted

## Context

The design doc's data-model section states the intended invariant plainly: *"Everything in `fact_session` is an aggregation of `fact_tool_event` — store this, derive the rest."* The appeal is real — one finest-grain fact table, everything else a deterministic query over it, so re-deriving the session grain never needs the transcript again.

Phase 2 is where that invariant met the data, and it does not hold. `fact_session` carries token usage (`input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_creation_tokens`), a turn count (`n_turns`), and a wall-clock duration (`duration_sec`). None of these are tool-event facts:

- **Token usage lives on assistant records**, in `message.usage`, and it is a *per-turn* figure that must be summed across the transcript's assistant turns. In real logs the cache-read component dominates (a single final turn was observed at 52,696 cache-read tokens against 1 input token). Tool events have no usage field at all.
- **`n_turns`** is a count of assistant records — a turn-grain fact, not a tool-event-grain one.
- **`duration_sec`** is the span between the first and last record timestamps, which includes model thinking time and user gaps that no tool event captures.

The design doc itself makes cache-read percentage a first-class quality signal (unstable context = low cache-read across many runs), so these fields are not optional decoration we could drop to preserve the invariant.

Two ways to keep `fact_session` a pure rollup were considered and rejected:

- **Add token columns to `fact_tool_event`.** This smears a turn-level fact across the tool events of that turn — dishonest at the grain, and it forces an arbitrary allocation rule (which event "owns" the turn's tokens?).
- **Introduce a `fact_turn` grain.** Cleaner in theory, but it is a whole table and ingest path Phase 2 does not otherwise need, added solely to preserve a slogan.

## Decision

**`fact_session` is derived from two sources, not one, and this is intentional.**

- **Event-derived, from `fact_tool_event`:** the tool counts — `n_tool_calls`, `n_reads`, `n_edits`, `n_writes`, `n_bash`, `n_files_touched`, `n_errors`, `n_permission_denials`, `n_duplicate_tool_calls`.
- **Transcript-read, directly from the JSONL:** usage (`input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_creation_tokens`, summed across assistant turns), `n_turns`, and `duration_sec`.

The parser returns the transcript-read fields on `ParsedSession`; the aggregation step joins them with the event-derived counts to build one `SessionRecord` per spawn. Usage is read defensively — a missing `usage` object or field contributes zero and never aborts ingest, because `message.usage` field names are provider-shaped and drift across versions.

The corrected invariant: **tool-derived facts aggregate from `fact_tool_event`; turn-derived facts (usage, turns, duration) are read from the transcript at parse time.** "Store the finest grain, derive the rest" holds *within* the tool-event domain, not across the whole session row.

## Consequences

- **Re-deriving `fact_session` requires the transcript, not just the store.** Since ingest already parses the transcript, this costs nothing at ingest time — but a hypothetical "re-aggregate from `fact_tool_event` alone" path is impossible for the usage/turn/duration fields. That path was never a requirement.
- **Usage robustness is a parser concern.** Because provider usage schemas drift, the defensive-read rule (missing → 0, sum integers only) is load-bearing, not defensive-coding hygiene. A schema change upstream degrades usage figures toward zero rather than crashing ingest.
- **The measured/modeled separation is unaffected.** These are still deterministic facts (what happened), not judgments. This ADR is about *where a deterministic fact is read from*, not about mixing measured and modeled data — that boundary is [0003](0003-deterministic-layer-emits-counts-not-verdicts.md).
- **Downstream reads are unchanged.** Consumers query `fact_session` columns; they neither know nor care that some columns aggregated from events and others were read from the transcript. The two-source derivation is contained entirely within ingest.
