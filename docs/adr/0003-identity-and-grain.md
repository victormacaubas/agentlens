# 0003. Identity and grain

## Status

Accepted

## Context

Every downstream aggregate depends on getting the grain right, and identity
changes are the most expensive kind of breaking change: they cascade into the
schema, every query, every fixture, and every test. The design doc settled most
of this during the prior build, and the reasoning is worth preserving because it
was learned the hard way.

The forcing constraint: **raw Claude Code IDs are not globally unique.** The same
string can appear as a main-session ID, as a subagent ID, and inside more than
one project bucket under `.claude/projects/`. Keying on the raw ID collides
across projects, and the collision is silent: two unrelated runs merge into one
row.

The remaining ambiguity was in `fact_tool_event`, which the design doc described
as "one row per tool_use / tool_result." That reads either as one row per
invocation or as two.

## Decision

**`fact_session`, the primary grain: one row per spawn.**

One row is one agent run, not one agent type. Four `implementer` spawns inside a
single parent session are four rows, each with its own `agent_id`. Deduping by
`agent_type` is never correct; the spawn is the unit. This is why report counts
are labelled spawns rather than sessions.

**Natural key: a qualified, derived `session_id`.**

`session_id = SHA-256(source_project, session_kind, raw_session_id)`, with all
three components retained as columns for display and unambiguous lookup. The raw
ID alone is explicitly rejected as a key for the collision reason above.
Qualified `parent_session_id` is derived with the same project and the `main`
kind, so lineage cannot accidentally cross projects.

**`fact_tool_event`, finest grain: one row per tool invocation.**

The `tool_use` and its matching `tool_result` join into a single row keyed
`(session_id, seq)`. Fields from the use (`tool_name`, `input_hash`,
`file_path_hash`, `ts`) and from the result (`is_error`, `denial_kind`,
`output_bytes`) live side by side. `n_tool_calls` is therefore `COUNT(*)` with
no filter, and error rate needs no self-join.

An **unmatched `tool_use`**, a call the session never got a result for because
it was interrupted or abandoned, becomes a row with null result fields. That is
a health signal about the run, not a parse failure, and it must not be dropped.

Rejected: one row per JSONL record, paired by `tool_use_id`. Closer to the source
but every aggregate then needs a `WHERE kind = ...` filter, and forgetting one is
a silently wrong number.

**`fact_verdict`, one row per scored identity.**

Key: `session_id + judge_input_hash + rubric_version + judge_model`, where
`judge_model` is the concrete identifier resolved from the response envelope,
never the alias typed at the CLI. `judge_input_hash` is the SHA-256 of the exact
prepared transcript view.

**Re-run behavior: upsert, never duplicate.**

Re-running upserts by qualified `session_id`. A grain is replaced only by a
*sound, non-stale* snapshot, judged against a source revision of
`(mtime_ns, size, content_hash)` captured before and verified after the read. A
malformed, incomplete, changed-during-read, or stale snapshot cannot overwrite a
newer sound grain. Verdicts are cache hits unless prepared input, rubric, or
resolved model changed, and finalization rechecks the session's current input
hash so an in-flight score cannot attach itself to a newer ingest.

**Assumptions a future reader should check, because they are the ones that break:**

- A `(source_project, session_kind, raw_session_id)` tuple identifies exactly one
  run. If Claude Code ever reuses a raw ID within one project and kind, this key
  collides and the fix is breaking.
- `seq` is stable for a given source file. If the parser's ordering ever changes,
  every `fact_tool_event` key changes with it, which is survivable only because
  the store is a rebuildable cache.

## Consequences

- **The store is a disposable cache, and that is load-bearing.** Because the key
  is derived and the source is the real truth, schema changes are handled by
  rebuild-from-source rather than in-place migration. This is what makes the
  no-ORM choice in ADR 0002 affordable. It also means the store must never
  accumulate anything that cannot be regenerated from `.claude/`, including
  verdicts, which is why judge caching is keyed on inputs rather than trusted as
  durable state.
- **A derived key is not human-readable.** Nobody can eyeball a `session_id`, so
  every display path and every error message has to carry the raw tuple as well.
  That is the cost of qualification and it is worth paying.
- **Joining use to result at parse time moves work earlier.** The parser has to
  buffer or index unmatched uses while streaming, rather than emitting rows
  blindly. In exchange every query downstream gets simpler.
- **The spawn grain makes N confusing unless labelled.** "12 runs this week" may
  be 3 sessions with 4 spawns each. Every surface that shows a count has to say
  spawns, or trends and the low-volume guard get misread.
