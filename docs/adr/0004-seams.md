# 0004. Seams

## Status

Accepted

## Context

A seam is a boundary where a real implementation can be swapped for a test
double. Too few and the suite fills with `@patch("agentlens.judge.claude_cli...")`
decorators that couple tests to internal import paths and seize up on any
rename. Too many and every reader walks through indirection to reach the one path
that ever runs.

agentlens has one obviously expensive dependency (an LLM judge that spawns a
process and costs money) and several plausible-looking candidates that turn out
not to earn it.

## Decision

**The rule that decides membership:** a dependency is a seam if it crosses a
process boundary, is slow, paid, or nondeterministic, **or** if the only way to
test around it would be to patch a module path. The list below is derived from
that rule; future candidates get judged by the rule, not by comparison to the
list.

**Seams, declared as Protocols in `models/protocols.py`:**

- **`JudgeBackend`**: spawns `claude -p` as a subprocess, costs money, and
  returns nondeterministic output. It is also the design doc's explicit
  pluggability point: an `ANTHROPIC_API_KEY` backend for CI goes behind this same
  Protocol.
- **`Clock`**: time is needed in four distinct places across three packages:
  window resolution (`core`), expiring verdict claims (`store`), the report
  generation timestamp (`render`), and the `--archive` filename (`core`).
  Threading the same `now` argument from `cli` down through `core` into `store`
  is the classic tell that a value wants injecting instead.

**Rejected as seams, with reasons:**

- **The filesystem.** Tests build real fixture trees under `tmp_path`. A
  `SessionSource` Protocol was considered and rejected: its fake would have to
  faithfully reproduce nested subagent directories, `.meta.json` siblings, and
  `mtime_ns`/`size`/`content_hash` revision behavior including
  changed-during-read. A fake that drifts from the real layout passes tests that
  production fails, which is precisely the failure the snapshot-integrity design
  exists to catch.
- **The store.** Tests use real SQLite in a temp file. A fake store would let SQL
  bugs through, and the hand-designed star schema with its explicit upsert
  semantics is the part most worth exercising. Real SQLite is fast enough that
  there is no speed argument either.
- **`subprocess`.** No `CommandRunner` seam under `JudgeBackend`, because two
  stacked seams is indirection. Instead the split is: **argv construction is a
  pure function** in `judge`, unit-tested directly, which matters because the
  hardened invocation (`--tools ""`, `--setting-sources "user"`, `--bare`,
  filtered env, temp-dir cwd) is a security control and deserves assertions of
  its own; and **execution is a thin wrapper** covered by one
  `@pytest.mark.integration` canary against the real `claude` CLI.

**Injection is required, never defaulted.** `def __init__(self, *, judge:
JudgeBackend, clock: Clock)`, not `judge: JudgeBackend | None = None` with a
fallback. A defaulted seam means a test that forgets to inject silently
constructs the real thing, and for a paid judge that is a bill you discover
later.

**One composition root:** `cli.py`. It is the only module that constructs
concrete implementations. Nothing below it builds its own collaborators.

**A Protocol earns its place by having two implementations.** This ADR declares
the Protocols; the change that writes each real implementation writes its fake in
`tests/fakes.py` in the same commit, not eventually.

## Consequences

- **`cli.py` knows about every concrete implementation.** That is the point, one
  place to read to learn what the program is actually made of, and it is also
  the thing that gets unwieldy first. When it does, the fix is a `build_*` factory
  that `cli.py` calls, not letting `core` construct its own judge.
- **The `claude` CLI contract is verified by exactly one test that needs the real
  binary.** If that canary is skipped in CI (no `claude` installed, no auth), the
  execution wrapper is effectively untested and a flag-name change in the CLI
  lands silently. The design doc's instruction to re-verify flags before Phase 3
  is the mitigation, and it is a manual one.
- **Filesystem tests are slower than pure ones** and they can fail for
  environmental reasons a fake never would. Accepted, because a drifting fake
  fails in the worse direction.
- **`Clock` adds a parameter to constructors that would otherwise call
  `datetime.now()` inline.** Small, constant ceremony in exchange for window
  logic and claim expiry that test without freezing global time.
