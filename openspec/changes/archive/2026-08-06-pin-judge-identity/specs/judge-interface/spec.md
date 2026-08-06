## ADDED Requirements

### Requirement: Judge authentication under minimal mode

Minimal mode (`--bare`) reads Anthropic credentials strictly from `ANTHROPIC_API_KEY` or from an `apiKeyHelper` supplied via `--settings`; it never reads OAuth, the keychain, or an `apiKeyHelper` reached through `--setting-sources`. The Claude CLI backend SHALL therefore pass the user's settings file to `--settings` so that a machine authenticating by `apiKeyHelper` has a working credential channel, and SHALL continue to pass `--setting-sources user` so that repo-local settings cannot reconfigure the judge.

#### Scenario: apiKeyHelper authentication succeeds under minimal mode

- **WHEN** the machine authenticates via `apiKeyHelper` with no `ANTHROPIC_API_KEY` in the environment
- **THEN** the judge invocation authenticates successfully rather than failing with a not-logged-in response

#### Scenario: Repo-local settings remain excluded

- **WHEN** the judge is invoked from a directory containing a project or local settings file
- **THEN** those settings are not loaded, because only the `user` setting source is enabled

### Requirement: Resolved judge model identity

The system SHALL record the concrete model that produced a verdict, not the possibly-floating alias the user configured. The Claude CLI backend SHALL read the resolved model identifier from the response envelope's `modelUsage` map and SHALL set the returned `Verdict.judge_model` to that concrete identifier.

The identifier SHALL be taken from the map's **key**, using the entry's `canonicalModel` field only as a fallback when the key is unusable. Where the two differ the key carries the dated snapshot and `canonicalModel` carries the undated family name, so keying identity on `canonicalModel` would permit two different snapshots of one family to collide on a single verdict key — the same class of drift this requirement exists to prevent.

Extraction SHALL be strict: if the envelope carries no `modelUsage`, an empty map, or more than one entry, the backend SHALL raise a `JudgeError` rather than falling back to the configured alias, because a silent fallback would reintroduce the alias ambiguity this requirement removes.

The backend SHALL expose the resolved model identifier after a successful call so callers can key verdict identity on it.

#### Scenario: Alias resolves to a concrete model identifier

- **WHEN** the judge is configured with the alias `sonnet` and the envelope reports a `modelUsage` entry for `claude-sonnet-5`
- **THEN** the returned `Verdict.judge_model` is `claude-sonnet-5`, not `sonnet`

#### Scenario: Concrete model identifier passes through unchanged

- **WHEN** the judge is configured with a fully pinned model string and the envelope reports that same identifier
- **THEN** the returned `Verdict.judge_model` is that identifier

#### Scenario: Missing model usage is a loud failure

- **WHEN** the envelope carries no `modelUsage` field, an empty map, or multiple entries
- **THEN** the backend raises a `JudgeError` and does not fall back to the configured alias

#### Scenario: Dated snapshot key is preferred over the family name

- **WHEN** the envelope's `modelUsage` entry has key `claude-haiku-4-5-20251001` and `canonicalModel` `claude-haiku-4-5`
- **THEN** the returned `Verdict.judge_model` is `claude-haiku-4-5-20251001`, the more precise of the two

#### Scenario: Resolved model is available to callers

- **WHEN** a successful scoring call has completed
- **THEN** the backend exposes the resolved concrete model identifier for use in verdict identity queries

### Requirement: Judge token accounting reflects actual consumption

The system SHALL report the judge's own token footprint from the resolved `modelUsage` entry, summing its input, cache-creation, and cache-read token counts into `judge_input_tokens` and taking its output count as `judge_output_tokens`. The backend SHALL NOT take these figures from the envelope's top-level `usage` map, which reports a nominal input count when a large uncached prompt is booked as cache creation.

#### Scenario: Cache-creation tokens are counted as input

- **WHEN** the envelope reports `usage.input_tokens` of 1 while the `modelUsage` entry reports thousands of cache-creation tokens
- **THEN** the verdict's `judge_input_tokens` reflects the thousands actually consumed, not 1

#### Scenario: Judge cost remains taken from the envelope total

- **WHEN** a verdict is built from a successful envelope
- **THEN** `judge_cost_usd` continues to come from the envelope's `total_cost_usd`, unaffected by the token-accounting source
