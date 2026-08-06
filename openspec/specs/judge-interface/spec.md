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

The system SHALL build a structured text document from a parsed session and its raw JSONL transcript, containing exactly these sections: Task (from .meta.json description or first user record, truncated at 2000 chars), Agent Identity (type, spawn_depth, parent_session_id), Deterministic Facts (turns, tool_calls, duration, errors, permission_denials, duplicate_calls, tokens, final_report_flagged_partial), Tool Sequence (one line per tool call with condensed input), Errors & Denials (first 300 chars of error output per failed step), and Final Report (last assistant message). The completed view SHALL stay within a deterministic byte budget: the Final Report and Tool Sequence sections are bounded and truncated with a visible truncation marker so the total view never exceeds the documented hard limit, while all six section headers and every error/denial entry are always preserved.

#### Scenario: View built from implementer session

- **WHEN** the view builder receives a parsed session with 79 tool calls, 2 errors, and a final report
- **THEN** it produces a structured text with all six sections, Errors section has 2 entries, and total size is under 20KB

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

#### Scenario: Oversized final report is bounded

- **WHEN** the final assistant message is 1MB of text
- **THEN** the Final Report section is truncated to its budget with the truncation marker and the whole view stays within the documented hard limit

#### Scenario: Very long tool history is bounded

- **WHEN** a session has tens of thousands of tool calls
- **THEN** the Tool Sequence section is bounded (e.g. a head/tail sample plus a total count) rather than emitting one unbounded line per call, and all error/denial entries remain in the Errors & Denials section

### Requirement: Claude CLI backend

The system SHALL implement the `Judge` Protocol via a `ClaudeCliJudge` class that invokes `claude -p` with `--output-format json`, `--model <configured>`, `--json-schema <verdict-schema>`, `--max-turns 3`, `--bare`, and `--append-system-prompt <rubric>`.

Because the transcript view supplied on stdin is untrusted, attacker-influenceable data, the invocation SHALL grant **no filesystem or shell tools**, and SHALL enforce this by explicitly disabling the built-in tool set (`--tools ""`) rather than by omitting a tool-granting flag — omitting `--allowedTools` selects the CLI's default, which grants all built-in tools. The invocation SHALL NOT enable a permissive permission mode.

