## MODIFIED Requirements

### Requirement: Verdict dataclass

The system SHALL define a `Verdict` frozen dataclass with fields: `session_id`, `rubric_version`, `judge_model`, `dimensions` (a dict mapping dimension name to a `DimensionScore` with `score: int` 0-5 and `evidence: list[str]`), `overall_score` (float, mean of dimensions), `suggested_fixes` (a list of typed `SuggestedFix` records, not free-form strings), `judge_cost_usd` (float), `judge_input_tokens` (int), `judge_output_tokens` (int).

A `SuggestedFix` SHALL be a frozen dataclass with: `dimension` (one of the four rubric dimension names), `target` (a value from a closed set naming what the fix applies to — the agent definition's instructions, its declared tools, its declared skills, or the caller's task phrasing), `recommendation` (a bounded-length natural-language description of the change), and `rationale` (why the change is warranted, grounded in what happened during the run).

A fix whose `dimension` is not a known rubric dimension, or whose `target` is outside the closed set, SHALL be rejected with a `JudgeError` and no verdict SHALL be persisted.

#### Scenario: Verdict is serializable to JSON

- **WHEN** a Verdict is created with valid fields
- **THEN** calling `json.dumps` on its `to_verdict_json()` method produces valid JSON matching the `fact_verdict.verdict_json` column schema

#### Scenario: Overall score is mean of dimensions

- **WHEN** dimensions are task_completion=4, honesty=5, efficiency=3, scope_adherence=4
- **THEN** overall_score is 4.0

#### Scenario: Typed fix is accepted

- **WHEN** a judge response supplies a fix with dimension `honesty`, target `agent_instructions`, a recommendation, and a rationale
- **THEN** the verdict carries it as a `SuggestedFix` record with those four fields

#### Scenario: Unknown fix dimension is rejected

- **WHEN** a judge response supplies a fix whose `dimension` is not one of the four rubric dimensions
- **THEN** the backend raises a `JudgeError` and no verdict is persisted

#### Scenario: Out-of-set fix target is rejected

- **WHEN** a judge response supplies a fix whose `target` names something outside the closed set, such as an arbitrary file path
- **THEN** the backend raises a `JudgeError` and no verdict is persisted

#### Scenario: Free-form string fix is rejected

- **WHEN** a judge response supplies `suggested_fixes` as a list of bare strings
- **THEN** the backend raises a `JudgeError` rather than accepting untyped prose

## ADDED Requirements

### Requirement: Provenance labelling of model-authored fields

The system SHALL mark, within the serialized verdict payload, which fields are model-authored text derived from untrusted transcript input and which are locally derived or validated. Dimension scores and `overall_score` SHALL be identifiable as locally derived and validated; dimension `evidence`, and each fix's `recommendation` and `rationale`, SHALL be identifiable as untrusted model output.

Provenance SHALL be carried in the payload itself rather than left as a convention for consumers to reimplement, so that every renderer, JSON consumer, and downstream tool reads the same signal.

Consumers rendering a verdict for human or machine handoff SHALL present model-authored fields as content to be reviewed, never as instructions to be executed, and SHALL NOT emit any artifact designed to be applied without human reading.

#### Scenario: Payload distinguishes derived from model-authored fields

- **WHEN** a verdict is serialized via `to_verdict_json()`
- **THEN** the payload identifies dimension scores and the overall score as locally derived, and evidence, recommendations, and rationales as untrusted model output

#### Scenario: Renderers mark untrusted content

- **WHEN** a renderer emits a verdict's fixes and evidence into a report intended as a handoff
- **THEN** that content is presented within an explicitly marked untrusted block, and the report contains no patch, diff, or command designed for direct application
