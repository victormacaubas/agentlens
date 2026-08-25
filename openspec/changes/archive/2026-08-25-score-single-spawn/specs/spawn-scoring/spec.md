## Purpose

Producing a modeled verdict for one agent run: what the judge is shown, what bounds
the call, what makes a verdict identifiable and comparable, and which parts of the
result are trustworthy enough to compute on versus merely display.

## ADDED Requirements

### Requirement: Scoring is requested explicitly, one spawn at a time

Scoring SHALL happen only when a caller asks for it, and a single request SHALL
produce at most one judge call and at most one verdict.

#### Scenario: Scoring is not requested

- **WHEN** a spawn is analyzed without scoring being requested
- **THEN** no judge call is made, no verdict is produced, and the run's observable
  behavior is unchanged from a run made before scoring existed

Rationale: the deterministic path is free and the scored path is not. A default that
spends money on a command users already run for facts is a trap.

#### Scenario: Scoring is requested for one spawn

- **WHEN** scoring is requested for a spawn that has been ingested successfully
- **THEN** exactly one judge call is made and exactly one verdict is recorded for it

#### Scenario: Scoring is requested twice for the same unchanged spawn

- **WHEN** the same spawn is scored again with an unchanged judge input, rubric
  version, and resolved model
- **THEN** the second run pays for a second judge call and replaces the stored
  verdict for that identity rather than accumulating a second row

Rationale: verdict reuse is deliberately out of scope here. This scenario fixes what
re-scoring means so that adding reuse later changes cost, not stored shape.

### Requirement: The judge is shown a bounded projection, not a raw transcript

The judge input SHALL be a derived view of the run containing the task prompt, every
assistant text message, and a structured account of the tool sequence. It SHALL be
bounded in size independently of transcript length, and every place content was
elided SHALL be marked inside the input itself.

#### Scenario: A long run is projected within bounds

- **WHEN** a run's transcript exceeds the input's size bounds
- **THEN** the projection stays within bounds, every assistant message remains
  present in some form rather than being dropped, and each shortened message carries
  an explicit elision marker

Rationale: dropping whole messages can delete the one sentence the projection exists
to preserve. A mid-run statement of intent is the honesty evidence most worth
keeping, and it sits inside a message rather than being a message's absence.

#### Scenario: Elision is visible to the judge and to the identity

- **WHEN** two runs differ only in what was elided from their projections
- **THEN** their judge inputs are not identical, and each input states that content
  was elided

Rationale: a judge that cannot tell it is seeing a partial run scores it as complete,
which biases honesty in the worst direction: an agent whose misbehavior was truncated
away reads as clean.

#### Scenario: Projection does not depend on stored data

- **WHEN** a spawn is scored against an empty store
- **THEN** the judge input is produced from the parsed transcript alone and is
  identical to the input produced when the same spawn is already stored

### Requirement: The rubric is pinned and versioned

The rubric SHALL cover task completion, honesty, efficiency, and scope adherence,
each with an integer score from 0 to 5 and supporting evidence, together with an
overall score and suggested fixes. It SHALL carry a version identifier that changes
whenever the rubric's content changes.

#### Scenario: A verdict names the rubric it was produced under

- **WHEN** a verdict is recorded
- **THEN** it carries the rubric version that produced it

#### Scenario: Rubric content and version cannot drift apart

- **WHEN** the rubric's content is changed without its version identifier being
  changed
- **THEN** the project's quality gate fails

Rationale: a hand-bumped version gives deliberate cache invalidation, which a content
hash does not, but only if forgetting to bump is caught mechanically.

### Requirement: A verdict is validated locally before it is used

A returned verdict SHALL be checked against the rubric's shape and ranges before it
is persisted, displayed, or counted, and SHALL be rejected rather than repaired when
it does not conform.

#### Scenario: Well-formed verdict is accepted

- **WHEN** the judge returns all four dimensions with integer scores in range,
  evidence, an overall score, and fixes
- **THEN** the verdict is accepted and recorded

#### Scenario: Score out of range

- **WHEN** a returned dimension score falls outside 0 to 5
- **THEN** the verdict is rejected as unusable, nothing is persisted for it, and the
  failure names the offending dimension

#### Scenario: Missing or unknown dimension

- **WHEN** a returned verdict omits one of the four dimensions, or names a dimension
  the rubric does not define
- **THEN** the verdict is rejected as unusable and the failure names what was missing
  or unrecognized

#### Scenario: Envelope reports an error

- **WHEN** the judge's response envelope reports an error
- **THEN** that is treated as a failed call regardless of any other status the
  envelope carries, and no verdict is recorded

Rationale: the envelope carries more than one status-like field and they can
disagree, so exactly one of them may be trusted as the error signal.

### Requirement: Provenance is recorded per field

A verdict SHALL record which of its fields were derived locally and which are
untrusted model output, and every surface presenting it SHALL preserve that
distinction.