The invocation SHALL pin its setting sources to `user` only, so that project- and directory-local settings files cannot reconfigure the judge and the invocation is independent of the working directory agentlens runs from. The subprocess SHALL be launched with an explicit working directory (a temporary directory, not agentlens's own) and an explicitly constructed environment forwarding only what auth and model routing require, rather than inheriting agentlens's working directory and full environment.

Because `--bare` is retained (for cost and for a reproducible judge context) and `--bare` does not read OAuth or keychain credentials, the backend SHALL detect the CLI's not-logged-in response and raise `JudgeUnavailableError` naming the remedy — set `ANTHROPIC_API_KEY` or configure `apiKeyHelper` — rather than treating it as a per-session judge failure.

The backend SHALL parse the envelope's `structured_output` field as the verdict and record `total_cost_usd` and `usage` from the envelope.

#### Scenario: Successful scoring

- **WHEN** the backend is called with a valid transcript view
- **THEN** it returns a `Verdict` with all four dimension scores, evidence, suggested_fixes, and judge cost metadata

#### Scenario: No tools are granted to the judge

- **WHEN** the backend builds the `claude -p` argument list
- **THEN** the arguments contain an explicit empty built-in tool set (`--tools ""`), grant no `Read`, `Grep`, `Bash`, or other filesystem/shell tool, and do not enable a permissive `--permission-mode dontAsk`

#### Scenario: Prompt-injected transcript cannot read the filesystem

- **WHEN** a transcript view containing an embedded instruction to read a canary file outside the prepared view is scored against the real CLI
- **THEN** the canary file's contents appear nowhere in the resulting verdict's evidence or suggested fixes, because the judge has no tool capable of reading it

#### Scenario: Structured output survives tool removal

- **WHEN** the backend invokes the CLI with the built-in tool set disabled and a verdict JSON schema
- **THEN** the envelope still carries a populated `structured_output` and a valid `Verdict` is produced

#### Scenario: Judge invocation ignores directory-local settings

- **WHEN** agentlens is run from a directory containing a `.claude/settings.local.json` that would alter tool permissions or the model
- **THEN** the judge invocation pins its setting sources to `user`, so that file does not affect the scoring call

#### Scenario: Subprocess does not inherit agentlens's working directory

- **WHEN** the backend launches the `claude` subprocess
- **THEN** it passes an explicit temporary working directory and an explicitly constructed environment, not agentlens's own cwd and full environment

#### Scenario: Missing credentials fail loudly with a remedy

- **WHEN** the CLI responds that it is not logged in (no `ANTHROPIC_API_KEY` and no configured `apiKeyHelper`), exiting non-zero while still emitting a JSON envelope reporting it
- **THEN** the backend raises `JudgeUnavailableError` whose message names setting `ANTHROPIC_API_KEY` or configuring `apiKeyHelper`, and the scoring loop does not count it as one of the consecutive per-session failures

#### Scenario: An unrelated non-zero exit is not misreported as an auth failure

- **WHEN** the CLI exits non-zero with output that carries no not-logged-in marker, including output that is not valid JSON
- **THEN** the backend raises `JudgeError` with the underlying output, unchanged by the credential detection

#### Scenario: Subprocess timeout

- **WHEN** the `claude -p` subprocess exceeds 60 seconds
- **THEN** the backend kills the process and raises a `JudgeTimeoutError`

#### Scenario: Envelope indicates error

- **WHEN** the envelope's `is_error` field is true
- **THEN** the backend raises a `JudgeError` with the envelope's result text as the message

#### Scenario: Claude CLI not found

- **WHEN** `claude` is not on PATH
- **THEN** the backend raises a `JudgeUnavailableError` before attempting any subprocess call

#### Scenario: Subprocess launch failure is normalized

- **WHEN** `subprocess.run` fails to launch `claude` with an `OSError`
- **THEN** the backend raises a `JudgeError` rather than letting the `OSError` escape

### Requirement: Verdict dataclass

The system SHALL define a `Verdict` frozen dataclass with fields: `session_id`, `rubric_version`, `judge_model`, `dimensions` (a dict mapping dimension name to a `DimensionScore` with `score: int` 0-5 and `evidence: list[str]`), `overall_score` (float, mean of dimensions), `suggested_fixes` (list of strings), `judge_cost_usd` (float), `judge_input_tokens` (int), `judge_output_tokens` (int).

#### Scenario: Verdict is serializable to JSON

- **WHEN** a Verdict is created with valid fields
- **THEN** calling `json.dumps` on its `to_verdict_json()` method produces valid JSON matching the `fact_verdict.verdict_json` column schema

#### Scenario: Overall score is mean of dimensions

- **WHEN** dimensions are task_completion=4, honesty=5, efficiency=3, scope_adherence=4
- **THEN** overall_score is 4.0

### Requirement: Locally derived overall score

The system SHALL derive `overall_score` locally as the arithmetic mean of the four dimension scores when constructing a `Verdict`, and SHALL NOT trust an `overall_score` value supplied by the judge model. Every dimension score SHALL be validated to be an integer within 0-5; a score outside that range SHALL raise a `JudgeError`. This invariant SHALL hold for any `Judge` backend that constructs a `Verdict`, so no backend can persist an out-of-range or inconsistent overall score.

#### Scenario: Supplied overall score is ignored in favor of the mean

- **WHEN** a judge response reports dimensions averaging 4.0 but supplies `overall_score = 99`
- **THEN** the constructed `Verdict` has `overall_score = 4.0` (the derived mean), not 99

#### Scenario: Out-of-range dimension score is rejected

- **WHEN** a judge response reports a dimension score of 6 (or a negative value, NaN, or non-integer)
- **THEN** the backend raises a `JudgeError` and no verdict is persisted

#### Scenario: Derived overall is always in range

- **WHEN** any `Verdict` is persisted
- **THEN** its `overall_score` is within 0-5 and equals the mean of its four dimension scores
