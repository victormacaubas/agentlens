## MODIFIED Requirements

### Requirement: Scoring loop

The scoring loop SHALL find sessions lacking a verdict for the current judge-input hash,
rubric version, and concrete judge model; build a bounded prepared view; call the judge;
and persist a validated verdict. It SHALL claim work before a paid call so concurrent
scorers cannot judge the same identity at the same time.

For a floating model alias, the loop SHALL try candidate sessions until one successful
call resolves the concrete model, the candidate set is exhausted, or the existing systemic
failure threshold aborts the run. A session-specific candidate failure SHALL be recorded
as a skip and SHALL NOT prevent healthy candidates from resolving the alias. After
resolution, the loop SHALL re-query against the concrete model and current input hash.
One invocation-wide maximum SHALL count every attempted judge call, including resolution
candidates.

#### Scenario: Only unscored sessions are judged

- **WHEN** a window contains sessions with verdicts matching their current input hash, rubric, and concrete model
- **THEN** those identities are not judged again

#### Scenario: Changed input is scored again

- **WHEN** a session's prepared input hash changes under the same session, rubric, and model
- **THEN** the changed input appears unscored and receives its own verdict

#### Scenario: Re-run after full scoring is free

- **WHEN** all current input hashes are scored and the configured model is concrete
- **THEN** the loop makes no judge calls

#### Scenario: Re-run under an alias costs one resolution call

- **WHEN** every current input is scored under the model behind an unresolved healthy alias
- **THEN** one candidate call resolves the alias and the concrete-model re-query finds no further work

#### Scenario: Alias resolution uses a healthy candidate

- **WHEN** the first alias-resolution candidate fails for a session-specific reason and a later candidate succeeds
- **THEN** the first is skipped, the later candidate resolves the model, and healthy remaining sessions continue

#### Scenario: Backend-resolved model is preserved

- **WHEN** an alias-configured backend returns a concrete model identifier
- **THEN** persistence uses the concrete identifier rather than the configured alias

#### Scenario: Alias movement invalidates prior verdicts

- **WHEN** an alias resolves to a new concrete model
- **THEN** current sessions are scored under the new model and older model verdicts remain separate

#### Scenario: One cap covers both stages

- **WHEN** a run has a maximum of N sessions and uses an alias
- **THEN** resolution and post-resolution work together make at most N judge attempts

#### Scenario: Persisted verdicts survive failures

- **WHEN** some sessions score before the systemic failure threshold aborts the run
- **THEN** those verdicts remain committed and a later run resumes from genuinely unscored identities

#### Scenario: Concurrent scorer does not duplicate spend

- **WHEN** two processes select the same unscored verdict identity
- **THEN** exactly one process claims and judges it while the other reports or skips the active claim

### Requirement: Verdict persistence

The system SHALL persist verdicts under the identity `(session_id, judge_input_hash,
rubric_version, concrete_judge_model)`. The verdict payload SHALL contain the full
validated verdict and judge cost fields. Persistence SHALL verify that the claimed session
still has the scored input hash and that the writer owns the active claim.

#### Scenario: Verdict upsert is idempotent

- **WHEN** the same exact verdict identity is finalized twice by its valid owner
- **THEN** the store contains one row

#### Scenario: Different models coexist

- **WHEN** one input is scored by two concrete models
- **THEN** both verdicts exist as separate rows

#### Scenario: Different input revisions coexist

- **WHEN** a session's prepared input changes and is scored again under the same rubric and model
- **THEN** the historical and current verdicts remain separately attributable

#### Scenario: Input changes before write

- **WHEN** re-ingest changes the current input hash while a judge call is in flight
- **THEN** finalization rejects the stale verdict instead of attaching it to the new grain

## ADDED Requirements

### Requirement: Recoverable scoring claims

A scoring claim SHALL identify one verdict identity, one owner, and an expiry. A process
SHALL release or finalize its claims after each call. Another process MAY recover an
expired claim after a crash, but SHALL NOT take an active claim.

#### Scenario: Active claim blocks duplicate call

- **WHEN** a second scorer encounters an unexpired claim
- **THEN** it makes no judge call for that identity

#### Scenario: Expired claim is recoverable

- **WHEN** a scorer crashes and its claim expires
- **THEN** a later scorer can claim and complete the verdict
