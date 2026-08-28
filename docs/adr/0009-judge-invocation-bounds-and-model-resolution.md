# 0009. Judge invocation bounds and model resolution

## Status

Accepted

## Context

The documented `claude -p` invocation for the judge (`docs/agentlens-design.md`)
predated any real call against the CLI and turned out to be wrong in two ways,
found by making two live calls against CLI 2.1.241 during `score-single-spawn`:

- With `--setting-sources "user"` but no `--settings`, the call returned
  `is_error: true`, `result: "Not logged in · Please run /login"`, exit 1.
  `--bare`'s own help text is literal about this: it reads an `apiKeyHelper`
  only from an explicit `--settings` path, never from `--setting-sources`
  alone and never from the keychain.
- A successful call reported `num_turns: 2` and `stop_reason: "tool_use"`
  despite `--tools ""`. Structured output via `--json-schema` is implemented
  as an internal tool call, so the floor for a schema-constrained call is two
  turns regardless of how many real tools are disabled. `--max-turns 3` had
  been documented as the flag that "bounds the call now that there are no
  tools to loop on"; that claim does not survive contact with the CLI, and
  `--max-turns` does not exist as a flag on the installed version either.

A third fact shapes model identity: the response envelope has no top-level
`model` key. The resolved model appears only as the single key of the
`modelUsage` object.

## Decision

**The invocation adds `--settings` and `--max-budget-usd`, and drops
`--max-turns`:**

```
claude -p "<prompt>"
  --output-format json
  --model <requested alias or id>
  --json-schema "<rubric verdict schema>"
  --bare
  --tools ""
  --setting-sources "user"
  --settings "<expanded path to the user's settings file>"
  --max-budget-usd <ceiling>
  --append-system-prompt "<judge instructions>"
```

launched with an explicit temporary directory as `cwd`, an environment reduced
to `PATH`, `HOME`, and `ANTHROPIC_*`, and a wall-clock timeout enforced by the
subprocess caller rather than a CLI flag, since no equivalent flag exists.

Each addition earns its place against a specific failure: `--settings` is what
makes `--bare` authentication work at all; `--max-budget-usd` bounds spend
given that the turn floor no longer bounds anything a flag can tighten.
`--tools ""` and the temporary `cwd` still stop the judge touching the
filesystem; `--bare` and `--setting-sources "user"` still stop a repository's
own configuration from influencing its own score.

*Alternative considered:* `--safe-mode`, which also disables CLAUDE.md,
skills, plugins, hooks, and MCP. Rejected: `--bare` covers the same ground for
this purpose and additionally pins auth to `ANTHROPIC_API_KEY` or
`apiKeyHelper`, which is the property the reproducibility requirement
actually needs. `--safe-mode` is documented as a troubleshooting flag.

*Alternative considered:* keeping `--setting-sources "user"` alone and
requiring the operator to export `ANTHROPIC_API_KEY`. Rejected: it makes the
judge unusable for anyone authenticating through a helper, which includes
this project's own author.

**The timeout and spend ceiling are constructor arguments on the concrete
`claude` CLI backend, not parameters on the `JudgeBackend` Protocol.** A
timeout is a property of this transport, not of a scoring request: a fake
backend has no subprocess and no dollars, so a `timeout` parameter it must
accept and ignore is the tell that the parameter belongs on the concrete
implementation instead. Argv construction is already a pure, unit-tested
function per ADR 0004's seam boundary, and both bounds are argv and execution
concerns, so they belong where that argv test can assert them directly.

*Alternative considered:* per-call parameters, so a larger transcript could be
given a larger budget. Rejected for now: nothing built so far needs per-spawn
variation, and widening the Protocol speculatively costs the fake clarity for
no exercised requirement. A concrete need is a Protocol change with a reason
attached, not a default to build ahead of one.

**The resolved model is read from `modelUsage`'s single key, and more than
one key is an error.** If `modelUsage` holds zero keys or more than one, the
call is unusable and the judge raises rather than guessing, because choosing
between two candidate models would silently corrupt the identity every later
verdict comparison depends on.

## Consequences

- **A model alias floats across point releases.** `claude-sonnet-5` carries no
  date stamp, so the same string in `modelUsage` may have come from different
  underlying weights at different points in time. This is the most specific
  identifier the envelope offers, and the gap is accepted rather than hidden:
  two verdicts recorded under the same resolved-model string are not
  guaranteed to be comparable at the weight level.
- **A flag rename in the CLI can land silently again.** The invocation this
  ADR replaces was wrong for some time before a live call surfaced it. The one
  `@pytest.mark.integration` canary against the real CLI is the mitigation,
  and it is opt-in, so a CI environment without `claude` installed and
  authenticated will not catch a future rename. Re-verifying flags against a
  new CLI release is manual, and this ADR is what the next re-verification
  has to check against instead of prose.
- **Spend and wall-clock bounds now live on `ClaudeCliJudge`'s constructor,**
  which means a caller that wants different bounds constructs a different
  backend rather than passing different arguments per call.
