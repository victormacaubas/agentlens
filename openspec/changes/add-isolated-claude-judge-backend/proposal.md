## Why

Phase 3 cannot safely call a paid, nondeterministic judge until the Claude CLI is isolated behind the existing `JudgeBackend` seam and its security-sensitive invocation contract is executable and testable. Establishing that boundary first closes transport and envelope uncertainties without coupling them to the still-open rubric, persistence, or report design.

## What Changes

- Add a Claude CLI judge backend that invokes one prepared prompt through a bounded, non-interactive, tool-free process.
- Return a transport-level `JudgeResponse` containing the untrusted raw result or structured output, concrete model identity, usage, cost, and duration metadata.
- Translate missing CLI, authentication, process, malformed-envelope, and judge-reported failures into the existing judge error taxonomy.
- Add a deterministic fake for the existing `JudgeBackend` seam so later scoring orchestration never invokes the paid backend in unit tests.
- Add an opt-in integration canary for the installed Claude CLI contract; the normal quality gate remains free and offline.
- Exclude rubric content, prompt preparation, verdict validation, `fact_verdict`, cache claims, scoring orchestration, and CLI/report exposure from this change.

## Capabilities

### New Capabilities

- `claude-judge-backend`: Isolated, tool-free Claude CLI execution and raw response-envelope normalization behind `JudgeBackend`.

### Modified Capabilities

None.

## Impact

- Adds the first implementation under the existing `agentlens.judge` layer and the matching fake under `tests`.
- Uses only the standard library and the already-installed external `claude` executable; no runtime dependency is added.
- Preserves the existing `JudgeBackend`, `JudgeResponse`, and judge error contracts unless the detailed design identifies a documented incompatibility.
- Does not depend on completion of the active deterministic reporting change and does not expose new user-facing CLI behavior.
