## Context

See `proposal.md` for motivation. What matters here is the starting state and what
scoping verified empirically.

`judge/` contains only an empty `__init__.py`. `JudgeBackend` and `JudgeResponse`
already exist in `models/` with settled shapes and docstrings, so this change fills
in behind an interface rather than designing one. `JudgeResponse` already names
`resolved_model`, `is_error`, `raw_result`, `structured_output`, `cost_usd`,
`input_tokens`, `output_tokens`, and `duration_ms`, which fixes what the backend must
populate.

`FactSession` and `FactToolEvent` carry `task_description` plus counters and no
message text of any kind. So the judge input cannot be built from the store, and no
`store` to `judge` flow appears anywhere in this change.

Two live `claude -p` calls were made during scoping against CLI 2.1.241, total spend
about half a cent. They overturned three things the design doc asserted, and the
resulting facts drive most of the decisions below.

**Probe result 1: the documented invocation does not authenticate.** With
`--setting-sources "user"` but no `--settings`, the call returned `is_error: true`,
`result: "Not logged in · Please run /login"`, exit 1, `total_cost_usd: 0`. Adding
`--settings <user settings path>` made the identical call succeed. `--bare`'s own help
text is literal: it reads an `apiKeyHelper` only from `--settings`, and never from the
keychain.

**Probe result 2: `--tools ""` does not mean one turn.** A successful call reported
`num_turns: 2` and `stop_reason: "tool_use"`. Structured output via `--json-schema` is
implemented as an internal tool call, so the floor for a schema-constrained call is
two turns. `--max-turns 3` was therefore a tight bound with exactly one turn of
headroom, not redundant belt-and-braces over `--tools ""`.

**Probe result 3: the envelope has no `model` key.** The resolved model appears only
as `modelUsage`'s key, with `modelUsage[key].canonicalModel` holding the same string.
On the probe both were `claude-sonnet-5`.

Other envelope facts the parser has to survive, all observed:

| field | observed | consequence |
|---|---|---|
| `subtype` | `"success"` while `is_error` was `true` | only `is_error` may be trusted |
| `total_cost_usd` | `0` (int) on failure, `0.00187` (float) on success | accept int or float |
| auth failure | inside `result` on stdout, stderr empty, exit 1 | must parse the envelope |
| `modelUsage` | a dict, one key on a single call | more than one key is ambiguous |
| `provider` | `"firstParty"` despite a proxy `ANTHROPIC_BASE_URL` | not a proxy tell |
| `usage` | has `input_tokens`, `output_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens` | token fields available |

The probe envelope is saved at `/tmp/agentlens-envelope-probe.json` for as long as
that file survives; it is evidence, not a fixture, and no real envelope is committed.

**Probe result 4: the rubric's nested schema is accepted as-is.** A third call passed
the full verdict schema, four dimensions each with an integer score and an evidence
array plus an array of fix objects, all with `additionalProperties: false`, and
`structured_output` came back as a populated nested dict. So the rubric needs no
flattening to survive the `--json-schema` path, and `raw_result` is not the fallback
the validator has to depend on. That call reported `num_turns: 2` again, confirming the
turn floor is a property of structured output rather than of that one probe.

## Goals / Non-Goals

**Goals:**

- Earn the `JudgeBackend` seam by writing a real implementation and its fake together.
- Fix the hardened invocation so the judge actually runs, and record why each part of
  it is there.
- Establish the verdict's identity and storage so that reuse (#27) is a lookup added
  to an existing shape rather than a reshaping.
- Make the projection bounded and honest about its own truncation, since it is both
  the judge's evidence and the cache key.

**Non-Goals:**

- No verdict reuse, no cache lookup, no concurrency. #27.
- No scoring of more than one spawn per invocation. #28.
- No modeled scores in the windowed report. #29.
- No retry policy. A judge failure fails the scoring attempt. Adding retries before
  there is evidence of transient failure modes would be speculative.
- No fix-text rendering on the terminal. Deliberately deferred, see Decisions.

## Decisions

### The hardened invocation, amended

```
claude -p "<prompt>"
  --output-format json
  --model <requested alias or id>
  --json-schema "<rubric verdict schema>"
  --bare
  --tools ""
  --setting-sources "user"
  --settings "<expanded path to the user settings file>"     <-- added
  --max-budget-usd <ceiling>                                 <-- added
  --append-system-prompt "<judge instructions>"
```

with an explicit temporary directory as `cwd`, an environment reduced to `PATH`,
`HOME`, and `ANTHROPIC_*`, and a wall-clock timeout on the subprocess. `--max-turns`
is removed because it no longer exists.

