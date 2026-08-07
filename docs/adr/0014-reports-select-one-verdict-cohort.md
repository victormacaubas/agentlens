# ADR 0014: Reports select one explicit verdict cohort

## Status

Accepted

## Context

The store permits several verdicts for one spawn: prepared input can change, rubric
versions can change, and different concrete models can score the same input. ADR 0010 says
different rubric or model identities are not comparable.

A report join on `session_id` alone returns every matching verdict. One spawn then
contributes several scores to an agent average, while a dictionary keyed by session keeps
whichever payload SQLite happened to return last. The headline average can disagree with
the displayed verdict, and model or rubric drift appears as agent behavior.

The deterministic report slice also needs one row per spawn. Aggregates alone cannot show
which same-type spawn was unscored or carry its task, raw identity, and measured counts.

## Decision

A report that includes modeled output selects one explicit cohort consisting of rubric
version and concrete judge model. It joins verdicts on qualified session ID, the session's
current `judge_input_hash`, rubric version, and concrete model. Each spawn contributes at
most one verdict and one score to aggregates.

If the current-input rows contain one concrete model, report construction can resolve it
deterministically. If several models exist and the caller supplied none, the report fails
with an actionable ambiguity instead of choosing by insertion order. Floating aliases are
not report cohort identities.

The report result includes the selected cohort and one typed deterministic row for every
qualified spawn in the window. Scored rows attach their selected verdict; unscored rows
remain present without modeled fields. Agent and parent rollups derive from those rows and
retain the `n_spawns_with_errors` name.

## Consequences

- Agent averages and displayed payloads reconcile because each scored spawn contributes
  once.
- Reversing verdict insertion order cannot change report output.
- Users comparing a non-default concrete model must select it when a window contains
  several model cohorts.
- Historical verdicts for older input remain queryable but do not appear as the current
  session's score.
- JSON grows to include per-spawn deterministic rows. Existing top-level verdict mapping
  can remain as a compatibility view, but it must derive from the same selected cohort.
- Side-by-side model or rubric comparison is a separate future report mode. It must produce
  one aggregate per cohort rather than combine them.
