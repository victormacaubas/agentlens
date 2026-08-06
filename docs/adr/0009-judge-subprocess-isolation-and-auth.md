# 10. Judge subprocess runs `--bare` with pinned settings, an isolated cwd/env, and loud failure on missing auth

Status: Accepted

## Context

`harden-judge-invocation` ([ADR 0008](0008-judge-invoked-without-tools.md)) closed the capability hole that let a prompt-injected transcript read arbitrary files, by removing the judge's tools (`--tools ""`). Three further exposures surfaced once the tool question was settled, all about the *environment* the subprocess runs in rather than what it can do with tools:

- **Settings.** `_build_args()` left `--setting-sources` unpinned, so a `.claude/settings.local.json` discovered from whatever directory agentlens happens to run in — including a directory inside an untrusted repo the analyzed subagent touched — could reconfigure the judge call (model, permission mode, anything a settings file can set).
- **Process isolation.** `_build_args()` produced no `cwd` and no `env`, so the subprocess inherited agentlens's own working directory and full environment — an unnecessarily wide surface for a call whose only legitimate input is the prepared transcript view on stdin.
- **Auth.** `--bare`'s documented auth model is strictly `ANTHROPIC_API_KEY` or an `apiKeyHelper` configured via settings — OAuth and keychain credentials are never read under `--bare`. Probing confirmed `--setting-sources ""` (full isolation) is incompatible with `--bare`: settings are `--bare`'s only remaining auth channel. An OAuth-only user therefore hits a hard wall the first time `agentlens score` calls the judge, and the design doc's claim that headless mode "uses the user's existing login by default" is false under `--bare`.

`--bare` itself was kept rather than dropped, for a reason independent of any cost figure: a non-bare call loads hooks, `CLAUDE.md`, plugin context, and auto-memory into the judge's system context, and that inherited context varies by machine and by working directory. A non-bare verdict would be graded by a materially different judge than a bare one on someone else's machine, which corrupts the cross-run comparability `report` depends on — the same property [ADR 0009](0009-verdict-comparability.md) formalizes as the judge-context leg of verdict comparability.

## Decision

The judge subprocess is invoked with:

- **`--setting-sources user`** — narrower than the CLI default (which also reads `project` and `local`), because `user` is the only setting source that is both under the invoker's control and compatible with `--bare`'s auth requirement. This also makes the invocation independent of the working directory agentlens runs from.
- **An explicit temporary directory as `cwd`**, created and cleaned up per call, rather than agentlens's own working directory.
- **An explicitly constructed `env`** that forwards only `PATH`, `HOME`, and any variable prefixed `ANTHROPIC_*` (covering `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, and gateway-routing variables such as `ANTHROPIC_BASE_URL`), dropping everything else the parent process's environment carries.
- **`--bare`, retained** for judge-context reproducibility, not primarily for cost — the cost saving is real but is not the load-bearing argument.
- **Loud, specific failure on missing auth.** When the CLI's response indicates it is not logged in (a case-insensitive `not logged in` substring in the envelope's `result`, checked tolerantly on the non-zero-exit path, since an unauthenticated call exits non-zero while still emitting a valid JSON envelope), the backend raises `JudgeUnavailableError` naming the remedy (`ANTHROPIC_API_KEY` or `apiKeyHelper`) rather than letting it surface as an opaque `JudgeError` that the scoring loop counts toward its consecutive-failure abort.

These are ordered dependencies, not independent knobs: the `env` filter is only safe to apply *because* pinning to `user` settings keeps whichever auth channel a given machine relies on (including a gateway token injected via `settings.json`'s `env` block) flowing through. Narrowing settings without narrowing `env` first would have been a smaller, incomplete fix.

*Alternatives considered.* `--setting-sources ""` for full isolation — empirically impossible alongside `--bare` (produces `Not logged in`). A full OS sandbox (`sandbox-exec`, a container) for process isolation — rejected as disproportionate for a tool-less text-generation call and platform-specific. Dropping `--bare` unconditionally to support OAuth-only users — rejected on the reproducibility grounds above; documented as a known limitation instead. A `--judge-auth oauth` opt-in flag — deferred, because the OAuth path has never been exercised by any probe on any available machine, and shipping a flag would imply a guarantee not yet observed to hold.

## Consequences

- The judge invocation cannot be reconfigured by a settings file discovered from an untrusted repo, or from whatever directory agentlens happens to run in.
- OAuth-only users cannot run `agentlens score` — a named limitation (README), not a silent failure: `JudgeUnavailableError` states the exact remedy at the point of failure.
- The env filter forwards `ANTHROPIC_*` as a prefix rather than an enumerated list, so it does not need to know in advance which specific variable a given machine's auth setup uses.
- `--bare` is now a documented reproducibility requirement, not merely a cost optimization — a future change that drops it must reckon with judge-context comparability ([ADR 0009](0009-verdict-comparability.md)), not just cost.
- `JudgeUnavailableError` must keep propagating as a hard failure (not a per-session skip) for this decision to hold: an environment problem should stop a scoring run, not silently consume the run's failure budget one session at a time.
- The not-logged-in detection depends on matching a CLI message string (`not logged in`) on a specific path (non-zero exit, parseable stdout). A future CLI wording change would silently reclassify the failure as a generic `JudgeError` rather than `JudgeUnavailableError` — a narrower, already-safe degradation, since the user still receives a `JudgeError` naming the envelope text.
- Complements [ADR 0008](0008-judge-invoked-without-tools.md) (no tools for the judge) and [ADR 0009](0009-verdict-comparability.md) (verdict comparability, which cites this ADR's `--bare` + pinned settings as what holds judge context fixed).