Each part earns its place against a specific failure: `--tools ""` and the temp `cwd`
stop the judge touching the filesystem; `--bare` and `--setting-sources "user"` stop a
repository influencing its own score; `--settings` is what makes authentication work
at all; the timeout stops a hang; `--max-budget-usd` stops runaway spend.

*Alternative considered:* `--safe-mode`, which also disables CLAUDE.md, skills,
plugins, hooks, and MCP. Rejected: `--bare` covers the same ground for this purpose
and additionally pins auth to `ANTHROPIC_API_KEY` or `apiKeyHelper`, which is the
property the reproducibility requirement actually needs. `--safe-mode` is documented
as a troubleshooting flag.

*Alternative considered:* keeping `--setting-sources "user"` alone and requiring the
operator to export `ANTHROPIC_API_KEY`. Rejected: it makes the judge unusable for
anyone authenticating through a helper, which includes this project's own author.

### Bounds live on the backend, not on the Protocol

`JudgeBackend.score(prompt, *, model)` is unchanged. The timeout and the spend ceiling
are constructor arguments on the concrete CLI backend.

A timeout is a property of this transport, not of a scoring request. The fake has no
subprocess and no dollars, so a `timeout` parameter it must accept and ignore is the
tell that the parameter is in the wrong place. ADR 0004 already separates argv
construction (pure, unit-tested, and it says the hardened invocation "deserves
assertions of its own") from execution (thin, one integration canary); both bounds are
argv and execution concerns, so they belong where that argv test can assert them.

*Alternative considered:* per-call parameters, so a large transcript could be given a
larger budget. Rejected for now. Nothing in this change or #28 needs per-spawn
variation, and widening the Protocol speculatively costs the fake clarity. If it
becomes real it is a Protocol change with a reason attached.

### The projection: `ingest` extracts, `models` carries, `judge` renders

CLAUDE.md assigns the "prepared prompt view" to `judge`, but `judge` may import only
`models` and `utils`, so it can never see JSONL. The split:

```
ingest   reads records, extracts the narrative      (parsing is ingest's job)
   |     -> SpawnNarrative (models)
core     passes it across                           (siblings never touch)
   |
judge    renders prompt string, caps, marks elisions, wraps in rubric
```

`ingest/records.py` already has `assistant_message_groups`, which groups assistant
records by `message.id` specifically because one logical turn is often written as
several records. The extraction reuses it rather than re-deriving that grouping, since
getting it wrong would double-count turns in the judge's view of the run.

`SpawnNarrative` carries the task prompt, the ordered assistant text messages, and the
ordered tool sequence in structured form. It is a `models` type with no logic, so
`judge` stays free of both JSONL and `sqlite3`.

*Alternative considered:* build the projection in `judge` from raw records. Rejected:
it would either require `judge` to import `ingest`, which the import contract forbids,
or duplicate JSONL parsing that `ingest` already owns and tests.

*Alternative considered:* build it from store facts. Impossible as it turns out, since
no fact row holds message text, and desirable that it is impossible: verdicts do not
become downstream of ingest's aggregation logic.

### Bounding the projection: cap within messages, never drop them

Every assistant text message is present in the projection. Long ones are shortened
head-and-tail with an explicit in-band elision marker. A global ceiling acts as a
backstop and marks itself the same way. Tool events are capped by count, also marked.

Dropping whole messages to fit a budget is the one strategy that can delete the exact
sentence the projection exists to preserve. "I'll skip the tests for now" is a line
inside a message, likelier near its start than its end, and it is the honesty evidence
most worth keeping. Head-and-tail capping preserves that class of statement even in a
very long run.

The markers are in-band rather than metadata for two reasons. A judge that cannot tell
it is seeing a partial run scores it as complete, which biases honesty in the worst
direction: an agent whose misbehavior was truncated away reads as clean. And since the
projection *is* the cache key, two different truncations of one run must not hash
identically.

*Alternative considered:* a token-count budget rather than bytes. Rejected: it needs a
tokenizer, which is a dependency, and ADR 0002's set is closed. Bytes over-approximate
in the safe direction.

### `rubric_version` is a hand-bumped string, guarded by a pinning test

A short opaque `TEXT` value, `"v1"`, incremented by a human. A test pins the digest of
the rubric's content against the declared version, so editing the rubric without
bumping fails `make check`.

A content hash as the version was considered and rejected: it makes fixing a typo in
the rubric prose silently invalidate every stored verdict and repay for all of them.
Hand-bumping makes invalidation deliberate. The obvious failure of hand-bumping is
forgetting, which is exactly what the pinning test catches, so the pair gets a hash's
safety with a human's control.

### The resolved model comes from `modelUsage`, and more than one key is an error

`resolved_model` is read from `modelUsage`'s single key. If `modelUsage` holds no keys
or more than one, the call is unusable and raises `JudgeResponseError` naming the
ambiguity.

Choosing one of two models would silently corrupt the identity every later comparison
depends on, which is worse than failing a scoring attempt.

**Known limitation, accepted deliberately.** `claude-sonnet-5` carries no date stamp,
so it floats across point releases. ADR 0003 wants verdicts comparable under a
concrete model and this is the most specific identifier the envelope offers. Two
verdicts recorded under the same string may have come from different weights. The gap
is recorded rather than hidden, and nothing in this change can close it.

### Terminal shows scores only; fix text goes to the artifact

The summary prints the overall score and four dimension scores, all locally derived
integers, and names where the fixes were written. No evidence or fix prose reaches the
terminal.

A terminal cannot be relied on to render hostile text inertly: ANSI escapes repaint
the screen, a newline can fake a prompt line, and a recommendation reading like a
shell command looks copy-pasteable whatever label sits above it. Doing this safely
needs a sanitizer, which is security-critical code, and this slice already carries the
judge, the projection, a new table, and two amended surfaces.

The cost is real and worth naming: `PRODUCT.md` says "the fix is the product, not the
score", and this slice ships the score to the terminal and the fix to a file. That is
a deliberate deferral, not a resolution. Presenting fix text readably and safely
deserves its own change.

### Error translation

`judge` catches `OSError`, `subprocess.SubprocessError`, and `json.JSONDecodeError` at
its boundary, per ADR 0005, and nothing `subprocess`-shaped leaves the package.

`JudgeUnavailableError`: binary absent, timeout expired, or the envelope reports not
being authenticated. `JudgeResponseError`: `is_error` true, `modelUsage` ambiguous, or
the verdict fails validation.

Auth-shaped failures are distinguished by what the envelope says rather than lumped
together, because the probe surfaced a third case the taxonomy did not anticipate: a
present, configured judge whose credential helper failed to reach its secret store.
Reporting that as "not logged in" sends the reader to fix the wrong thing. It maps to
`JudgeUnavailableError` with its own message.

### Verdict storage

One `fact_verdict` table keyed on session, judge-input hash, rubric version, and
resolved model, upserting on that key. Scores are stored as columns; evidence and fix
text are stored as untrusted payload with the provenance split recorded alongside, so
a reader never has to infer trust from a field name.

No score, verdict, or fix column is added to any deterministic table. The import
contract already makes `ingest` and `judge` unable to see each other; keeping the
tables separate is the storage-level form of the same rule.

`fact_verdict` returns no `UpsertOutcome` and adds no member to it. `fact_session`
needs that vocabulary because it compares derivation fingerprints and can legitimately
refuse a stale write; `dim_agent` needs none because content-addressed identity makes
every conflict definitionally identical, so it uses `ON CONFLICT DO NOTHING`.
`fact_verdict` is a third case: a conflict on the full natural key means the same
input, rubric, and model were scored again, and because the judge is nondeterministic
the new verdict can differ from the stored one. So the conflict clause is an
unconditional `DO UPDATE` with no staleness comparison and nothing to report back.
There is no refusal case to name.

The provenance split is a constant, `VERDICT_PROVENANCE`, and a fix's `target` sits on
the untrusted side of it. Only `dimension` is locally derived among a fix's fields,
because the validator constrains it to the rubric's fixed set and rejects anything
else. A `target` is free text the model wrote to name what to fix, so nothing local
derives it and every surface must escape it. Classifying it as locally derived would
let a path or a control sequence the model invented through unescaped.

### The judge-isolation guard moves from a runtime assertion to the import contract

`test_cli_report.py` asserted `"agentlens.judge" not in sys.modules` inside a
shared pytest process, to prove the deterministic report path never touches the
judge. That assertion is order-dependent: it only holds if no earlier test in
the same process imported `agentlens.judge`, which stopped being true the
moment `judge` gained its own test modules. The report path's own behavior
never changed; the test was checking a process-global side effect of whichever
tests happened to run first.

Two ways to re-establish the guarantee were considered. Driving the report
command in a fresh interpreter via `subprocess.run([sys.executable, "-c",
...])` and inspecting that interpreter's own `sys.modules` would restore
process isolation, but it pays a subprocess's wall-clock cost on every future
run of this one test, for a guarantee something else already gives statically.

The `lint-imports` contract "The deterministic report path never reaches the
judge" already forbids `agentlens.judge` from `agentlens.core.report` and
`agentlens.core.ingest_run`, and `allow_indirect_imports` is not set for that
contract, so it disallows indirect imports too: reaching the judge through a
helper module breaks the contract exactly as directly as importing it inline
would. That is precisely "no import of `agentlens.judge`, direct or indirect,
from the report path" — the same property the runtime assertion existed to
prove, checked statically instead of by inspecting one process's import
cache. It also runs on every `make check`, not only when this one test
happens to execute first in the suite.

**Decision: retire the runtime assertion, rely on the import contract.** The
`sys.modules` line and the now-unused `sys` import are removed from
`test_cli_report.py`. The test's other assertions (schema version, current-
and prior-window spawn counts) are about window arithmetic, not about the
judge, so the test is renamed to
`test_since_7d_emits_real_current_and_prior_numbers`, dropping the
`without_a_judge` clause the deleted assertion justified. Task 6.1 adds the
test that proves the import contract actually fails the build when broken,
which is what makes relying on it here sound rather than a restated hope.

