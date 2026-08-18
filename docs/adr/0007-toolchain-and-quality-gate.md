# 0007. Toolchain and quality gate

## Status

Accepted

## Context

The prior build of agentlens had `ruff`, `mypy --strict`, and `pytest`
configured, and no gate command, no CI, and no import contracts. That
combination is the specific failure this ADR exists to prevent: the mechanical
hygiene checks existed, but nothing checked the project's own architecture, and
nothing guaranteed that all the checks ran together.

The principle worth recording, because it is invisible in the config file:
**architectural rules are enforced mechanically rather than by review.** ADRs
0001 through 0006 each decided something. Anything they decided that did not end
up in this gate is a wish, and wishes lose to whatever pattern is nearest when
somebody is in a hurry.

## Decision

**The stack:** `uv` for environments and the lockfile, `ruff` for lint and
format, `mypy` for types, `pytest` for tests, `import-linter` for the layer map.
One config file (`pyproject.toml`), no overlapping tools: no Black, no isort.

**Configuration of note:**

- `requires-python = ">=3.12"` while developing on 3.14. `uvx` fetches its own
  interpreter so the floor barely gates users, and a 3.12 floor keeps `pipx`
  working on current system Pythons.
- `line-length = 100`, against the inherited Python standard's default of 88. This
  is a deliberate deviation carried from the pre-reboot configuration rather than
  an inherited one, since the reboot deleted `pyproject.toml` and left nothing to
  inherit. Recorded because the standard's own precedence rule says the standard
  wins when a repo has no tooling config, so this is the project overriding it on
  taste. Nothing in `src/` currently exceeds 88, so the cost of reverting to 88 is
  one reflowed line in a test module.
- Ruff `select` adds `S` (bandit) beyond the usual set, because this tool builds
  `subprocess` argv and renders HTML. `S603`/`S607` will fire in `judge` when the
  real backend lands; the correct response is a per-line `# noqa` with a reason,
  never a blanket ignore, so that the exception is a deliberate act.
- mypy `strict = true` with `warn_unreachable`, over `src` **and** `tests`, with
  **no per-package exceptions**. Retrofitting strict is painful enough that most
  projects never do it; the whole argument for paying now is that the project is
  a few hundred lines.
- pytest `--strict-markers`, so a typo'd marker is an error rather than a test
  that silently never runs, and `-m 'not integration'` by default.

**Five import contracts** encode ADR 0001 and ADR 0002:

| Contract | Encodes |
|---|---|
| `Layered architecture` | the layer table, including the four stage packages as independent siblings |
| `Only store touches the database driver` | `sqlite3` confined to `store` |
| `Only judge spawns processes` | `subprocess` confined to `judge` |
| `Only render templates output` | `jinja2` confined to `render` |
| `Only cli parses arguments` | `click` and `argparse` confined to `cli` |

**One gate command: `make check`.** Five steps, cheapest first, so a formatting
slip fails in two seconds rather than after the suite. A gate with five separate
commands gets run partially. **CI runs `make check` itself**, not an equivalent
list of steps. If CI checked a different set, the local gate would be the one
that quietly stopped being trusted.

**No pre-commit.** Declined to keep moving parts down. The cost is real and
recorded below.

**No coverage threshold.** A number in the gate gets satisfied by tests written
for the number rather than for the behavior.

## Verification

**The gate was run and came back green** against the declarations this baseline
wrote: 5 contracts kept, mypy clean on 20 files, 3 tests passing, format and lint
clean.

Green alone would not have been enough, because a contract that has never seen a
violation is an unverified assertion. Each contract was checked by injecting a
deliberate violation and confirming it broke the build:

- `store` importing `core`: caught by the layers contract.
- `core` importing `sqlite3`: caught by the forbidden contract.
- `ingest` importing `judge`: **not caught on the first attempt.**

That third result is the reason this section exists. import-linter distinguishes
`|` (independent siblings) from `:` (non-independent siblings) inside a layer, and
the layer had been written with `:`. The contract reported `KEPT` while `ingest`
imported `judge` freely, so the design doc's "measured vs. modeled, kept
separate" principle was declared, checked, and unenforced, all at once. Switching
the delimiter to `|` makes it break correctly. Both sibling layers now use `|`,
and the distinction is commented in `pyproject.toml` because it is silent in
exactly the direction that gives false confidence.

**Generalization for anyone adding a contract later: assert it fails before
trusting it to pass.**

## Consequences

- **`import-linter` needs every named package to exist on disk with an
  `__init__.py`.** A layer with no directory fails outright with `Missing layer`.
  This is why `utils`, `core`, `ingest`, `store`, `judge`, and `render` exist as
  near-empty packages at baseline. An empty package is analyzed and kept, so the
  gate runs before any behavior does.
- **`include_external_packages = true` is mandatory**, not optional, because every
  forbidden contract names a module outside the root package, and the standard
  library counts. Without it the run refuses to start.
- **Strict mypy over `tests` too** means test helpers and fakes carry annotations.
  More friction per test, and it is what keeps `tests/fakes.py` honest about
  satisfying its Protocol.
- **No pre-commit means formatting and secret-scanning failures surface in CI**
  rather than before the commit. The concrete risk this leaves open: agentlens
  reads session transcripts that can contain credentials, so a fixture pasted from
  real data could carry a secret into permanent git history with nothing checking.
  `sample-data/` is gitignored and fixtures are synthetic (ADR 0006), which is the
  mitigation, and it depends on discipline rather than a hook.
- **The gate does not prove the code is shaped the way this project decided.** It
  proves the code runs and the imports point the right way. Cohesion, state
  ownership, duplication, and test design are reviewed by the
  `structure-review` skill before a change is archived, and that review asking
  for changes blocks the archive.
