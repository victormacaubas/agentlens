## Context

See `proposal.md` for motivation. The shipped path parses and persists one
subagent transcript at a time, and the store can read one qualified session by
key. It has no bulk discovery, definition catalog, skill bridge, window query,
or `report` command.

The constraints that shape this design:

- The store is a disposable SQLite cache. Every persisted value must be
  reproducible from the current Claude source tree.
- `ingest`, `store`, and `render` remain siblings. `core` owns every flow that
  crosses those package boundaries.
- The runtime dependency set is closed. Discovery, frontmatter parsing,
  snapshots, date resolution, and SQL use the standard library and existing
  dependencies.
- `.claude/` is read-only. Temporary and report files live outside it.
- Phase 2 covers subagent spawns only. Main-session ingestion and scoring remain
  out of scope until after 1.0.0.
- Window membership uses spawn start time. Named calendar windows resolve in
  the machine local timezone; persisted and queried bounds are unambiguous UTC
  instants.

## Goals / Non-Goals

**Goals:**

- Build one vertical path from `agentlens report --since 7d` through discovery,
  sound parsing, deterministic persistence, analytical SQL, and JSON/terminal
  output.
- Preserve the one-row-per-spawn grain and report every qualifying subagent,
  including rows with unknown historical definition or skill context.
- Distinguish proven false values from history that current files cannot prove.
- Keep normal and dry-run reports behaviorally equivalent apart from persistent
  writes.

**Non-Goals:**

- Main-session rows, main-session scoring, or parent-session rollups.
- LLM calls, `fact_verdict`, scores, evidence, or suggested fixes.
- Markdown, HTML, dashboard, and report-history archives.
- Recovering deleted historical agent or skill definitions from git, backups,
  or transcript content that Claude did not record.
- A general ORM, migration framework, query builder, or filesystem abstraction.

## Decisions

### `report` is one core-owned vertical workflow

The CLI parses a typed `ReportArgs`, resolves the window, constructs the
existing concrete collaborators, and calls one core report function. Core runs
discovery, parsing, persistence, querying, document assembly, and output in
that order.

The stage packages remain independent:

```text
cli
 │
 ▼
core/report
 ├── ingest: discovered source snapshots and measured facts
 ├── store: batch upsert and analytical reads
 └── render: deterministic report document and summary
```

Alternative considered: let `store` discover files or let `render` query
SQLite. Both shorten the core function but couple sibling stages and make
measured facts depend on persistence or presentation.

### Discovery returns subagent source bundles, not paths alone

Discovery scans the configured Claude project root for subagent JSONL files and
builds a bundle containing the transcript path, optional sidecar path,
qualified project and parent identity, and the raw agent identifier. It ignores
project-level main-session JSONL files. Parent transcripts may be inspected for
the name-resolution fallback, but no main-session row is emitted.

Definitions are scanned from user and project agent directories before sessions
are derived. Project definitions override user definitions only for spawns in
that project.

Alternative considered: retain path-only discovery and let each parser rediscover
its sidecar, parent, and definitions. That repeats path interpretation and makes
one report vulnerable to resolving the same definition differently between
sessions.

### Every derivation input participates in snapshot identity

Transcript revision alone is insufficient: a sidecar, agent definition, skill
inventory, or parent name-resolution record can change while the JSONL remains
unchanged. Ingest therefore carries two related identities:

- the existing transcript revision proves the JSONL read was sound;
- a derivation fingerprint hashes the sound revisions and deterministic values
  of every input that shaped the session row and bridge rows.

The batch upsert compares the derivation fingerprint and the newest observed
input modification time. A changed sidecar or context snapshot can update
derived facts without pretending the transcript changed, while an older
composite snapshot cannot overwrite a newer one.

Alternative considered: fold every input into `revision_content_hash`. That
would preserve the current SQL shape but make a column documented as the
transcript hash mean several unrelated files.

### Historical definition and availability claims are conservative

Current files cannot prove what an old spawn saw after those files have changed.
A spawn binds to the currently observed effective definition only when that
definition's modification time is no later than the spawn start. Otherwise its
definition identity and declaration states are unknown.

Skill availability follows the same rule. Transcript evidence can prove a skill
was available or fired. Current files can prove availability only when their
observed revision predates the spawn; absence from the current filesystem does
not prove historical unavailability. `declared` and `available` are therefore
three-state values (`true`, `false`, `unknown`); `fired` remains boolean.

The catalog is content-addressed but reflects current reproducible source. A
later definition edit re-evaluates affected session bindings; it does not keep
an unrebuildable old definition version solely in the cache.

Alternative considered: bind every historical spawn to the definition visible
at report time. That produces complete-looking data by assigning new content to
old runs. Recovering real history from git would avoid the ambiguity but adds a
new source and a large compatibility surface.

### Agent frontmatter uses a bounded parser

Agent definitions are Markdown with YAML-like frontmatter, but adding a YAML
runtime dependency would reopen the closed dependency decision. Ingest parses
only the known fields this product stores: name, model, effort, tools, and
skills. It accepts the scalar and list forms emitted by Claude tooling, ignores
unknown keys, and rejects a definition whose known field has an unsupported
shape.

User definitions and matching project definitions form the definition catalog.
Skill availability scans user and project skill directories plus installed
plugin skill directories exposed beneath Claude's plugin cache. Each file is
read with the same stat-read-stat soundness rule used for transcripts.

Alternative considered: implement general YAML. A partial general parser would
be less predictable than a deliberately bounded field parser, while a full
parser is outside this product's responsibilities.