#### Scenario: Scores and prose are distinguished

- **WHEN** a recorded verdict is read
- **THEN** its scores are marked as locally derived and its evidence and fix text are
  marked as untrusted model output

#### Scenario: Untrusted text is never presented as actionable

- **WHEN** any surface presents evidence or fix text
- **THEN** it is marked as untrusted, and nothing shaped like a patch, a diff, or a
  runnable command is emitted for direct application

### Requirement: A verdict is identified by its inputs and its resolved model

A verdict SHALL be uniquely identified by the session, the hash of the exact judge
input, the rubric version, and the concrete model identifier read from the response
envelope rather than the alias that was requested.

#### Scenario: Alias is not recorded as the model

- **WHEN** scoring is requested with a floating model alias
- **THEN** the recorded model identifier is the concrete identifier the envelope
  reported, not the alias

Rationale: verdicts scored under different concrete models are not comparable, and an
alias floats.

#### Scenario: Envelope reports more than one model

- **WHEN** the response envelope attributes usage to more than one model
- **THEN** the call is treated as unusable rather than one model being chosen, and the
  failure states that the verdict's identity is ambiguous

Rationale: guessing which of two models produced the verdict silently corrupts the
identity that every later comparison depends on.

### Requirement: The judge runs read-only and reproducibly

A judge call SHALL have no tool reachable to it, SHALL run with an explicit temporary
working directory, SHALL load only the invoking user's settings and none belonging to
a project or checkout, and SHALL receive only the environment its authentication
needs.

#### Scenario: No tool is reachable

- **WHEN** a judge call runs
- **THEN** no tool is available to it, and nothing in the user's filesystem is read or
  written as a result of the call

#### Scenario: Project configuration cannot influence a verdict

- **WHEN** a judge call runs from within a project that carries its own instructions,
  hooks, or configuration
- **THEN** none of it is loaded, and the verdict is the same as one produced for the
  same input from an unrelated directory

Rationale: a scored spawn's verdict must not depend on which directory agentlens
happened to be run from, and a repository must not be able to influence its own score.

### Requirement: A judge call is bounded in time and in spend

A judge call SHALL be bounded by a wall-clock limit and by a maximum spend, and
exceeding either SHALL end the call rather than allowing it to continue.

#### Scenario: Call does not return

- **WHEN** a judge call produces no response within its wall-clock limit
- **THEN** the call is ended, the failure reports that the judge did not respond in
  time, and no verdict is recorded

Rationale: the observable risk is a call that hangs, which no spend limit addresses.

#### Scenario: Call exceeds its spend limit

- **WHEN** a judge call would spend more than its configured maximum
- **THEN** the call is bounded and no verdict is recorded from it

### Requirement: An unusable judge fails fast and names the cause

When the judge cannot be used, the run SHALL stop promptly with a message
identifying which cause applies, and SHALL NOT silently continue as though scoring
had been declined.

#### Scenario: Judge is not installed

- **WHEN** the judge cannot be found
- **THEN** the run reports that it could not be found, names what it looked for, and
  exits with the judge failure code

#### Scenario: Judge is not authenticated

- **WHEN** the judge is present but reports that it is not authenticated
- **THEN** the run reports that authentication is the cause, distinguishes it from
  the judge being absent, and exits with the judge failure code

#### Scenario: Credential lookup fails

- **WHEN** the judge is present and configured but its credential lookup fails
- **THEN** the run reports that cause specifically rather than reporting it as not
  authenticated

Rationale: a helper that cannot reach its secret store is an operator problem
unrelated to agentlens, and reporting it as "not logged in" sends the reader to fix
the wrong thing.

#### Scenario: A failed scoring attempt does not discard deterministic work

- **WHEN** a spawn is ingested successfully and its scoring attempt then fails
- **THEN** the deterministic facts for that spawn remain recorded and the failure is
  reported as a scoring failure rather than an ingest failure

### Requirement: agentlens reports its own cost, and only its own

The dollar cost and token counts of agentlens's judge calls SHALL be recorded and
reported. The token usage of the agent runs being analyzed SHALL be reported as a
quality signal and SHALL NOT be expressed in currency.

#### Scenario: Judge cost is recorded

- **WHEN** a verdict is recorded
- **THEN** the judge call's dollar cost and its input and output token counts are
  recorded with it

#### Scenario: Analyzed usage is never dollarized

- **WHEN** any surface reports the token usage of an analyzed agent run
- **THEN** it is expressed in tokens and cache-read proportion, and no currency
  figure is derived from it

#### Scenario: Cost is reported for a call that produced no usable verdict

- **WHEN** a judge call completes but its verdict is rejected as unusable
- **THEN** the cost that was already spent is still reported

Rationale: a rejected verdict was still paid for, and a cost report that hides it
understates what the run cost.
