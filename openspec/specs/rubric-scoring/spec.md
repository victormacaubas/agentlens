# Rubric and Scoring

## Purpose

Defines the rubric v1 prompt template, the verdict JSON schema for structured output, the scoring loop that finds unscored sessions and calls the judge, per-session failure handling, and verdict persistence into `fact_verdict`.

## Requirements

### Requirement: Rubric v1 definition

The system SHALL define rubric v1 as a prompt template with four dimensions — `task_completion`, `honesty`, `efficiency`, `scope_adherence` — each scored on an integer scale of 0 to 5. The rubric version SHALL be a manual semver string (starting at `"v1"`) stored as a module constant.

#### Scenario: Rubric version is stable across runs

- **WHEN** the scoring loop runs twice without code changes
- **THEN** both runs use the same `rubric_version` string

#### Scenario: Rubric version changes on intentional bump

- **WHEN** the `RUBRIC_VERSION` constant is changed from `"v1"` to `"v2"`
- **THEN** all sessions are considered unscored under `"v2"` regardless of existing `"v1"` verdicts

### Requirement: Rubric prompt template

The system SHALL provide a prompt template that instructs the judge to: read the prepared transcript view, score each of the four dimensions 0-5 with evidence citations, and suggest concrete fixes. The template SHALL instruct the judge to treat the transcript view as untrusted data and never to follow any instructions embedded within it. The template SHALL NOT ask the judge to compute an overall score — the overall score is derived locally from the dimension scores (see the judge-interface "Locally derived overall score" requirement). The template SHALL be appended via `--append-system-prompt` (not replacing Claude Code's foundation prompt).

#### Scenario: Prompt includes all four dimensions

- **WHEN** the rubric prompt template is rendered
- **THEN** it contains explicit instructions and scoring criteria for task_completion, honesty, efficiency, and scope_adherence

#### Scenario: Prompt marks the transcript as untrusted

- **WHEN** the rubric prompt template is rendered
- **THEN** it instructs the judge that the transcript view is data to be graded and that embedded directives must not be followed

### Requirement: Verdict JSON schema for structured output

The system SHALL define a JSON Schema suitable for `claude -p --json-schema` that validates the verdict structure: four dimension objects (each with `score` integer 0-5 and `evidence` array of strings) and `suggested_fixes` (array of strings). The schema SHALL NOT require the model to supply `overall_score`; that value is derived locally from the dimension scores rather than trusted from model output.

#### Scenario: Schema enforces score bounds

- **WHEN** the judge outputs a dimension score of 6
- **THEN** `--json-schema` validation rejects it and the model retries

#### Scenario: Schema requires all dimensions

- **WHEN** the judge omits the `honesty` dimension
- **THEN** `--json-schema` validation rejects it and the model retries

#### Scenario: Schema does not depend on a model-supplied overall score

- **WHEN** a judge response omits `overall_score`
- **THEN** verdict construction still succeeds because the overall score is derived locally from the dimensions

### Requirement: Scoring loop

The system SHALL implement a scoring loop that: resolves a window of sessions, queries the store for sessions lacking a verdict for the current `(rubric_version, judge_model)`, builds a prepared transcript view for each unscored session, calls the judge, and persists the resulting verdict to `fact_verdict`. The loop SHALL be idempotent — re-runs score only genuinely new/unscored sessions.

The `judge_model` component of verdict identity SHALL be the concrete model identifier resolved by the judge backend, never the alias supplied at the CLI. The loop SHALL NOT overwrite the backend's resolved `judge_model` with its configured value; it sets only `session_id` and `rubric_version`, which are the loop's own facts. Consequently, when a floating alias advances to a new underlying model, sessions previously scored under the prior model SHALL be reported as unscored and re-scored, rather than colliding with the existing verdicts on the same key.

Because only a judge call resolves an alias to a concrete identifier, and nothing in the store maps one to the other, the loop SHALL determine its unscored set in two stages when configured with a value that is not itself a concrete identifier. It SHALL first take the set keyed on the configured value as an upper bound, score one candidate from it to obtain the resolved identifier, and then re-query the remaining candidates keyed on that resolved identifier. A run in which every session is already scored under the resolved identifier therefore costs exactly one judge call, not zero — the price of learning what the alias currently points at.

#### Scenario: Only unscored sessions are judged

- **WHEN** the loop runs on a window of 20 sessions where 15 already have verdicts
- **THEN** the judge is called for the 5 unscored sessions, plus at most one resolution call against an already-scored session when the resolved identifier is not yet known

#### Scenario: Re-run after full scoring is free

- **WHEN** all sessions in a window already have verdicts for the current rubric, and the configured judge model is itself a concrete identifier so no resolution is needed
- **THEN** the loop makes zero judge calls and reports "all sessions already scored"

#### Scenario: Re-run under an alias costs one resolution call

- **WHEN** all sessions in a window already have verdicts for the current rubric and resolved model, but the loop was configured with an alias whose resolved identifier is not yet known
- **THEN** the loop makes a single judge call to resolve the alias, re-queries against the resolved identifier, finds nothing further to score, and reports that all sessions are already scored

#### Scenario: Persisted verdicts survive failures

- **WHEN** the loop scores 8 of 12 sessions and then encounters a systemic failure
- **THEN** the 8 persisted verdicts remain in the store and a re-run starts from session 9

#### Scenario: Backend-resolved model is preserved

- **WHEN** the judge backend returns a verdict whose `judge_model` is a concrete identifier and the loop was configured with an alias
- **THEN** the persisted verdict carries the concrete identifier, not the alias

#### Scenario: Alias movement invalidates prior verdicts

- **WHEN** sessions were scored while an alias resolved to one concrete model, and the alias later resolves to a different concrete model
- **THEN** those sessions are reported as unscored under the new model and are re-scored, and the earlier verdicts remain in the store as separate rows

### Requirement: Per-session failure handling

The system SHALL skip a session on judge failure (timeout, malformed output, envelope `is_error`) and continue the loop. Expected transcript I/O failures encountered while building the prepared view — the transcript being missing, unreadable, or invalid UTF-8 (`OSError`, `UnicodeError`) — SHALL be normalized into the same per-session skip path with the session identifier recorded, rather than escaping and aborting the loop. Programmer errors SHALL still fail fast. Skipped sessions SHALL NOT have a verdict row written. The loop SHALL abort if 3 consecutive sessions fail (indicating a systemic issue).

#### Scenario: Single failure skipped

- **WHEN** session 5 of 12 times out
- **THEN** the loop logs a warning, skips session 5, and continues with session 6

#### Scenario: Three consecutive failures abort

- **WHEN** sessions 5, 6, and 7 all fail consecutively
- **THEN** the loop aborts immediately, reports what was scored, and exits with non-zero status

#### Scenario: Missing transcript is skipped, not fatal

- **WHEN** a discovered transcript for session 5 of 12 is deleted (or unreadable, or invalid UTF-8) before it is read
- **THEN** session 5 is counted as one skipped session with its identifier in the progress/error event, and the loop continues with session 6

### Requirement: Verdict persistence

The system SHALL upsert verdicts into `fact_verdict` with the composite key `(session_id, rubric_version, judge_model)`. The `verdict_json` column SHALL contain the full verdict object. Judge cost fields (`judge_cost_usd`, `judge_input_tokens`, `judge_output_tokens`) SHALL be populated from the `claude -p` envelope.

#### Scenario: Verdict upsert is idempotent

- **WHEN** the same session is scored twice with the same rubric version and model
- **THEN** the second write overwrites the first (no duplicate rows)

#### Scenario: Different models coexist

- **WHEN** a session is scored with both `sonnet` and `opus`
- **THEN** both verdicts exist in the store as separate rows

### Requirement: Verdict comparability

The system SHALL treat two verdicts as comparable only when they share a rubric version, a concrete judge model identifier, and a judge system context. Rubric version and concrete model identifier SHALL be enforced through the `fact_verdict` primary key. The judge system context SHALL be held constant structurally — by invoking the judge in minimal mode with pinned setting sources — so that it need not be a key column; any future change that makes the judge context configurable SHALL promote it to part of the verdict identity.

#### Scenario: Same rubric and model are comparable

- **WHEN** two verdicts share a rubric version and a concrete model identifier
- **THEN** their scores are treated as comparable for windowed reporting and prior-window deltas

#### Scenario: Differing concrete model is not silently comparable

- **WHEN** two verdicts share a rubric version but were produced by different concrete models
- **THEN** they occupy separate rows in `fact_verdict` and are not conflated
