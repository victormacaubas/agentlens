# Judge Interface

## Purpose

Defines the LLM judge protocol, the prepared transcript view builder, the Claude CLI backend, and the verdict dataclass — the contract between the scoring loop and any backend implementation.

## Requirements

### Requirement: Judge Protocol

The system SHALL define a `Judge` Protocol with a single `score` method that accepts a prepared transcript view (string) and a rubric version (string) and returns a `Verdict` dataclass. The Protocol SHALL be the sole contract between the scoring loop and any backend implementation.

#### Scenario: Protocol is implementable

- **WHEN** a new backend class implements the `Judge` Protocol's `score` method
- **THEN** the scoring loop accepts and uses that backend without modification

### Requirement: Prepared transcript view

The system SHALL build a structured text document from a parsed session and its raw JSONL transcript, containing exactly these sections: Task (from .meta.json description or first user record, truncated at 2000 chars), Agent Identity (type, spawn_depth, parent_session_id), Deterministic Facts (turns, tool_calls, duration, errors, permission_denials, duplicate_calls, tokens, final_report_flagged_partial), Tool Sequence (one line per tool call with condensed input), Errors & Denials (first 300 chars of error output per failed step), and Final Report (full text of last assistant message).

#### Scenario: View built from implementer session

- **WHEN** the view builder receives a parsed session with 79 tool calls, 2 errors, and a final report
- **THEN** it produces a structured text with all six sections, tool sequence has 79 lines, Errors section has 2 entries, and total size is under 20KB

#### Scenario: Tool input summarization

- **WHEN** a tool call is `Read src/foo.py`
- **THEN** the tool sequence line is `Read src/foo.py` (path only, no file contents)

#### Scenario: Bash command truncation

- **WHEN** a Bash tool call has a 500-char command that exited with code 1
- **THEN** the tool sequence line shows the first 120 chars of the command and `→ exit 1`

#### Scenario: Task description truncation

- **WHEN** the task description exceeds 2000 characters
- **THEN** the Task section contains exactly the first 2000 characters followed by a truncation marker

#### Scenario: Missing final report

- **WHEN** the transcript has no assistant text blocks (degenerate session)
- **THEN** the Final Report section reads "(no final report)"

### Requirement: Claude CLI backend

The system SHALL implement the `Judge` Protocol via a `ClaudeCliJudge` class that invokes `claude -p` with `--output-format json`, `--model <configured>`, `--json-schema <verdict-schema>`, `--permission-mode dontAsk`, `--allowedTools "Read,Grep"`, `--max-turns 3`, `--bare`, and `--append-system-prompt <rubric>`. It SHALL parse the envelope's `structured_output` field as the verdict and record `total_cost_usd` and `usage` from the envelope.

#### Scenario: Successful scoring

- **WHEN** the backend is called with a valid transcript view
- **THEN** it returns a `Verdict` with all four dimension scores, evidence, suggested_fixes, and judge cost metadata

#### Scenario: Subprocess timeout

- **WHEN** the `claude -p` subprocess exceeds 60 seconds
- **THEN** the backend kills the process and raises a `JudgeTimeoutError`

#### Scenario: Envelope indicates error

- **WHEN** the envelope's `is_error` field is true
- **THEN** the backend raises a `JudgeError` with the envelope's result text as the message

#### Scenario: Claude CLI not found

- **WHEN** `claude` is not on PATH
- **THEN** the backend raises a `JudgeUnavailableError` before attempting any subprocess call

### Requirement: Verdict dataclass

The system SHALL define a `Verdict` frozen dataclass with fields: `session_id`, `rubric_version`, `judge_model`, `dimensions` (a dict mapping dimension name to a `DimensionScore` with `score: int` 0-5 and `evidence: list[str]`), `overall_score` (float, mean of dimensions), `suggested_fixes` (list of strings), `judge_cost_usd` (float), `judge_input_tokens` (int), `judge_output_tokens` (int).

#### Scenario: Verdict is serializable to JSON

- **WHEN** a Verdict is created with valid fields
- **THEN** calling `json.dumps` on its `to_verdict_json()` method produces valid JSON matching the `fact_verdict.verdict_json` column schema

#### Scenario: Overall score is mean of dimensions

- **WHEN** dimensions are task_completion=4, honesty=5, efficiency=3, scope_adherence=4
- **THEN** overall_score is 4.0
