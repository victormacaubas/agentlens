## 1. Close the capability hole

- [x] 1.1 Add `--tools ""` to `_build_args()` in `src/agentlens/judge/claude_cli.py`, with a module constant for the empty tool set and a comment naming why omitting `--allowedTools` is not equivalent (D1)
- [x] 1.2 Add `--setting-sources "user"` to `_build_args()`, with a comment recording that `""` is impossible under `--bare` because settings are the only auth channel (D2)
- [x] 1.3 Update the unit test in `tests/unit/test_claude_cli.py` to assert positively that `--tools` is present with an empty value and `--setting-sources` is `user`, keeping the existing negative assertions as a fast complement (D5)

## 2. Isolate the subprocess

- [x] 2.1 Pass an explicit temporary directory as the subprocess `cwd` in `score()`, created and cleaned up per call (D3)
- [x] 2.2 Build the subprocess `env` explicitly — forward `PATH`, `HOME`, and any `ANTHROPIC_*` variable by prefix; drop everything else (D3)
- [x] 2.3 Add unit tests asserting `subprocess.run` receives a `cwd` that is not agentlens's cwd, and an `env` that omits an unrelated sentinel variable while preserving `ANTHROPIC_*`

## 3. Fail loudly on missing auth

- [x] 3.1 Detect the CLI's not-logged-in response on the **non-zero-exit path** in `score()` — an unauthenticated call exits 1 with valid JSON on stdout, so the existing `returncode != 0` guard fires before `_parse_envelope` and a check on the `is_error` branch alone would be dead code. Tolerantly parse stdout there, match a case-insensitive `not logged in` substring on `result`, and raise `JudgeUnavailableError` naming `ANTHROPIC_API_KEY` or `apiKeyHelper` as the remedy (D4)
- [x] 3.2 Add a unit test using the recorded not-logged-in envelope (exit code 1, `is_error: true`, `result: "Not logged in · Please run /login"`) asserting `JudgeUnavailableError` (not `JudgeError`) is raised, so the loop's consecutive-failure counter is not consumed
- [x] 3.3 Add a unit test for the fall-through: a non-zero exit with non-JSON stdout still raises `JudgeError` with its message intact, so the new detection cannot swallow an unrelated failure
- [x] 3.4 Verify `JudgeUnavailableError` propagates out of `ScoringLoop.run` as a hard failure rather than a per-session skip; add a test if the current behavior does not already guarantee it
      - **Design defect found and fixed.** D4's premise was wrong: `JudgeUnavailableError` subclasses `JudgeError` (`errors.py:20`), so `except JudgeError` (`scoring.py:141`) swallowed it into a per-session skip, contradicting the spec scenario. Fixed with an `except JudgeUnavailableError: raise` clause ahead of the broad catch (hierarchy left alone). Covered by `test_judge_unavailable_error_propagates_as_hard_failure`.

## 4. Prove the security property

- [x] 4.1 Create `tests/integration/` with `__init__.py` and register the `integration` marker in `pyproject.toml`, excluded from the default run (preserves ADR 0001 for `tests/unit/`)
- [x] 4.2 Write the canary integration test: a sentinel file outside the prepared view, a transcript view instructing the judge to read it, and an assertion that the sentinel content appears in no dimension evidence and no suggested fix
      - Written as specified, then **rewritten** after 4.3's control showed the sentinel-absence assertion could not fail. The sentinel check survives as a secondary defense-in-depth assertion; the primary guarantee is now the tool inventory. See 4.3.
- [x] 4.3 **Negative control** — run the canary test once with `--tools ""` removed from `_build_args()` and confirm it FAILS, then restore the flag. Record the observed failure in the change's completion notes, so the control is demonstrably exercised rather than assumed (D5)

**First attempt: the control did NOT fail, and the original test was replaced as a result.**

The sentinel-absence test from 4.2 **passed with `--tools ""` removed** (53.98s). Diagnosis: the judge *declined* the injection rather than lacking the capability. Its evidence read *"The Task field contains an instruction to read a canary file, not a legitimate subagent task."* No tool call attempted, `permission_denials: []`, sentinel absent from stdout. A second, deliberately plausible framing (injection disguised as a rollout-note verification inside `## Final report`) showed **no behavioral difference** between conditions either. That test measured the model's disposition to comply, not the absence of a tool: the same failure mode ADR 0008 documents one layer up.

**Test rewritten onto a capability observable.** `--output-format stream-json --verbose` emits a `system`/`init` event whose `tools` field reports the inventory the session actually loaded, independent of model behavior. Probed against CLI 2.1.221 with the judge's real argument list (rubric + verdict schema):

| condition | `init.tools` |
|---|---|
| with `--tools ""` | `['StructuredOutput']` |
| without | `['Bash', 'Edit', 'Read', 'StructuredOutput']` |

`StructuredOutput` is the schema's own output mechanism and grants no filesystem/shell access, so the assertion is an **allowlist** (`tool_set <= {"StructuredOutput"}`), which fails closed if the CLI ever gains a tool. A denylist would silently fail open, which is the shape of the original bug. Production keeps `--output-format json`; the probe derives its args from the real `_build_args()` and swaps only the output format, so it cannot drift from production. Test renamed `test_no_tools_flag_loads_no_filesystem_or_shell_tool`.

