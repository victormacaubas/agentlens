## Context

`ClaudeCliJudge.score()` passes the prepared transcript view to `claude -p` via stdin. That view is attacker-influenceable: it contains the task text, tool arguments, error excerpts, and the agent's final report — any of which may derive from a repo, issue, or file the analyzed subagent read.

A prior change (`2026-07-13-harden-judge-security-and-scoring`) addressed this by removing `--permission-mode dontAsk` and `--allowedTools "Read,Grep"` from the argument list, recording the decision in ADR 0008, and asserting in `tests/unit/test_claude_cli.py::test_build_args_grants_no_filesystem_tools` that those strings are absent. The decision was right. The mechanism was not: **omitting `--allowedTools` selects the CLI's default, which is the full built-in tool set.**

Verified against the installed CLI using exactly the flags `_build_args()` produces today, with a canary file:

| flags | canary read? |
|---|---|
| `--output-format json --model sonnet --max-turns 3 --bare` (today) | **yes** — returned the sentinel string, `permission_denials: []` |
| same, plus `--tools ""` | no — returned `NO_TOOLS` |

`--tools ""` also composes with `--json-schema`: a probe with both returned a populated `structured_output`. So the fix costs nothing in capability or output quality.

Two adjacent exposures surface once the tool question is settled. `_build_args()` produces no `cwd` and no `env`, so the subprocess inherits agentlens's working directory and full environment. And `--setting-sources` is unpinned, so settings files discovered from that inherited cwd — including a `.claude/settings.local.json` in an untrusted repo — participate in the judge call.

Auth is entangled with all of this. `--bare`'s help states that Anthropic auth is strictly `ANTHROPIC_API_KEY` or `apiKeyHelper` via `--settings`, and that OAuth and keychain are never read. Probes with the environment stripped to `PATH` and `HOME`:

| flags | outcome |
|---|---|
| `--bare --setting-sources "user"` | ✅ |
| `--bare --setting-sources ""` | ❌ `Not logged in · Please run /login` |
| no `--bare`, `--setting-sources "user"` | ✅ |
| no `--bare`, `--setting-sources ""` | ❌ `Not logged in · Please run /login` |

An earlier draft of this table carried per-session costs ($0.0006 bare vs $0.063 non-bare). Those figures were measured against a trivial one-line prompt, not a prepared transcript view, so they understate a real judge call by roughly two orders of magnitude and are not a sound basis for any decision. They have been removed rather than corrected: cost belongs to `pin-judge-identity`, which owns the estimate table and re-measures it against a realistic view. The `--bare` decision below stands on reproducibility, which does not depend on a cost figure.

Two conclusions. First, `--setting-sources ""` is unavailable to us — settings are the only auth channel `--bare` accepts. Second, this development machine authenticates via an `ANTHROPIC_AUTH_TOKEN` injected by `~/.claude/settings.json`'s `env` block (a gateway setup); the OAuth path was **never exercised** by any probe. On a stock OAuth-only machine, `agentlens score` fails at the first judge call today. `docs/agentlens-design.md` §2's claim that headless mode "uses the user's existing login by default — no key needed" is false under `--bare`.

## Goals / Non-Goals

**Goals:**

