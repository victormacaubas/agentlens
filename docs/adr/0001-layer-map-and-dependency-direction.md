# 0001. Layer map and dependency direction

## Status

Accepted

## Context

agentlens is a multi-command developer CLI that reads Claude Code session logs,
persists a dimensional store, scores runs with an LLM judge, and renders four
output surfaces. The design doc (`docs/agentlens-design.md`) already describes
four stages (parse, store, score, render) and two principles that only hold if
something enforces them: "one data core, many thin renderers" and "measured vs.
modeled, kept separate."

The project was rebooted from a working prototype, so the alternatives were not
hypothetical:

- **The prior layout** (`discovery/`, `parser/`, `aggregation/`, `ingest/`,
  `judge/`, `reporting/`, `store/`) put domain types inside `store/models.py`
  and `discovery/models.py`. Every other package therefore imported `store`
  just to obtain types, which inverts the intended flow and makes the store
  impossible to swap or test around. It also had no `render/` package despite
  four output surfaces, so formatting lived in `reporting/rendering.py`
  alongside SQL. Rejected for both reasons.
- **A flat `cli > core > store > models`** was considered. Rejected because the
  judge (a subprocess call to an external CLI) and four renderers would all
  start life inside `core/`, and formatting logic that starts in `core` does not
  leave on its own. It also leaves nothing to check the measured/modeled split.

The decomposition below is stage-shaped rather than technology-shaped: the four
middle packages correspond 1:1 to the design doc's four stages, which means the
architecture diagram and the import contract say the same thing.

## Decision

| Package | May import | Owns |
|---|---|---|
| `cli` | everything below | command definitions, exit-code mapping, composition root |
| `core` | `ingest`, `store`, `judge`, `render`, `models`, `utils` | orchestration: the ingest run, the scoring run, report assembly, window resolution |
| `ingest` | `models`, `utils` | `.claude/` discovery, JSONL parsing, snapshot integrity, name resolution |
| `store` | `models`, `utils` | all SQLite access, all SQL, all `sqlite3` types and exceptions |
| `judge` | `models`, `utils` | the `JudgeBackend` Protocol, the `claude -p` backend, argv construction, rubric, prepared transcript view |
| `render` | `models`, `utils` | terminal, markdown, JSON, and HTML formatting; the untrusted-content trust boundary |
| `models` | nothing in-project | domain types, Protocols, enums. No logic, no I/O. |
| `utils` | nothing in-project | leaf helpers: hashing primitives, small pure functions |

Two rules ride along with the table:

- **Dependencies flow one way.** A circular import is a layering error, not an
  import puzzle; the shared thing belongs in `models` or `utils`. Reaching for
  `if TYPE_CHECKING:` to break a real cycle hides it rather than fixing it.
- **A package that owns an external technology owns its types.** Nothing
  `sqlite3`-shaped leaves `store`. Nothing `subprocess`-shaped leaves `judge`.
  Nothing `jinja2`-shaped leaves `render`. The moment a driver type appears in a
  signature two layers up, that driver is part of the architecture.

`ingest`, `store`, `judge`, and `render` are **siblings and mutually
independent**. This is the enforceable form of "measured vs. modeled, kept
separate": `ingest` cannot import `judge`, so deterministic facts can never be
computed from model output, and `judge` cannot import `store`, so a verdict
cannot be written except through `core`.

Enforcement: the `Layered architecture` and `Middle layers are independent`
contracts in `pyproject.toml`, checked by `import-linter` via `make check`.
Per-technology ownership is enforced by the forbidden contracts in the same
file.

`cli` starts as a single module (`cli.py`) rather than a package. It becomes a
package when it exceeds a few hundred lines; the contract holds either way.

## Consequences

What this makes easy: any middle package can be read and tested in isolation,
knowing its only in-project dependencies are `models` and `utils`. The store can
be exercised with real SQLite in a temp file without constructing a judge. The
renderers can be exercised against hand-built domain objects with no database.

What this makes harder, and deliberately so:

- **`core` carries all the wiring.** Because the four middle packages cannot
  call each other, every cross-stage flow passes through `core`. `core` will be
  the largest package and the one most likely to need internal structure first.
  That is the price of the independence contract, and it is the intended trade.
- **Cross-stage convenience is banned.** The judge cannot read the store to
  fetch the transcript it is about to score; `core` must hand it in. This will
  feel like ceremony the first time. It is what keeps the judge testable without
  a database and re-scoreable from any transcript source.
- **`fact_session` derivation location stays open.** The design doc says derive
  session rows from tool events. Doing that in Python puts it in `ingest`; doing
  it in SQL puts it in `store`. Both are at the same layer, so the map does not
  prejudge it, but it also does not settle it, and the first change that needs
  it must decide and say so.
- **Two low-ceremony packages exist from day one.** `models` and `utils` are
  nearly empty at baseline. An empty package that the contract names is
  deliberate; it is what lets the gate run before any behavior exists.