**Negative control, run in two layers.** The implementer's run failed at the *args-list guard* (`test_claude_cli_canary.py:153`, `assert "--tools" in args`) — a string assertion that short-circuits before the CLI is ever invoked. That proves the guard works, not that the observable does. I therefore re-ran it with the flag removed **and the guard neutralized**, so the CLI observable itself was the thing under test:

```
>       assert tool_set <= _ALLOWED_TOOLS
E       AssertionError: tool inventory contains tools outside the allowed set: {'Read', 'Edit', 'Bash'}
E       assert {'Bash', 'Edit', 'Read', 'StructuredOutput'} <= frozenset({'StructuredOutput'})
tests/integration/test_claude_cli_canary.py:229: AssertionError
1 failed, 187 deselected in 54.65s
```

Both layers are now demonstrably able to fail. Flag and guard restored (`claude_cli.py:134`, test line 153 verified present); `pytest -m integration` green again (46.81s), no experiment residue.

## 5. Align documentation with the code

- [x] 5.1 Rewrite `docs/adr/0008-judge-invoked-without-tools.md` in place — keep Status: Accepted, correct Decision to name `--tools ""`, and add to Consequences the lesson that asserting a flag string's absence does not assert a capability's absence (D6)
- [x] 5.2 Correct `docs/agentlens-design.md` §2: replace the `--allowedTools "Read,Grep" --permission-mode dontAsk` invocation with the hardened one, and fix the auth claim that headless mode uses the user's existing login by default (false under `--bare`)
- [x] 5.3 Add a Limitations section to `README.md` stating that the judge requires `ANTHROPIC_API_KEY` or a configured `apiKeyHelper`, and that OAuth/keychain login is not supported
- [x] 5.4 Correct the `agentlens-phase3-cli-flags` memory note, which claims `--allowedTools "Read,Grep"` is what enforces read-only
      - Rewritten against fresh probes; also added a `flag-absence-is-not-capability-absence` note for the generalizable lesson.

## 6. Quality gate

- [x] 6.1 `uv run pytest` green (default run excludes the integration marker) — 187 passed, 1 deselected
- [x] 6.2 `uv run pytest -m integration` green against the real CLI, run once deliberately — this is the only run that exercises the security property, so it is a required step, not an optional extra
      - Green (46.81s) against the rewritten test, whose negative control is demonstrably able to fail (4.3). The earlier 28.50s pass was against the superseded sentinel-only test and did not establish the property.
- [x] 6.3 `uv run ruff check` and `uv run mypy` green — ruff clean; mypy clean across 45 source files
- [x] 6.4 `openspec validate harden-judge-invocation --strict` passes — "Change 'harden-judge-invocation' is valid"
- [x] 6.5 Re-confirm at merge time that `fact_verdict` is still empty (`sqlite3 ~/.cache/agentlens/agentlens.db "select count(*) from fact_verdict"`) — the "no migration needed" claim in the Migration Plan was verified when this change was drafted and should not be trusted on age alone
      - Re-confirmed 2026-08-04: `fact_verdict` = 0, `fact_session` = 0. No migration needed.

## Orchestration notes (2026-08-04)

**Pre-flight probes against CLI 2.1.221** (the design's probes were a week old and worth re-running):

- The vulnerability was **live**: today's pre-fix `_build_args()` output read a canary file and returned its contents, `permission_denials: []`.
- `--tools ""` blocks it (`NO_TOOLS` reply) and composes with `--json-schema` (`structured_output` populated).
- The full hardened argv, with a tempdir `cwd` and env filtered to `PATH`/`HOME`/`ANTHROPIC_*`, works end to end. `--tools` being variadic is not a problem when a flag follows the empty value.
- Recorded the real not-logged-in envelope (exit 1, empty stderr, valid JSON, `·` U+00B7 in `result`) so 3.2 asserts a real shape.

**All tasks complete.** The hardening (sections 1-3) is verified by unit tests and direct probe, and the security property is now guarded by a test whose negative control has been observed failing at the CLI observable, not merely at a string guard.

**Lesson worth carrying past this change** (candidate for ADR 0008's Consequences, and already recorded there in substance): this change's *own* first test repeated the exact mistake it was written to fix. The sentinel-absence test passed against a vulnerable argument list because it measured the model's disposition rather than the CLI's loaded capability. Two layers deep, the same rule held: assert on what the system reports it loaded, not on what it happened to do. When a negative control fails, check *which* assertion caught it — a control that trips on a cheap string guard before reaching the real observable has not exercised the observable at all.

**Follow-up not blocking archive:** the `--tools ""` / `init.tools` coupling is a CLI contract. If a future CLI renames `StructuredOutput` or restructures the init event, the allowlist fails closed (correct, but the message will point at the allowlist rather than the rename). Worth a note in whichever change next touches `_build_args()` — `pin-judge-identity` rebases onto this one.
