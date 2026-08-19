## Context

The architecture already declares `JudgeBackend` as the paid, nondeterministic seam, `JudgeResponse` as its untrusted transport result, and the `JudgeError` family as its package boundary. No concrete `agentlens.judge` package or fake exists yet, and nothing consumes the Protocol.

The installed Claude CLI is 2.1.236. Its current help documents `--print`, `--output-format json`, `--json-schema`, `--model`, `--max-turns`, `--bare`, `--tools ""`, `--setting-sources`, and `--no-session-persistence`. Bare mode removes automatic project customizations and keychain reads but does not itself remove built-in tools, so `--tools ""` remains the enforcing control. Official documentation exposes the resolved model through `modelUsage` in a single JSON result and documents a 10 MB standard-input limit.

ADR 0004 rejects a second subprocess seam: command construction is pure and directly tested, while execution remains a thin wrapper with a real opt-in canary. The judge package must translate process and JSON failures at its boundary, and no subprocess-shaped type may escape it.

## Goals / Non-Goals

**Goals:**

- Make the process boundary deterministic except for the external Claude call.
- Make every isolation control inspectable through pure command/environment construction.
- Normalize only the response envelope and preserve verdict content as untrusted data.
- Give later orchestration a no-cost fake that records calls and returns configured transport responses or errors.

**Non-Goals:**

- Define rubric instructions, verdict semantics, score validation, prompt reduction, or provenance.
- Persist verdicts, compute cache identities, coordinate concurrent claims, or select comparable cohorts.
- Add a user-facing scoring command or instantiate the concrete backend in production composition.
- Support OAuth/keychain authentication, third-party-provider environment variables, proxies, or MCP tools in this first backend.

## Decisions

### Extend the transport Protocol with an optional output schema

`JudgeBackend.score` will accept an optional keyword-only `output_schema: Mapping[str, object] | None`. The concrete backend serializes it for `--json-schema`; the fake records it unchanged. The backend still does not validate verdict meaning.

This is necessary because `JudgeResponse` already models `structured_output`, but the current Protocol provides no way to request it. The Protocol has no consumers, so correcting the transport contract now has no compatibility cost.

Alternative considered: hard-code the Phase 3 verdict schema in the Claude backend. Rejected because rubric and verdict validation belong above the transport and are explicitly outside this change.

### Keep construction pure and execution concrete

The judge package will expose pure internal construction and normalization functions plus one concrete Claude CLI backend. Command construction receives the executable, model, and optional schema; environment construction filters a supplied mapping. The backend owns temporary-directory creation and `subprocess` execution.

The concrete backend will accept an executable name and timeout at construction, with production-oriented defaults of `claude` and 300 seconds. This supports deterministic subprocess-fixture tests without introducing a second Protocol or allowing subprocess types to escape the judge package.

Alternative considered: inject a command runner. Rejected by ADR 0004 because it duplicates the seam and makes tests prove the fake runner rather than the actual process boundary.

### Send the prompt on standard input

The invocation will use:

- `--print`
- `--output-format json`
- `--model <requested-model>`
- `--max-turns 3`
- `--bare`
- `--tools ""`
- `--setting-sources user`
- `--no-session-persistence`
- `--json-schema <serialized-schema>` only when requested

The prepared prompt is sent through standard input, not placed in argv. Execution uses a newly created empty temporary directory and an environment containing only `PATH`, `HOME`, and keys beginning with `ANTHROPIC_`.

This avoids OS argument-length limits and process-list disclosure, prevents Claude session-log writes, and keeps project-local settings, hooks, plugins, skills, MCP configuration, and instructions outside the run.

Alternative considered: pass the prompt positionally as shown in the original design sketch. Rejected because standard input is documented for print mode, has an explicit limit, and exposes less transcript content to the host.

### Fail closed on result-envelope shape

Normalization requires a JSON object with result type, success subtype, `is_error` false, and exactly one key in `modelUsage`. That key is the concrete `resolved_model`; the requested alias is never used as a fallback.

For a schema request, `structured_output` must be an object. Without a schema request, `result` must be a string. Optional cost, usage, and duration fields are copied only after strict type checks; booleans do not count as integers. Domain fields inside raw or structured verdict content are not inspected.

Any non-success subtype, malformed JSON, absent result form, missing or ambiguous model identity, or incompatible metadata raises `JudgeResponseError`.

Alternative considered: accept the requested alias when `modelUsage` is absent or choose the first model when several appear. Rejected because either behavior can merge incomparable verdict cohorts.

### Translate process failures without leaking prompts

Failure translation follows the existing taxonomy:

- executable startup failures, timeouts, and recognized missing-auth responses become `JudgeUnavailableError`;
- other non-zero exits, malformed envelopes, and judge-reported failures become `JudgeResponseError`.

Diagnostics may include the exit status and a bounded, sanitized CLI error summary. They must never include stdin, argv values containing user content, environment values, or raw transcript text. Authentication classification is deliberately narrow and based on documented CLI messages; unknown failures fail as response errors rather than being guessed unavailable.

Alternative considered: return a `JudgeResponse` with `is_error=True`. Rejected because callers could accidentally persist or interpret an unusable response as a verdict; failures cross the boundary as typed exceptions.

### Test the real boundary at two levels

Normal tests use pure builder/normalizer assertions and a temporary executable fixture that behaves like a CLI process. This exercises stdin, cwd, filtered environment, exit handling, timeout handling, and envelope normalization without calling a model.

One `integration`-marked canary invokes the installed, authenticated Claude CLI and verifies the current envelope, resolved-model, structured-output, and tool-free assumptions. It remains outside `make check` because it needs auth and incurs judge cost.

The seam fake belongs in `tests/fakes.py`, records each prompt/model/schema call, and returns configured responses or raises configured judge errors. It never constructs the real backend.

## Risks / Trade-offs

- **[Claude CLI flags or envelope fields drift]** → Keep construction assertions exact and retain one opt-in real-CLI canary; a canary failure blocks Phase 3 integration until the contract is re-verified.
- **[`--bare --setting-sources user` authentication differs across installations]** → Treat authentication failure as unavailable and cover the supported API-key/apiKeyHelper path in the opt-in canary.
- **[The strict environment breaks proxy or third-party-provider setups]** → Keep those environments outside this first backend rather than weakening isolation silently; a later change can add an explicit backend/configuration contract.
- **[A prepared prompt exceeds Claude's 10 MB stdin limit]** → Surface the CLI failure as a judge-response error; prompt budgeting belongs to the later preparation slice.
- **[`modelUsage` contains multiple models after a future CLI change]** → Fail closed so persistence cannot label a mixed response with an arbitrary model.
- **[A 300-second timeout is too short for unusually large prompts]** → Keep the concrete timeout constructor-configurable while leaving user-facing timeout configuration to later orchestration.

## Migration Plan

There is no existing judge implementation or stored verdict data to migrate. The change adds an unused concrete backend and extends an unused Protocol. Rollback removes the new judge package, fake, tests, and optional Protocol parameter without changing deterministic ingest or reporting data.
