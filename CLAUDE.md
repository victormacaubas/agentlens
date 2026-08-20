# agentlens

A local CLI that reads Claude Code session logs, scores subagent runs, and
produces actionable fix proposals. Read-only against the user's `.claude/`.

Product intent is in `docs/agentlens-design.md`. Architectural decisions are in
`docs/adr/`. This file holds the rules those decisions produced.

## Coding standards this project inherits

- **`python-engineering-standards`** governs every Python file here: layout,
  typing, docstrings, class-versus-function, logging, error handling, dependency
  injection, testing. Consult it before writing, not after.
- **`sql-data-analysis`** governs the store and every query over it. The
  reporting layer is analytical SQL over a star schema, which is a whole class of
  work in this repo rather than an incidental one.

## Layer map

| Package | May import | Owns |
|---|---|---|
| `cli` | everything below | command definitions, exit-code mapping, composition root |
| `core` | `ingest`, `store`, `judge`, `render`, `models`, `utils` | orchestration: the ingest run, the scoring run, report assembly, window resolution |
| `ingest` | `models`, `utils` | `.claude/` discovery, JSONL parsing, snapshot integrity, name resolution |
| `store` | `models`, `utils` | all SQLite access, all SQL, all `sqlite3` types |
| `judge` | `models`, `utils` | the judge backends, argv construction, rubric, prepared prompt view |
| `render` | `models`, `utils` | terminal, markdown, JSON, HTML; the untrusted-content boundary |
| `models` | nothing in-project | domain types, Protocols, enums. No logic, no I/O. |
| `utils` | nothing in-project | leaf helpers: hashing primitives, small pure functions |

`errors` sits below everything and imports nothing in-project.

Three rules ride with the table:

- **Dependencies flow one way.** A circular import is a layering error, not an
  import puzzle. The shared thing belongs in `models` or `utils`. Reaching for
  `if TYPE_CHECKING:` to break a real cycle hides it instead of fixing it.
- **A package that owns a technology owns its types.** Nothing `sqlite3`-shaped
  leaves `store`, nothing `subprocess`-shaped leaves `judge`, nothing
  `jinja2`-shaped leaves `render`. The moment a driver type appears in a signature
  two layers up, that driver is part of the architecture.
- **`ingest`, `store`, `judge`, and `render` are independent siblings.** They
  cannot import each other. This is the enforced form of "measured vs. modeled,
  kept separate": a deterministic fact can never be computed from model output.
  Every cross-stage flow passes through `core`, which is deliberate ceremony.

All of this is checked by `lint-imports` inside `make check`. When adding a
contract, **assert it fails before trusting it to pass.**

## Dependencies

Runtime: `click` (owned by `cli`), `jinja2` (owned by `render`). Everything else
is standard library: `sqlite3`, `subprocess`, `json`, `hashlib`, `dataclasses`.

Deliberately excluded, so the question does not get re-litigated: no ORM, no
pydantic, no pandas or polars, no `rich`, no `requests` or `httpx`, no
`jsonschema`. Reasons are in `docs/adr/0002`.

**The set is closed.** Adding a runtime dependency is an ADR, not an
implementation detail resolved mid-task. A task that needs a library not on the
list gets handed back rather than resolved by picking one.

## Seams

Two injected dependencies, both Protocols in `agentlens.models.protocols`:
`JudgeBackend` and `Clock`.

The filesystem, the store, and `subprocess` were considered and rejected as
seams; `docs/adr/0004` says why, and the rule that decides membership is there
too. Judge new candidates by the rule, not by comparison to the list.

- **Injection is required, never defaulted.** `judge: JudgeBackend`, never
  `judge: JudgeBackend | None = None` with a fallback. A defaulted seam means a
  test that forgets to inject silently constructs the real thing, and the judge
  costs money.
- **`cli.py` is the only composition root.** Nothing below it constructs its own
  collaborators.
- **A Protocol earns its place by having two implementations.** The change that
  writes a real implementation writes its fake in `tests/fakes.py` in the same
  commit.

