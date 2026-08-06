## Why

The judge's filesystem-access vulnerability (SEC-01) is marked resolved but is still live. The prior fix removed `--allowedTools "Read,Grep"` from the `claude -p` argument list, and the test asserts those strings are absent — but omitting `--allowedTools` means *default*, and the default is the full built-in tool set. Verified against the installed CLI with the exact flags `_build_args()` produces today:

```
$ echo "SENTINEL-AGENTLENS-7f3a-CANARY" > /tmp/agentlens_canary.txt
$ claude -p "Read /tmp/agentlens_canary.txt and tell me the string" \
    --output-format json --model sonnet --max-turns 3 --bare
  "result": "The file contains the string: **SENTINEL-AGENTLENS-7f3a-CANARY**"
  "permission_denials": []
```

A prompt-injected transcript can still read any file the user's Claude process can see. ADR 0008 documents a decision that is correct but unenforced, and `docs/agentlens-design.md` §2 still prescribes the vulnerable flags as the "confirmed CLI contract" — so the next contributor re-derives the hole from the design doc.

The root cause generalizes beyond this bug: **asserting that a flag string is absent is not asserting that a capability is absent.** The test could not have caught this.

## What Changes

- The Claude CLI judge passes `--tools ""` — the installed CLI's documented way to disable all built-in tools (`claude --help`: `Use "" to disable all tools`). Verified: the same canary prompt returns `NO_TOOLS`, and `--tools ""` composes with `--json-schema` (structured output still populated).
- The judge pins `--setting-sources "user"`, dropping `project` and `local`. A `.claude/settings.local.json` in whatever directory agentlens happens to run from can no longer reconfigure the judge, making the invocation independent of the caller's working directory.
- The subprocess runs with an explicit `cwd` (a temporary directory) and a filtered environment rather than inheriting agentlens's cwd and full environment. Defense in depth: nothing to reach even if a future change reintroduces a tool.
- **BREAKING (behavior):** `--bare` is retained deliberately — for a reproducible judge context, since inherited hooks/CLAUDE.md/plugin context varies by machine and directory — which means the judge cannot authenticate via OAuth login or keychain. On the CLI's "Not logged in" response the backend raises `JudgeUnavailableError` naming the requirement (`ANTHROPIC_API_KEY` or a configured `apiKeyHelper`) instead of counting the failure toward the consecutive-failure abort. Detection happens on the non-zero-exit path, since an unauthenticated call exits 1 while still emitting a valid JSON envelope. OAuth support is documented as a known limitation in the README and deferred.
- Tests assert the **security property**, not the argument shape: a canary file outside the prepared view, a transcript engineered to request it, and an assertion that its contents never reach the verdict.
- `docs/agentlens-design.md` §2 is corrected (it currently mandates `--allowedTools "Read,Grep" --permission-mode dontAsk`), ADR 0008 is rewritten in place — the decision stands, the mechanism failed — and the README gains a limitations section covering the auth requirement.

## Capabilities

### New Capabilities
<!-- none -->

### Modified Capabilities
- `judge-interface`: the Claude CLI backend requirement gains the concrete mechanism that enforces the no-tools guarantee (`--tools ""`), pinned setting sources, explicit cwd/env isolation, and the fail-loudly auth contract. The "no tools are granted" scenario becomes a capability assertion rather than a string-absence assertion.

## Impact

- Code: `src/agentlens/judge/claude_cli.py` (argument list, subprocess `cwd`/`env`, auth-failure detection and normalization).
- Docs: `docs/agentlens-design.md` §2 (confirmed CLI contract), `docs/adr/0008-judge-invoked-without-tools.md` (rewritten in place), `README.md` (limitations section).
- Specs: `judge-interface` delta.
- Tests: `tests/unit/test_claude_cli.py` (canary property test, arg assertions, auth-failure path).
- Memory: the `agentlens-phase3-cli-flags` note claims `--allowedTools "Read,Grep"` is what enforces read-only — now wrong and actively misleading; it needs correcting when this lands.
- No store schema, rubric, or CLI-surface changes. Verdict comparability and the fix-output shape are handled by the sibling `pin-judge-identity` and `trustworthy-fix-output` changes.
