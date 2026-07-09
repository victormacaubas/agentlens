# 1. Tests are synthetic-only; real-log validation deferred to v2

Status: Accepted

## Context

agentlens parses Claude Code session logs — JSONL transcripts under `~/.claude/projects/`, their `.meta.json` sidecars, and agent definitions under `.claude/agents/`. The obvious way to test a parser is to point it at real logs and assert on what comes out. During Phase 0+1 that instinct produced tests that read the developer's actual `~/.claude` tree, with a machine-pinned path into a specific project and content assertions on real subagent JSON.

Those tests were removed, for reasons that generalize beyond the one change:

- **Not reproducible.** Real logs differ per machine and per user; a test pinned to one person's `~/.claude/projects/<specific-project>/...` passes for them and is skipped (or fails) for everyone else and in CI. Coverage that only exists on one laptop is not coverage.
- **Proprietary content.** Real transcripts contain work product — file paths, code, business details. Asserting against them risks leaking that content into the repo and its history.
- **The parser shape is still settling.** The JSONL record schema shifts across Claude Code versions, and our own dataclasses are early. Strict assertions against real subagent JSON now would be brittle and would slow the red-green loop, coupling every parser tweak to a re-capture of fixtures.

The forces on the other side: we do want confidence the parser handles *real* shapes, not just our idealized fixtures. The resolution is to get that confidence from a **manual** one-off run, not an automated test, and to defer strict real-log validation to a later, dedicated effort (v2) once the schema and dataclasses have stabilized.

## Decision

**All automated tests are synthetic. No test reads the real `~/.claude` tree.**

- Every fixture is hand-built under `tmp_path`: a synthetic `.claude`-shaped directory, a hand-authored JSONL transcript, or plain dicts fed directly to parsing functions.
- **No real-log reads at test time.** No `Path.home() / ".claude"` in tests. The one permitted use of `Path.home()` in tests is asserting the default store-*path resolution* logic (which computes a `~/.cache/...` path) — that computes a path, it does not read logs.
- **No strict JSON-schema or row-content assertions against real subagent logs.** Deferred to v2.
- Manual verification against real logs (e.g. a developer running `agentlens session --file <a real log>` and eyeballing the result) is encouraged as a sanity check, but it never becomes an automated test.

## Consequences

- **Deterministic, portable, CI-safe.** Tests pass identically on any machine and in CI, with no dependency on the developer's local Claude Code history and no proprietary content in the repo.
- **The red-green loop stays fast** and decoupled from fixture re-capture as the parser shape evolves.
- **A real gap remains until v2:** drift between our synthetic fixtures and real Claude Code output is not caught automatically. Two things partially cover it in the meantime — a manual one-off run against a real log, and Phase 2 code that reads these rows and will surface semantic errors downstream. Parser functions are kept small and pure so v2's stricter, fixture-backed suite slots in without a rewrite.
- **v2 owes a follow-up:** a sanctioned way to validate against real shapes — most likely a small set of *sanitized* captured fixtures committed to the repo (scrubbed of proprietary content), not live reads of `~/.claude`. When that lands, this ADR should be revisited (and superseded if the policy changes).
- **New contributors have a bright line:** if a test needs `~/.claude`, it is wrong. This prevents the well-intentioned reintroduction of real-log tests that this decision exists to stop.