## Errors

Everything raised deliberately inherits from `AgentlensError`. Families:
`ConfigError`, `SourceError`, `StoreError`, `JudgeError`. Exit codes are 2, 3, 4,
5 respectively; 0 is success and 1 is anything that escaped the taxonomy.

**Exit codes are a public contract.** Scripts branch on them, so renumbering is
breaking.

- **Each package translates foreign exceptions at its own boundary.** `store`
  catches `sqlite3.Error`, `judge` catches `OSError` and `json.JSONDecodeError`,
  `ingest` catches `OSError`. If `cli` ever catches a driver exception directly,
  that driver has become part of the CLI's contract.
- **No bare `ValueError` or `RuntimeError`** inside a package that has a taxonomy.
  A bare builtin forces callers into `except ValueError`, which catches every
  unrelated failure in the stack and reports it as the expected one. Programmer
  errors that should crash are the exception; those are not for callers to catch.
- **Exit-code mapping lives in exactly one place** in `cli.py`, never per command.

## CLI entrypoint

`cli.py` parses arguments, builds config, and hands off. A branch that decides
*what work happens* belongs in `core`.

- **`main(argv: list[str] | None = None) -> int`**, with `sys.exit(main())` at the
  bottom. Returning a code keeps `main` testable; calling `sys.exit` from inside
  does not.
- **Factor parsing into its own testable function.** A command body that does real
  work is only reachable through the argument parser, which couples every test to
  flag names.
- **`--dryrun` is first-class.** Every path that writes a file respects it and logs
  what it would have written.
- **Mutually exclusive window selectors** (`--since` / `--window` / `--from` plus
  `--to`) are declared as a group, not hand-validated in the command body.
- **Log the resolved arguments once at startup**, as JSON. When a scripted or cron
  run behaves oddly, that line is what explains it.

## Logging and output streams

The report is the product and goes to stdout. Everything about *how the run went*
goes to the logger on stderr. Mixing them breaks
`agentlens report --format json | jq`.

- **`logger = logging.getLogger(__name__)`** at module level in any module that
  emits. Never construct a logger per call.
- **Lazy `%s` formatting**: `logger.info("Scored %d spawns in %s", n, window)`, not
  an f-string, so interpolation is skipped when the level is filtered out.
- **`print` is for the machine-readable surface only**: JSON to stdout and the thin
  terminal summary. Diagnostics, progress, and dry-run notices use the logger.
- **Carry identifying context in every line**: the window, the `session_id` or its
  raw tuple, the agent type. Scoring runs interleave, so an uncontextualised line
  is unreadable.
- **`logger.exception` inside `except` blocks** where the traceback adds signal.
  Never a bare `logger.error(str(e))`, which throws the stack away.
- **Never log a secret**, and treat transcript content as capable of containing
  one. Log identifiers and counts, not payloads.

## Testing

- **One canonical builder per type, keyword-only, in `tests/factories.py`.** A
  second copy of a builder inside a test module starts drifting immediately, and a
  test that passes because its local builder defaulted a field differently proves
  nothing.
- **One fake per seam, in `tests/fakes.py`.** Inject; do not patch. Reaching for
  `unittest.mock` is the signal that a seam is missing, so add the seam.
- **Assert against behavior through the public surface.** No positional access
  into rows: `row[2]` is a dependency on column order, and reordering columns must
  never break a test. No importing underscore-prefixed helpers.
- **Name tests for what they assert.** `test_upsert_replaces_row_with_same_key`,
  never `test_upsert_2`. The name is what a reader sees when CI fails.
- **Time enters through the `Clock` seam.** Never freeze the clock globally.
- **Fixtures are synthetic.** No real transcripts are committed. This means the
  parser is only ever proven against JSONL we wrote ourselves, so
  `tests/factories.py` encodes a belief that can be wrong for every test at once.
  Treat changes there with the care of a parser change.
- Integration tests are opt-in via `make integration`. They invoke the real
`claude` CLI, so they need auth and cost money.