- A prompt-injected transcript cannot cause any filesystem read, shell execution, or side effect beyond the model's text output — enforced by the argument list, not by prompt wording.
- The judge invocation is independent of the working directory agentlens runs from.
- The security guarantee is verified by a test that asserts the *property* (a canary file's contents never reach the verdict), not the presence or absence of a flag string.
- An unauthenticated environment produces one clear, actionable error rather than three opaque judge failures and an abort.
- The design doc, ADR 0008, and README agree with the code, so the vulnerability cannot be re-derived from stale documentation.

**Non-Goals:**

- **OAuth/keychain auth support.** Deliberately out of scope; documented as a known limitation. The alternative (dropping `--bare`) costs ~100× per call and makes the judge's system context machine-dependent — see Decisions.
- **Transcript delimiting or injection-neutralization in the prepared view.** Once tools are gone, injection's remaining reach is score manipulation, which is a data-integrity concern rather than a disclosure one. Explicitly parked (thread B in exploration); no red-team fixture corpus in this change.
- **Verdict comparability and the cache key.** Handled by `pin-judge-identity`.
- **The shape of `suggested_fixes` and the downstream handoff boundary.** Handled by `trustworthy-fix-output`.
- Re-deriving `_estimate_judge_cost` from the measured `--bare` figures — belongs with `pin-judge-identity`, which touches cost anyway.

## Decisions

### D1: Disable tools with `--tools ""` rather than an empty `--allowedTools`

`--tools` is the installed CLI's documented switch for selecting from the built-in set, and `""` is its documented "disable all tools" value. `--allowedTools ""` would express a permission grant over a tool set that is still loaded; `--tools ""` removes the tools themselves. The empirical difference is decisive: today's argument list reads the canary, and adding `--tools ""` returns `NO_TOOLS`.

*Alternatives considered.* `--disallowedTools "Read,Grep,Bash,…"` — rejected: an enumeration is a denylist that silently fails open when the CLI gains a tool. `--permission-mode plan` — rejected: it constrains writes, not reads, and the disclosure vector is reads. Prompt-only mitigation — rejected as the primary control; retained as secondary (ADR 0008's existing position).

### D2: Pin `--setting-sources "user"`

Dropping `project` and `local` removes the sharpest remaining edge: a settings file in an untrusted repo reconfiguring the judge. `user` must be retained because it is the only surviving auth channel under `--bare` (probe table above). This also makes the invocation cwd-independent, which is a precondition for D3 being meaningful.

*Alternative considered.* `--setting-sources ""` for full isolation — empirically impossible alongside `--bare`; it produced `Not logged in`.

### D3: Explicit `cwd` (temporary directory) and filtered `env`

Pass a temporary directory as the subprocess `cwd` and construct `env` explicitly rather than inheriting. The filtered env forwards only what auth and model routing require (`PATH`, `HOME`, and the `ANTHROPIC_*` variables) and drops the rest.

This is defense in depth with no behavioral cost today — with `--tools ""` there is nothing to aim at a directory. Its value is durability: it is the control that holds if a future change reintroduces a tool, whether deliberately or by regression. Note the ordering dependency — the env filter is only safe *because* D2 keeps auth flowing through user settings; on a machine that relies on an exported `ANTHROPIC_AUTH_TOKEN`, forwarding `ANTHROPIC_*` preserves that path too.

*Alternative considered.* A full OS sandbox (`sandbox-exec`, container) — rejected as disproportionate for a text-generation call with no tools, and platform-specific.

### D4: Keep `--bare`; fail loudly on missing auth

`--bare` is retained for a reason that does not depend on any cost measurement: **reproducibility.** A non-bare call loads hooks, CLAUDE.md, plugin context, and auto-memory into the judge's system context, and that inherited context varies by machine and by working directory. A non-bare verdict is therefore graded by a materially different judge than a bare one, which corrupts exactly the cross-run comparability `report` exists to provide. This is the load-bearing argument.

`--bare` is also cheaper, since the inherited context is thousands of tokens on every call — but no multiplier is claimed here. The figures a previous draft cited were measured against a trivial prompt (see Context) and are not trustworthy at the order-of-magnitude level. The magnitude of the saving is `pin-judge-identity`'s to establish; this change only needs the direction, which is not in doubt.

The cost is that OAuth-only users cannot run the judge. Rather than surface that as three opaque judge failures and a consecutive-failure abort, the backend detects the CLI's `Not logged in` response and raises `JudgeUnavailableError` with the remedy named: set `ANTHROPIC_API_KEY` or configure `apiKeyHelper`. `JudgeUnavailableError` already propagates as a hard failure rather than a per-session skip, which is the correct semantics — this is an environment problem, not a bad session.

**Where the detection has to live.** An unauthenticated call was probed directly, with the environment stripped to `PATH` and `HOME`:

```
exit code: 1
stderr:    (empty)
stdout:    {"is_error":true,"subtype":"success","result":"Not logged in · Please run /login",
            "total_cost_usd":0,"usage":{...},"modelUsage":{},...}
```

Two details decide the implementation. The exit code is **non-zero**, and `score()` currently raises `JudgeError` on `returncode != 0` (`claude_cli.py:58-62`) *before* `_parse_envelope` is ever reached — so a check against the envelope's `result` on the `is_error` branch would be dead code. But stdout is nonetheless **valid JSON** carrying the message, so the information is available on the failing branch.

The detection therefore happens on the non-zero-exit path: attempt to parse stdout as an envelope, and if that yields a not-logged-in `result`, raise `JudgeUnavailableError`; otherwise fall through to today's `JudgeError`. Parsing must be tolerant, since a non-zero exit with unparseable stdout is a legitimate different failure and must keep its current behavior. Matching stays a loose case-insensitive substring (`not logged in`) rather than the full string with its `·` separator.

*Alternatives considered.* Drop `--bare` unconditionally — rejected on the cost and reproducibility grounds above. A `--judge-auth oauth` opt-in flag — deferred: the OAuth path is unverified on any machine available here, so shipping a flag that promises support would risk promising behavior we have not observed; and it would require deciding whether auth mode enters the verdict identity, which belongs with `pin-judge-identity`. Documented as a limitation with a follow-up instead.

### D5: Test the capability, not the argument string

The existing `test_build_args_grants_no_filesystem_tools` asserts `"--allowedTools" not in args`, `"Read" not in args`, and so on. That assertion passes against a vulnerable argument list, which is how this bug survived. Replace it with a test that writes a canary file outside the prepared view, feeds a transcript view containing an explicit instruction to read it, and asserts the canary's contents appear nowhere in the resulting verdict (dimension evidence or suggested fixes).

The string-level assertions are kept as a fast complement, with `--tools ""` asserted positively — but they are no longer the guarantee. This lesson is recorded in the rewritten ADR 0008 because it generalizes past this bug.

Two limits are worth stating rather than glossing. First, the canary test carries an `integration` marker and is excluded from the default `pytest` run (forced by ADR 0001 and by the fact that it spends money), so the *routine* quality gate is still a string assertion — a positive one on `--tools ""`, which is stronger than an assertion of absence, but string-level all the same. The security property is enforced by a test somebody has to deliberately run; the tasks make that an explicit, named step rather than a good intention.

Second, a test that passes proves nothing unless it can fail. The canary test is therefore paired with a **negative control**: the same test is run once with `--tools ""` removed and must fail. That run is recorded as its own task with an observable outcome, not folded into a "confirm the test works" instruction, because an unexercised negative control is exactly how the current `test_build_args_grants_no_filesystem_tools` came to pass against a vulnerable argument list.

### D6: Rewrite ADR 0008 in place rather than superseding it

The decision ADR 0008 records — the judge is invoked with no tools — was correct and still stands. What failed was the mechanism it described and the test that was believed to enforce it. A superseding ADR would signal that the decision changed, which would misrepresent the history. The rewrite keeps Status: Accepted, corrects the Decision section to name `--tools ""`, and adds to Consequences the generalizable lesson: a test asserting that a flag string is absent does not assert that a capability is absent, so security properties must be verified behaviorally.

## Risks / Trade-offs

- **OAuth-only users cannot run `agentlens score`.** → Named limitation in the README with the exact remedy; `JudgeUnavailableError` states it at the point of failure rather than letting the user infer it. Follow-up work tracked separately.
- **The `Not logged in` detection depends on a CLI message string.** A future CLI wording change would silently reclassify the failure as a generic `JudgeError`. → Matching is treated as a best-effort refinement over an error path that already fails safely (the user still gets a `JudgeError` naming the envelope text); the check is written to be loose (case-insensitive substring on `not logged in`) and covered by a unit test with a recorded envelope so the coupling is visible.
- **The detection sits on the non-zero-exit path, which is also where genuinely unparseable failures land.** → The envelope parse there is tolerant by construction: any failure to parse, or a parsed envelope without the not-logged-in marker, falls through to today's `JudgeError` unchanged. A unit test covers the fall-through with non-JSON stdout so the refinement cannot swallow an unrelated non-zero exit.
- **`--tools ""` is likewise a CLI contract that may shift.** → The canary property test (D5) fails loudly if a CLI upgrade re-enables tools, which is precisely the regression the current test cannot see.
- **The env filter could break auth on an unanticipated setup.** → `ANTHROPIC_*` is forwarded as a prefix rather than an enumerated list, covering `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, and `ANTHROPIC_BASE_URL` (gateway routing) without needing to know which one a given machine uses.
- **The canary test invokes the real `claude` CLI, which conflicts with synthetic-only tests (ADR 0001) and costs money per run.** → The property test is written as an integration test under `tests/integration/` with a `@pytest.mark.integration` marker, excluded from the default run, alongside a unit-level test that asserts `--tools ""` is present in the argument list. ADR 0001's synthetic-only guarantee for `tests/unit/` is preserved. This is the first inhabitant of `tests/integration/`, which the project's test-structure notes already anticipate.

## Migration Plan

No data migration. The store currently holds zero verdicts and zero sessions, so no verdict is invalidated and nothing needs re-scoring. Rollback is reverting the argument-list change; the documentation corrections are independently safe to keep.

Sequencing note: this change and `pin-judge-identity` both touch `_build_args()`. Land this one first — the hole is live and proven — and `pin-judge-identity` rebases onto it trivially.

## Open Questions

- Does the OAuth path work at all on a stock machine? Unverified here because this machine's gateway token short-circuits it. Worth confirming before any future `--judge-auth oauth` work, and it needs a machine without the settings `env` token.
- Should the filtered env forward `NO_COLOR` / `TERM` to keep the CLI's output stable in unusual terminals? Not observed to matter in probes; left out until it does.
