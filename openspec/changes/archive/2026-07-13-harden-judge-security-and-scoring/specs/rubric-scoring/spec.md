## MODIFIED Requirements

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
