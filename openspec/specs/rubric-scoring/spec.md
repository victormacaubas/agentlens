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

The system SHALL provide a prompt template that instructs the judge to: read the prepared transcript view, score each of the four dimensions 0-5 with evidence citations, compute an overall score as the mean of dimensions, and suggest concrete fixes. The template SHALL be appended via `--append-system-prompt` (not replacing Claude Code's foundation prompt).

#### Scenario: Prompt includes all four dimensions

- **WHEN** the rubric prompt template is rendered
- **THEN** it contains explicit instructions and scoring criteria for task_completion, honesty, efficiency, and scope_adherence

### Requirement: Verdict JSON schema for structured output

The system SHALL define a JSON Schema suitable for `claude -p --json-schema` that validates the verdict structure: four dimension objects (each with `score` integer 0-5 and `evidence` array of strings), `overall_score` (number), and `suggested_fixes` (array of strings).

#### Scenario: Schema enforces score bounds

- **WHEN** the judge outputs a dimension score of 6
- **THEN** `--json-schema` validation rejects it and the model retries

#### Scenario: Schema requires all dimensions

- **WHEN** the judge omits the `honesty` dimension
- **THEN** `--json-schema` validation rejects it and the model retries

### Requirement: Scoring loop

The system SHALL implement a scoring loop that: resolves a window of sessions, queries the store for sessions lacking a verdict for the current `(rubric_version, judge_model)`, builds a prepared transcript view for each unscored session, calls the judge, and persists the resulting verdict to `fact_verdict`. The loop SHALL be idempotent — re-runs score only genuinely new/unscored sessions.

#### Scenario: Only unscored sessions are judged

- **WHEN** the loop runs on a window of 20 sessions where 15 already have verdicts
- **THEN** the judge is called exactly 5 times (for the unscored sessions)

#### Scenario: Re-run after full scoring is free

- **WHEN** all sessions in a window already have verdicts for the current rubric and model
- **THEN** the loop makes zero judge calls and reports "all sessions already scored"

#### Scenario: Persisted verdicts survive failures

- **WHEN** the loop scores 8 of 12 sessions and then encounters a systemic failure
- **THEN** the 8 persisted verdicts remain in the store and a re-run starts from session 9

### Requirement: Per-session failure handling

The system SHALL skip a session on judge failure (timeout, malformed output, envelope `is_error`) and continue the loop. Skipped sessions SHALL NOT have a verdict row written. The loop SHALL abort if 3 consecutive sessions fail (indicating a systemic issue).

#### Scenario: Single failure skipped

- **WHEN** session 5 of 12 times out
- **THEN** the loop logs a warning, skips session 5, and continues with session 6

#### Scenario: Three consecutive failures abort

- **WHEN** sessions 5, 6, and 7 all fail consecutively
- **THEN** the loop aborts immediately, reports what was scored, and exits with non-zero status

### Requirement: Verdict persistence

The system SHALL upsert verdicts into `fact_verdict` with the composite key `(session_id, rubric_version, judge_model)`. The `verdict_json` column SHALL contain the full verdict object. Judge cost fields (`judge_cost_usd`, `judge_input_tokens`, `judge_output_tokens`) SHALL be populated from the `claude -p` envelope.

#### Scenario: Verdict upsert is idempotent

- **WHEN** the same session is scored twice with the same rubric version and model
- **THEN** the second write overwrites the first (no duplicate rows)

#### Scenario: Different models coexist

- **WHEN** a session is scored with both `sonnet` and `opus`
- **THEN** both verdicts exist in the store as separate rows
