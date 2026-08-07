## MODIFIED Requirements

### Requirement: Prepared transcript view

The system SHALL build a structured view with exactly six sections: Task, Agent
Identity, Deterministic Facts, Tool Sequence, Errors & Denials, and Final Report.
It SHALL process the source incrementally with memory bounded by retained summaries and
pending tool pairs rather than total transcript or payload size.

The complete UTF-8 view SHALL never exceed its documented hard byte limit. Every section
header SHALL remain present. Tool history, final-report text, and error/denial detail SHALL
use explicit budgets and visible truncation markers. When all error entries cannot fit, the
view SHALL retain the total count, stable step references, and a deterministic bounded
sample rather than claim to preserve unbounded text.

#### Scenario: View built from implementer session

- **WHEN** the builder receives a session with 79 tool calls, 2 errors, and a final report
- **THEN** it produces all six sections, represents both errors, and stays within 20KB

#### Scenario: Tool input summarization

- **WHEN** a tool call is `Read src/foo.py`
- **THEN** the tool sequence line contains the path and no file contents

#### Scenario: Bash command truncation

- **WHEN** a failed Bash call contains a 500-character command
- **THEN** the tool line contains a bounded command excerpt and its exit status

#### Scenario: Task description truncation

- **WHEN** the task description exceeds 2000 characters
- **THEN** the Task section contains a bounded prefix and visible truncation marker

#### Scenario: Missing final report

- **WHEN** the transcript has no assistant text blocks
- **THEN** the Final Report section reads `(no final report)`

#### Scenario: Oversized final report is bounded

- **WHEN** the final assistant message contains 1MB of text
- **THEN** the Final Report section is truncated and the whole view stays within the byte limit

#### Scenario: Very long tool history is bounded

- **WHEN** a session has tens of thousands of tool calls and large result bodies
- **THEN** processing uses bounded memory and the Tool Sequence reports a bounded deterministic sample with the total count

#### Scenario: Error volume cannot break the limit

- **WHEN** a session contains more error and denial detail than the view can hold
- **THEN** the Errors & Denials section reports the total, preserves stable sampled step references, marks truncation, and the whole view remains within the hard limit

### Requirement: Verdict dataclass

The system SHALL expose one backend-independent validated verdict boundary. A valid
`Verdict` SHALL contain the exact four rubric dimensions, integer scores from 0 through 5,
bounded evidence, a locally derived arithmetic-mean overall score, bounded typed fixes,
a non-empty concrete model identifier, and finite non-negative judge cost and token
accounting. The system SHALL reject invalid values with `JudgeError` before persistence,
regardless of which `Judge` backend produced them. Serialized verdicts SHALL retain the
required provenance labels for derived and model-authored fields.

#### Scenario: Verdict is serializable to JSON

- **WHEN** a valid verdict is created
- **THEN** `to_verdict_json()` produces the documented verdict payload

#### Scenario: Overall score is mean of dimensions

- **WHEN** dimensions score 4, 5, 3, and 4
- **THEN** `overall_score` equals 4.0 regardless of any backend-supplied overall

#### Scenario: Invalid dimension set or score

- **WHEN** a backend omits a dimension, adds one, or supplies an out-of-range, non-integer, or non-finite score
- **THEN** verdict validation raises `JudgeError` and no row is persisted

#### Scenario: Typed fix is accepted

- **WHEN** a fix uses a known dimension and target with recommendation and rationale within their limits
- **THEN** the verdict carries it as a typed fix record

#### Scenario: Unknown fix dimension is rejected

- **WHEN** a fix names a dimension outside the four rubric dimensions
- **THEN** verdict validation raises `JudgeError`

#### Scenario: Out-of-set fix target is rejected

- **WHEN** a fix names a target outside the closed target set
- **THEN** verdict validation raises `JudgeError`

#### Scenario: Free-form string fix is rejected

- **WHEN** `suggested_fixes` contains a bare string
- **THEN** verdict validation raises `JudgeError`

#### Scenario: Invalid or oversized fix is rejected

- **WHEN** a fix is a bare string, uses an unknown dimension or target, exceeds the item limit, or contains an oversized field
- **THEN** verdict validation raises `JudgeError`

#### Scenario: Oversized evidence is rejected

- **WHEN** evidence exceeds its item-count or item-length limit
- **THEN** verdict validation raises `JudgeError`

#### Scenario: Invalid accounting is rejected

- **WHEN** judge cost or token metadata is negative, boolean, NaN, infinite, or otherwise non-numeric
- **THEN** the backend reports a `JudgeError` and the scoring loop treats it as a per-session malformed-output failure

## ADDED Requirements

### Requirement: Bounded model-output diagnostics

Every exception or log message derived from model-controlled envelope, result, evidence,
or fix content SHALL include only a bounded excerpt or structural summary. Diagnostics
SHALL identify the failing field or item without dumping the complete payload.

#### Scenario: Malformed envelope contains private long text

- **WHEN** a malformed model envelope contains a long unique sentinel
- **THEN** error output identifies the malformed field but omits content beyond the configured excerpt bound

### Requirement: Runtime schema defense

The runtime verdict parser SHALL enforce the same item-count, string-length, enum, score,
and accounting constraints as the external JSON schema. External schema validation SHALL
not be the sole trust boundary.

#### Scenario: Backend bypasses external schema

- **WHEN** a custom or mocked backend returns a structurally typed but over-limit verdict
- **THEN** backend-independent validation rejects it before persistence