## Risks / Trade-offs

**The judge works today on this machine for a reason unrelated to agentlens.** The
successful probe ran with its `apiKeyHelper` erroring (`could not read secret
'op://Employee/LiteLLM Token/password'`) and succeeded off a cached token. → The
credential-failure case gets its own message and its own scenario, so a future failure
reads as an operator problem rather than an agentlens bug. It cannot be tested against
the real CLI without breaking the operator's own auth, so the unit test drives it
through a fake envelope.

**A flag rename in the CLI lands silently.** ADR 0004 already names this as an
accepted consequence, and this change is the proof: the documented invocation had been
wrong in two ways for some time. → The integration canary asserts the invocation
works, `make integration` is opt-in, and the amended contract is recorded in an ADR
so the next re-verification has something specific to check rather than prose to
re-read.

**The projection is where a wrong verdict comes from.** If it omits the evidence a
dimension needs, the judge scores confidently on an incomplete view and the number
looks as trustworthy as any other. → Bounds are generous, elisions are marked in-band
so the judge is told, and the rubric's instructions say what to do when the input
declares itself partial.

**Byte caps over-approximate token cost.** A projection inside its byte ceiling can
still be an expensive call. → `--max-budget-usd` is the backstop, which is part of why
it is worth having despite the turn floor being only two.

