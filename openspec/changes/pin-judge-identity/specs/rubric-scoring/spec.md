## MODIFIED Requirements

### Requirement: Scoring loop

The system SHALL implement a scoring loop that: resolves a window of sessions, queries the store for sessions lacking a verdict for the current `(rubric_version, judge_model)`, builds a prepared transcript view for each unscored session, calls the judge, and persists the resulting verdict to `fact_verdict`. The loop SHALL be idempotent — re-runs score only genuinely new/unscored sessions.

The `judge_model` component of verdict identity SHALL be the concrete model identifier resolved by the judge backend, never the alias supplied at the CLI. The loop SHALL NOT overwrite the backend's resolved `judge_model` with its configured value; it sets only `session_id` and `rubric_version`, which are the loop's own facts. Consequently, when a floating alias advances to a new underlying model, sessions previously scored under the prior model SHALL be reported as unscored and re-scored, rather than colliding with the existing verdicts on the same key.

#### Scenario: Only unscored sessions are judged

- **WHEN** the loop runs on a window of 20 sessions where 15 already have verdicts
- **THEN** the judge is called exactly 5 times (for the unscored sessions)

#### Scenario: Re-run after full scoring is free

- **WHEN** all sessions in a window already have verdicts for the current rubric and resolved model
- **THEN** the loop makes zero judge calls and reports "all sessions already scored"

#### Scenario: Persisted verdicts survive failures

- **WHEN** the loop scores 8 of 12 sessions and then encounters a systemic failure
- **THEN** the 8 persisted verdicts remain in the store and a re-run starts from session 9

#### Scenario: Backend-resolved model is preserved

- **WHEN** the judge backend returns a verdict whose `judge_model` is a concrete identifier and the loop was configured with an alias
- **THEN** the persisted verdict carries the concrete identifier, not the alias

#### Scenario: Alias movement invalidates prior verdicts

- **WHEN** sessions were scored while an alias resolved to one concrete model, and the alias later resolves to a different concrete model
- **THEN** those sessions are reported as unscored under the new model and are re-scored, and the earlier verdicts remain in the store as separate rows

## ADDED Requirements

### Requirement: Verdict comparability

The system SHALL treat two verdicts as comparable only when they share a rubric version, a concrete judge model identifier, and a judge system context. Rubric version and concrete model identifier SHALL be enforced through the `fact_verdict` primary key. The judge system context SHALL be held constant structurally — by invoking the judge in minimal mode with pinned setting sources — so that it need not be a key column; any future change that makes the judge context configurable SHALL promote it to part of the verdict identity.

#### Scenario: Same rubric and model are comparable

- **WHEN** two verdicts share a rubric version and a concrete model identifier
- **THEN** their scores are treated as comparable for windowed reporting and prior-window deltas

#### Scenario: Differing concrete model is not silently comparable

- **WHEN** two verdicts share a rubric version but were produced by different concrete models
- **THEN** they occupy separate rows in `fact_verdict` and are not conflated
