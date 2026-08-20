# 0005. Error taxonomy and translation boundaries

## Status

Accepted

## Context

agentlens reads files it does not own, writes a SQLite cache, and shells out to
an external CLI. Each of those fails in ways a caller should handle differently:
a malformed transcript should skip one grain and continue, an unauthenticated
judge should stop the run and say so, a bad flag should print usage.

Without a taxonomy declared up front, the first person to need a domain error
reaches for `ValueError`, and callers are then forced into `except ValueError`,
which catches every unrelated failure from anywhere in the stack and reports it
as though it were the expected one.

The tool is also meant to be scripted (the design doc specifies JSON output for
piping), so a wrapper should be able to distinguish "no sessions found" from
"judge unavailable" without parsing stderr.

## Decision

**The taxonomy**, in `src/agentlens/errors.py`:

```
AgentlensError                        (base; nothing else inherits from Exception)
├── ConfigError                 -> 2  bad flags, unusable config, ambiguous
│                                     judge-model cohort needing --judge-model
├── SourceError                 -> 3
│   ├── MalformedSourceError          unparseable JSONL / missing required field
│   ├── SourceChangedError            file changed during read; snapshot unsound
│   └── SessionNotFoundError          requested session id is not on disk
├── StoreError                  -> 4  translates sqlite3.Error
└── JudgeError                  -> 5
    ├── JudgeUnavailableError         claude CLI missing, or Not logged in
    └── JudgeResponseError            is_error true, or verdict fails validation
```

Anything uncaught exits **1**. Success is **0**. `ConfigError` maps to 2, which
matches the convention `click` already uses for usage failures.

**Each package translates foreign exceptions at its own boundary:**

| Package | Catches | Raises |
|---|---|---|
| `store` | `sqlite3.Error` | `StoreError` |
| `judge` | `OSError`, `subprocess.SubprocessError`, `json.JSONDecodeError` | `JudgeError` subclasses |
| `ingest` | `OSError`, `json.JSONDecodeError` | `SourceError` subclasses |
| `render` | `jinja2` errors | `AgentlensError` (no family earned yet) |

The tell that this rule has eroded is `cli.py` catching `sqlite3.Error` or
`subprocess.CalledProcessError`. If that appears, the driver's exception
hierarchy has become part of the CLI's contract, and ADR 0002's claim that the
store could be swapped is no longer true.

**Corollary:** code inside a package that has a taxonomy stops raising bare
`ValueError` and `RuntimeError`. A programmer error that should crash is the one
exception: `assert` or a bare raise is fine for "this cannot happen," because
those are not for callers to catch.

**Exit-code mapping lives in exactly one place:** a single decorator or `try`
around command dispatch in `cli.py`, never repeated per command.

**Not created yet, deliberately:** no `RenderError`, no
`StaleSnapshotError`. A stale snapshot is a decision to skip a replacement, not a
failure, so it is a return value rather than an exception. Both get added when a
caller genuinely needs to branch on them, and adding a subclass is cheap; adding
one nobody catches is noise.

## Consequences

- **`errors.py` sits outside the layer contract**, imported by every package.
  That is correct for an exception module and it means the taxonomy must stay
  dependency-free: no imports from `models`, no imports of anything third-party.
  Domain context travels as constructor arguments and plain attributes.
- **Five exit codes are a public contract.** Once a script branches on 3 versus
  5, renumbering is breaking. The codes are documented in `CLAUDE.md` for that
  reason.
- **Translation costs a wrapper at every boundary.** Every `store` function grows
  a `try/except sqlite3.Error` and every judge call grows one around the
  subprocess. Repetitive, and worth it: the alternative is that swapping SQLite
  or the judge transport becomes a user-visible change.
- **The taxonomy will feel over-specified at first.** Four `SourceError`
  subclasses before any parser exists is a bet that these are the real
  categories. The bet is grounded, since each one corresponds to a distinct behavior
  the design doc's snapshot-integrity rules already require, but if one turns
  out never to be raised, delete it rather than finding a use for it.
