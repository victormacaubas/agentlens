## 1. Transport contract and deterministic invocation

- [ ] 1.1 Extend `JudgeBackend.score` with the optional output-schema transport input, add the recording/configurable fake in `tests/fakes.py`, and implement pure Claude argv and environment construction in the new judge package. Cover raw and structured requests, exact hardened flags, schema serialization, prompt exclusion from argv, and the `PATH`/`HOME`/`ANTHROPIC_*` environment allowlist; run `make quick`.

## 2. Fail-closed envelope normalization

- [ ] 2.1 Implement raw and structured success-envelope normalization into `JudgeResponse`, deriving the concrete model only from exactly one `modelUsage` key and strictly narrowing optional cost, token, and duration metadata. Cover aliases, missing or multiple models, malformed JSON, non-success envelopes, absent requested result forms, and incompatible metadata types; run `make quick`.

## 3. Isolated process execution

- [ ] 3.1 Implement the concrete Claude CLI backend with prompt input on stdin, an empty temporary cwd, filtered environment, disabled session persistence, and a constructor-configurable timeout. Exercise the real subprocess boundary with a temporary executable fixture, covering stdin/cwd/environment observations, raw and structured success, missing executable, timeout, recognized authentication failure, other non-zero exits, and diagnostic redaction; run `make quick`.

## 4. External contract canary

- [ ] 4.1 Add one opt-in `integration`-marked test against an installed, authenticated Claude CLI that validates structured output, concrete model resolution, the bare/user-settings authentication path, disabled tools, and no session persistence without entering the default quality gate; run `make quick`.

## 5. Change verification

- [ ] 5.1 Run `graphify update .`, strict OpenSpec validation for `add-isolated-claude-judge-backend`, and the single merge-boundary `make check`; reconcile the artifacts if implementation exposes a contract mismatch.