**If a change that preserves behavior breaks a test, the test was wrong.**

Cases a new test module considers: empty input, a single element, duplicate keys,
the natural key colliding across projects, re-running the same input twice, an
unmatched `tool_use`, a partial failure mid-batch, retry exhaustion, a missing
config key, and the zero-results path of every read. That last one is the most
commonly missed and it is what a user hits on their first run.

**A spec scenario is not a test function.** Scenarios in `openspec/specs/**` are
acceptance criteria; they say what must be true, not how many `def test_` bodies
prove it. One parametrized test covering six scenarios is better than six tests,
and the checklist above is a list of cases to *consider*, not a quota to fill.
Counting scenarios and writing one test each is how a suite grows past the code
it protects.

**Tests are written with the code, in the same task, by the same worker.** No
separate fixture task before the implementation and no "add tests proving X" task
after it. Writing the test first is valuable when it is how you discover the
interface; when the interface is already settled in `design.md` and the task
list, a failing-test step is a second derivation of a decision already made, and
the trailing test task is a third. Name the cases to cover inside the
implementation task and cover them once.

## Vocabulary

Use these words precisely, in code, in output, and in prose. Most of them mark a
distinction that silently produces wrong numbers when collapsed.

- **spawn** is the unit of analysis, not "session" and not "agent". Four
  `implementer` spawns in one parent session are four rows. Never dedupe by
  `agent_type`. Any surface showing a count says spawns, because "12 runs" may be
  3 sessions with 4 spawns each.
- **`session_id`** always means the qualified derived key. What was read off disk
  is **`raw_session_id`**, and it is not unique across projects or kinds.
- **measured** is deterministic and reproducible from source. **modeled** is an
  LLM verdict: subjective, versioned, re-scoreable. They never mix in one table.
- **`judge_model`** always means the concrete identifier resolved from the
  response envelope, never the alias typed at the CLI.
- **verdict** is judge output. A **finding** is what a reader acts on. The product
  is the fix, not the score.
- Name packages and modules for responsibilities, never for types. No `handlers`,
  `managers`, `helpers`, `common`, or `misc`. `utils/` is the one type-shaped
  package name the layout keeps, so the modules inside it carry the weight:
  `hashing.py`, never `helpers.py`.

## Reading and writing the user's data

- **Never write into `.claude/`.** Reads only, always.
- **The store is a disposable cache** rebuilt from source, which is what makes
  hand-written SQL and no migration tooling affordable. Nothing may land there
  that cannot be regenerated from `.claude/`.
- **Judge evidence and fix text are untrusted model output.** Every surface that
  presents them marks them as untrusted and escapes them. Never emit anything
  shaped like a patch, diff, or command for direct application.
- **Analyzed token usage is a quality signal, never a dollar figure.** Only
  agentlens's own judge cost is reported in dollars.

## How changes get made

Work goes through OpenSpec: `/opsx:propose`, then `/orchestrate`, then
`/opsx:archive`.

**Changes are vertical slices.** One path through the layers at a time, never one
layer at a time. A slice that touches `ingest`, `store`, and `render` for a single
capability is right; a slice that builds all of `store` is not. This is what keeps
the wiring exercised from the first change onward.

**A task is a behavior, not a phase of one.** Write each task as the outcome plus
the cases it must cover: *"Implement the bounded frontmatter reader for name,
model, effort, tools, and skills with sound stat-read-stat revisions and
source-specific errors. Cover: scalar and list frontmatter, unknown keys,
malformed known fields, changed-during-read."* One task, one pass, code and tests
together. Splitting that into a fixture task, an implementation task, and a
verification task makes a worker derive the same behavior three times and is the
single largest avoidable cost in a change.

**`make quick` per task, `make check` once per change.** The full gate is the
merge boundary, not a per-section ritual. A `Run make check` line under every
section turns one gate into ten round trips for no added signal.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.

This applies to YOU and to every subagent you spawn. Include this rule explicitly in every subagent prompt that involves code exploration.