**`total_cost_usd` versus `modelUsage[key].costUSD`.** Both exist and they are not the
same number. → `total_cost_usd` is used, matching the design doc, and the choice is
recorded here so a future reader does not switch them for looking more precise.

**Re-scoring costs money with no warning.** Without reuse, scoring the same spawn
twice pays twice. → `--dryrun` reports the identity that would be written, and #27 is
the fix. Named in the spec so the behavior is a decision rather than an oversight.

## Migration Plan

No data migration. The store is a rebuildable cache per ADR 0003, so `fact_verdict`
is created on next use and an existing store gains an empty table.

Documentation changes that ship with the code, because leaving them undone re-breaks
the contract this change exists to fix:

- `docs/agentlens-design.md`: amend the hardened invocation to add `--settings`, drop
  `--max-turns`, add the spend ceiling and the timeout, and correct the claim that
  `--tools ""` bounds turns.
- New ADR: what bounds a judge call, how it authenticates, and why the resolved model
  is read from `modelUsage`. It binds every future judge call, which is the test
  CLAUDE.md sets for promoting a design decision to an ADR.
- `DESIGN.md` cites "ADR 0011" for the untrusted-output rule and no such ADR exists.
  Repoint it at the real source.

Rollback is removing the flag; nothing else changes behavior for an unscored run.

## Open Questions

These can be answered during implementation without changing the specs, the approach,
or the task breakdown.

- The concrete wall-clock timeout and spend ceiling values. Both are constructor
  arguments with defaults; tuning them changes no requirement. One data point from
  scoping: a deliberately trivial scoring prompt against the real rubric schema cost
  `$0.011`, roughly six times the one-line probe, so a ceiling in the low tens of cents
  is the right order and `0.25` was not reached.
- The concrete byte caps for per-message, whole-projection, and tool-event count.
  Same reasoning.
- What `--max-budget-usd` does on breach: refuse, or truncate mid-response. Unverified
  because provoking it costs real money for little information. Safe to defer because
  both outcomes satisfy the spec: a refusal is a failed call, and a truncated response
  fails verdict validation on its missing dimensions. Worth confirming opportunistically.
- Whether safe fix-text rendering lands later in Phase 3 or waits for the Phase 5 HTML
  report. A scoping question for a change that does not exist yet.
