## MODIFIED Requirements

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
