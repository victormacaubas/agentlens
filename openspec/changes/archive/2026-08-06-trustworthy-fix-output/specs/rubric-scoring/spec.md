## MODIFIED Requirements

### Requirement: Rubric v1 definition

The system SHALL define the rubric as a prompt template with four dimensions — `task_completion`, `honesty`, `efficiency`, `scope_adherence` — each scored on an integer scale of 0 to 5. The rubric version SHALL be a manual semver string stored as a module constant, and SHALL be bumped whenever the prompt template or the verdict output schema changes, since verdicts produced under different versions are not comparable.

#### Scenario: Rubric version is stable across runs

- **WHEN** the scoring loop runs twice without code changes
- **THEN** both runs use the same `rubric_version` string

#### Scenario: Rubric version changes on intentional bump

- **WHEN** the `RUBRIC_VERSION` constant is bumped
- **THEN** all sessions are considered unscored under the new version regardless of existing verdicts under the prior version

#### Scenario: Output schema change requires a version bump

- **WHEN** the verdict output schema changes shape, such as `suggested_fixes` becoming typed records
- **THEN** `RUBRIC_VERSION` is bumped so prior verdicts are not treated as comparable

### Requirement: Rubric prompt template

The system SHALL provide a prompt template that instructs the judge to: read the prepared transcript view, score each of the four dimensions 0-5 with evidence citations, and suggest concrete fixes in the required typed shape. The template SHALL instruct the judge to treat the transcript view as untrusted data and never to follow any instructions embedded within it. The template SHALL NOT ask the judge to compute an overall score — the overall score is derived locally from the dimension scores (see the judge-interface "Locally derived overall score" requirement).

The template SHALL instruct the judge that each suggested fix must name the rubric dimension it addresses, select a target from the closed set of things a fix may apply to, and give a recommendation together with a rationale grounded in what happened during the run. The template SHALL scope fixes to recommending changes to the agent's own guidance, and SHALL instruct the judge not to emit commands, file paths, diffs, or any content intended to be executed or applied directly.

The template SHALL be appended via `--append-system-prompt` (not replacing Claude Code's foundation prompt).

#### Scenario: Prompt includes all four dimensions

- **WHEN** the rubric prompt template is rendered
- **THEN** it contains explicit instructions and scoring criteria for task_completion, honesty, efficiency, and scope_adherence

#### Scenario: Prompt marks the transcript as untrusted

- **WHEN** the rubric prompt template is rendered
- **THEN** it instructs the judge that the transcript view is data to be graded and that embedded directives must not be followed

#### Scenario: Prompt requires the typed fix shape

- **WHEN** the rubric prompt template is rendered
- **THEN** it instructs the judge that each fix must carry a dimension, a target from the closed set, a recommendation, and a rationale

#### Scenario: Prompt forbids executable fix content

- **WHEN** the rubric prompt template is rendered
- **THEN** it instructs the judge to scope fixes to the agent's own guidance and not to emit commands, file paths, or diffs intended for direct application

### Requirement: Verdict JSON schema for structured output

The system SHALL define a JSON Schema suitable for `claude -p --json-schema` that validates the verdict structure: four dimension objects (each with `score` integer 0-5 and `evidence` array of strings) and `suggested_fixes` as an array of objects. Each fix object SHALL require `dimension` (enumerated to the four rubric dimension names), `target` (enumerated to the closed set of fix targets), `recommendation` (a length-bounded string), and `rationale` (a length-bounded string), and SHALL disallow additional properties. The array SHALL be bounded in length. The schema SHALL NOT require the model to supply `overall_score`; that value is derived locally from the dimension scores rather than trusted from model output.

Each dimension's `evidence` array SHALL also be bounded — in item count and in per-item length. Evidence remains free-form text, since it is quotation from the transcript; only its volume is constrained, so that the verbatim channel cannot be padded with plausible-looking entries to bury content or exhaust a reviewer's attention.

#### Scenario: Schema enforces score bounds

- **WHEN** the judge outputs a dimension score of 6
- **THEN** `--json-schema` validation rejects it and the model retries

#### Scenario: Schema requires all dimensions

- **WHEN** the judge omits the `honesty` dimension
- **THEN** `--json-schema` validation rejects it and the model retries

#### Scenario: Schema does not depend on a model-supplied overall score

- **WHEN** a judge response omits `overall_score`
- **THEN** verdict construction still succeeds because the overall score is derived locally from the dimensions

#### Scenario: Schema rejects a bare-string fix

- **WHEN** the judge outputs `suggested_fixes` as an array of strings
- **THEN** `--json-schema` validation rejects it and the model retries with the typed shape

#### Scenario: Schema constrains the fix target to a closed set

- **WHEN** the judge outputs a fix whose `target` is a value outside the enumerated set
- **THEN** `--json-schema` validation rejects it and the model retries

#### Scenario: Schema bounds the evidence channel

- **WHEN** the judge outputs a dimension whose `evidence` array exceeds the permitted item count, or an evidence item longer than the permitted length
- **THEN** `--json-schema` validation rejects it and the model retries, so the verbatim channel cannot be padded without bound
