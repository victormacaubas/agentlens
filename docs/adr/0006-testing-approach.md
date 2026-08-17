# 0006. Testing approach

## Status

Accepted

## Context

When shared test infrastructure has no declared home, every piece of work writes
its own local helper because there is nothing to import. Those copies drift
silently, and eventually the suite becomes the main obstacle to changing the
source it exists to protect, at which point improving the code requires
rewriting tests, so it does not happen.

agentlens has a specific version of this risk. Its input is nested JSONL written
by someone else's tool, so a "build me a session fixture" helper is needed by
almost every test in the parser, the aggregation layer, and the judge. If three
modules each grow their own builder with slightly different defaults, a test can
pass because its local builder happened to set a field the real parser requires.

## Decision

**The floor.** `pytest`. Tests are named for what they assert:
`test_upsert_replaces_row_with_same_key`, never `test_upsert_2`, because the
name is what a reader sees when it fails in CI. Time enters through the `Clock`
seam from ADR 0004, so a test passes a fixed instant rather than freezing the
clock globally.

**The homes**, declared now:

```
tests/
├── conftest.py          # fixtures with real lifecycle to manage, and only those
├── factories.py         # one canonical builder per domain type, keyword-only
├── fakes.py             # one fake per seam from ADR 0004
├── unit/
└── integration/         # @pytest.mark.integration, excluded from the default run
```

**The rules:**

- **One canonical builder per type**, keyword-only, in `tests/factories.py`. The
  moment a second copy of a builder exists inside a test module, the two start
  drifting. Keyword-only because a builder's parameter list grows, and positional
  arguments silently change meaning when it does.
- **One fake per seam**, in `tests/fakes.py`, written in the same commit as its
  Protocol. Tests inject rather than patch:
  `ScoringRun(judge=FakeJudge(), clock=FakeClock(...))`, never
  `@patch("agentlens.judge.claude_cli.subprocess.run")`. A patch names an
  internal import path, so it breaks on a rename and says nothing about the
  contract; injection breaks only when the contract changes, which is exactly
  when a test should break.
- **Tests assert against behavior through the public surface.** No positional
  access into rows: `row[2]` is a dependency on column order, and reordering
  columns must never break a test. No importing underscore-prefixed helpers. No
  patching anything that has a seam; reaching for a patch means ADR 0004 missed
  one.

**The norm that cannot be checked mechanically**, so it goes in `CLAUDE.md`
verbatim: **if a change that preserves behavior breaks a test, the test was
wrong.** That reframes a refactor breaking tests from an expected cost into a
finding.

**Fixtures are synthetic only.** Unit tests build JSONL through
`tests/factories.py`; no real session transcripts are committed. Two alternatives
were considered:

- **A committed sanitized real corpus** would close the drift gap in CI, but it
  requires a scrubber for paths, prompts, and code plus review discipline on
  every fixture update, and a miss is permanently in git history.
- **A local real-corpus canary**, an integration-marked test pointing at the
  developer's own `~/.claude` asserting invariants rather than values, would
  catch drift without committing anything, at the cost of a test whose result
  depends on the machine.

Both declined for now. **The consequence is acknowledged rather than solved: the
parser is only ever proven against JSONL we wrote ourselves.** Closing this gap
stays on the v2 list in the design doc, and the mitigation in the meantime is
that snapshot integrity and name resolution both have explicit fallback paths
that degrade rather than crash on unexpected input.

**The integration lane** exists for exactly one thing at baseline: the canary
that invokes the real `claude` CLI to verify the hardened flag contract from the
design doc still holds. It is excluded from the default run because it costs
money and requires auth.

**No `pytest-socket`.** The usual reason to add it is to stop a unit test making a
real network call. ADR 0002 excludes `requests` and `httpx`, so no in-process
HTTP client exists to misuse, and the one genuine network path, the `claude`
subprocess, opens its socket in a child process where `pytest-socket` cannot
see it. It would guard a risk the closed dependency set already eliminates.

**No coverage threshold.** A number in the gate gets satisfied by tests written
for the number. What the gate checks instead is the architecture (ADR 0007).

**The standing checklist** a new test module considers, several of which come
straight out of ADR 0003's identity decisions:

empty input · a single element · duplicate keys · the natural key colliding
across projects · re-running the same input twice · an unmatched `tool_use` ·
a partial failure mid-batch · retry exhaustion · a missing config key ·
the zero-results path of every read

The last one is the most commonly missed: the empty-window, no-rows-matched path
of the main report query is what a user hits on their first run, before any data
exists.

## Consequences

- **The drift gap is a known, accepted hole.** A change to Claude Code's JSONL
  format will not be caught by this suite. It will be caught by a user, or by the
  developer running the tool against real data by hand. That is the price of a
  fully hermetic suite and it is recorded here so nobody rediscovers it as a
  surprise.
- **`tests/factories.py` becomes load-bearing and must stay faithful.** It
  encodes our belief about the source format. When that belief is wrong, every
  test is confidently wrong together, which is the specific failure mode of
  synthetic-only testing.
- **Injection over patching means the seams have to exist before the tests do.**
  A test that wants to patch is a signal to go back to ADR 0004, which is slower
  in the moment than reaching for `unittest.mock`.
- **Excluding integration by default means the `claude` CLI contract is unchecked
  in CI.** Flags do shift across CLI versions. The design doc's instruction to
  re-verify before Phase 3 is a manual step, and manual steps get skipped.
