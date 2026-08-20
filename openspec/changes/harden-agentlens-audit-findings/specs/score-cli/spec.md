## MODIFIED Requirements

### Requirement: Max sessions cap

The system SHALL accept `--max-sessions N` only for positive integer N and SHALL reject
zero or negative values before opening the store or calling the judge. The cap SHALL apply
to every judge attempt in the invocation, including attempts used to resolve a floating
model alias. Repeated capped runs SHALL advance across genuinely unscored concrete-model
and input-hash identities rather than re-score the same prefix.

#### Scenario: Cap reached

- **WHEN** 50 sessions are unscored and `--max-sessions 20` is passed
- **THEN** at most 20 judge attempts occur and the summary reports the cap and remaining work

#### Scenario: Capped alias runs progress

- **WHEN** repeated capped runs use an alias that resolves to one concrete model
- **THEN** persisted verdict counts advance until every current input is scored without repeatedly overwriting the first prefix

#### Scenario: Invalid cap is rejected

- **WHEN** `--max-sessions` is zero or negative
- **THEN** Click reports an invalid option, exits with usage status, and makes no judge call

### Requirement: Final summary

The system SHALL print a final summary reporting attempts, sessions scored, total judge
cost, skipped sessions, abort status, and the resolved concrete model when an alias was
used. A run with any skipped session or systemic abort SHALL exit non-zero after printing
the summary. A fully successful run and an explicit user-declined confirmation SHALL keep
status 0.

#### Scenario: All scored

- **WHEN** every attempted session scores successfully
- **THEN** the CLI reports the score count and cost and exits 0

#### Scenario: Partial with skips

- **WHEN** some sessions score and others are skipped
- **THEN** the CLI retains successful verdicts, reports skipped counts, and exits non-zero

#### Scenario: Abort after repeated failures

- **WHEN** the systemic failure threshold aborts scoring
- **THEN** the summary names the abort and the command exits non-zero

#### Scenario: Capped partial output includes failures

- **WHEN** the maximum is reached after one or more failed attempts
- **THEN** the capped summary includes scored, skipped, and aborted state instead of reporting only the cap

#### Scenario: Resolved model named when an alias was used

- **WHEN** an alias resolves successfully
- **THEN** the summary names both the configured alias and concrete verdict identity