### Fired skills require explicit transcript evidence

`Skill` tool invocations are direct firing evidence. Injected skill markers are
handled by one parser whose supported marker shapes are pinned by synthetic
fixtures before bridge derivation uses them. Reads of `SKILL.md` remain ordinary
file reads and never count as a fire.

The bridge contains the union of skills named by applicable definitions,
provable availability, and firing evidence. The session's `n_skills_fired`
counts distinct bridge rows with `fired=true`.

Alternative considered: infer firing from a `SKILL.md` read. Agents and users
can read skill files without executing their workflow, so the resulting signal
would overcount.

### Spawn start time is a first-class session fact

`started_at` is the earliest usable transcript timestamp and controls report
membership. Store it as an ISO UTC instant. A window is half-open
`[start, end)`, so adjacent windows never double-count a boundary spawn.

`--since` resolves an elapsed duration. `--window this-week` computes local
calendar boundaries and converts them to UTC. An explicit `--from/--to` pair is
resolved once at the CLI/core boundary. The `Clock` seam supplies `now`.

Alternative considered: use file modification time. A transcript can be
touched or copied long after the spawn, which would move historical work between
report windows.

### Bulk ingest validates first and commits once

The report parses every discovered bundle and resolves its deterministic context
before mutating the persistent store. A hard source failure aborts report
generation and writes no part of that batch. Once all inputs are sound, store
applies definitions, sessions, tool events, and skill rows in one transaction.

Ordinary unreadable transcript lines remain counted parse-health facts, as in
the existing parser. A changed-during-read file, empty usable transcript,
invalid required sidecar, or database error aborts the batch.

Alternative considered: commit one session at a time and continue. That gives a
report an arbitrary partial population depending on filesystem order.

### Dry run uses a disposable clone of the store

For `--dryrun`, core clones the configured SQLite store into a temporary
SQLite database, applies the validated batch there, and runs the same analytical
queries and renderer. The configured store and report paths remain untouched.
When no persistent store exists, dry run starts from an empty temporary store.

This keeps dry-run output on the production query path and avoids a second
in-memory aggregation implementation.

Alternative considered: aggregate parsed dataclasses directly during dry run.
That would duplicate SQL semantics and let dry-run results diverge from normal
reports.

### Analytical SQL owns windows and rollups

Core resolves bounds and passes UTC instants, optional agent type, and
`min_sessions_for_trend` to store. Store selects current spawn rows, computes
current and prior agent aggregates, and returns store-owned rows converted to
typed model values. SQL keeps the spawn grain explicit and never joins verdict
tables.

The prior range ends at the current lower bound and has equal elapsed duration.
Trend status is comparable only when both populations meet the threshold,
default 5. Otherwise raw values and populations remain visible with
`insufficient_data`.

Each additive fact has a window total and a per-spawn average. Directional
deltas compare per-spawn averages so population growth does not masquerade as
per-spawn regression. Cache-read proportion is a weighted ratio computed from
summed cache-read, cache-creation, and uncached-input tokens; averaging
per-spawn percentages would give a tiny run the same weight as a large one.

Alternative considered: load every row into Python and aggregate there. SQLite
already owns filtering and aggregation, and duplicating those semantics would
make future dashboard and report consumers disagree.

### Report output has its own typed document

The existing session document stays unchanged. A new report document contains
scope metadata, complete current-window spawn rows, and agent rollups. The
document has a separate schema version and no modeled fields.

JSON serialization remains one shared render function. Default report files use
a stable scope-derived name and overwrite in place; `--format json` writes only
the document to stdout.

Alternative considered: widen the single-session document until it can represent
both surfaces. That would make optional fields dominate both schemas and risk
breaking existing consumers.

## Risks / Trade-offs

- **Historical definition or availability is often unknown for old spawns.** →
  Preserve `unknown` explicitly and expose it in rows rather than treating it
  as false or binding current content to old work.
- **Bulk validation holds many derived sessions before commit.** → Keep raw
  transcript handling per-file, retain only typed facts for the pending batch,
  and measure before adding chunking that would weaken all-or-nothing behavior.
- **Local calendar windows vary by machine timezone.** → Persist resolved UTC
  bounds and the local timezone identifier or offset in the report document.
- **Injected skill marker formats may change across Claude versions.** → Isolate
  marker recognition, pin each supported shape with a synthetic fixture, and
  let unknown shapes leave `fired=false` rather than guessing.
- **Known frontmatter fields may gain new shapes.** → Reject unsupported shapes
  with the source path and field name; extend the bounded parser from a pinned
  fixture rather than accepting an ambiguous value.
- **The persistent cache retains rows for sources later deleted from disk.** →
  Treat pruning as a separate source-reconciliation change; Phase 2 only
  upserts discovered sound sources and documents that cache deletion performs a
  full rebuild from currently available source.
- **A large first Phase 2 slice crosses every package.** → Implement it as
  tracer-bullet task groups that each leave the end-to-end report path green,
  rather than completing one package at a time.

## Migration Plan

No in-place migration. The store is a disposable cache and the new schema,
definition catalog, bridge rows, and derivation fingerprints are rebuilt from
source.

1. Pin the current schema and single-session behavior.
2. Add the new schema and typed values.
3. Delete or rebuild any development store before exercising the report path.
4. Keep `agentlens session` compatible with the expanded schema.
5. Roll back by reverting the code and deleting the disposable store before the
   older schema runs again.

## Open Questions

None. Window membership, calendar timezone, main-session scope, and historical
definition policy were resolved before this design.
