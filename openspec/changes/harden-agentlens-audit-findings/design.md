## Context

See `proposal.md` for motivation. Agentlens treats its SQLite database as a disposable
cache, reads `.claude` without writing it, and separates deterministic facts from modeled
verdicts. Current table keys assume raw Claude IDs are globally unique, current verdict
keys omit the input revision, and current reports join verdicts by session alone. The
scoring path also has two identities at different times: a configured alias before the
first successful call and a concrete model afterward.

The implementation must preserve per-spawn grain, synthetic-only tests, named-module
imports, store-only database access, and the `uv` quality gate. The schema change requires
a cache rebuild; it does not require an in-place migration.

## Goals / Non-Goals

**Goals:**

- Make every stored fact and verdict traceable to one source project, source snapshot,
  session kind, raw Claude ID, and effective agent definition.
- Prevent incomplete, stale, or concurrent work from replacing newer facts or causing
  duplicate paid scoring.
- Put hard memory and byte bounds around transcript processing and model-authored output.
- Make command status and report output truthful under partial failure and multiple verdict
  versions.
- Cover every Critical, High, and Medium finding from the five August 2026 audit artifacts.

**Non-Goals:**

- Preserve an existing cache across the schema change.
- Add live integration fixtures that read the user's real `.claude` tree.
- Change the rubric dimensions, scoring scale, or measured-versus-modeled boundary.
- Fix the Low module-docstring finding.
- Add a network service, background daemon, or external dependency.

## Decisions

### 1. Use a qualified source tuple and opaque session key

Discovery will produce `source_project`, `session_kind`, and `raw_session_id`. The source
project value will be the stable project bucket relative to the configured Claude projects
root. A named identity helper will canonicalize that tuple and derive a deterministic
SHA-256 `session_id`; the store will retain every tuple component for display, lookup, and
collision diagnostics. Subagent parent keys will use the same helper with the parent raw
ID and `main` kind.

This keeps joins compact without hiding provenance. A composite primary key would expose
the same semantics but would widen every event, bridge, verdict, and claim foreign key.
Continuing to use raw IDs would preserve the current silent-overwrite defect.

Raw-ID CLI lookup will collect all matches. One match resolves directly; multiple matches
produce an ambiguity error naming project and kind.

### 2. Treat parsing as a versioned snapshot

The parser will stream bytes while computing a content hash and parse-health counters. It
will stat the source before and after reading. A snapshot is eligible to persist only when
the stat identity is stable and the parse-health policy classifies it as complete.

The source revision will contain observed modification time, size, and content hash.
Persistence will compare it with the stored revision in the same transaction as grain
replacement. An older observation cannot replace a newer one; equal stat metadata with a
different hash is a conflict and gets skipped. This resolves the stale-writer race without
holding a database transaction during file I/O.

Silently dropping malformed records and treating the remainder as complete was rejected
because it can erase previously sound facts. Retrying every malformed file forever was
also rejected. The result will distinguish stable empty input, malformed content,
incomplete final lines, and files that changed during reading so the caller can report the
correct reason.

Discovery will yield targets lazily and isolate `OSError` at each root or project boundary.
Applying `--limit` through an iterator slice will stop traversal once the budget is filled.

### 3. Version agent definitions and bind the effective version to a session

Agent definitions will use a stable identity over `(agent_type, scope, source_project,
definition_hash)`. User definitions have no source project. Project definitions carry the
same project key as the session target. Effective resolution selects a matching project
definition before the user definition and writes that definition identity on the session.

The skill bridge will read declared skills from the session's bound definition version.
Later definition edits therefore affect later ingests without rewriting the historical
meaning of earlier sessions.

An overwrite-only `dim_agent` was rejected because it cannot answer which instructions or
skills produced an older run.

### 4. Separate whole-call identity from file identity

Tool events will retain `input_hash` for duplicate-call detection and add
`file_path_hash` for supported file-addressing tools. The parser will extract the tool's
path field, normalize its lexical representation under one documented policy, and hash
only that value. Aggregation will count distinct non-null path hashes.

One shared timestamp parser will validate complete ISO values, require or apply the
documented timezone policy, and normalize accepted instants to UTC. Duration and
`session_date` will use that helper so they cannot disagree at a day boundary.

### 5. Build the judge view with a streaming reducer and a final byte gate

The view builder will consume JSONL records incrementally and retain only bounded task
text, condensed tool lines, bounded pending tool-use metadata, error summaries, counters,
and the latest final-report candidate. Each of the six sections will receive a byte budget.
Errors will preserve total count and deterministic head/tail step references with bounded
excerpts. They will no longer promise unbounded full preservation.

After section allocation, a final UTF-8 byte gate will assert that the assembled document
fits `VIEW_MAX_BYTES`. Tests will exercise multibyte text and oversized fixed sections.

Reading the complete transcript and truncating only after materialization was rejected
because the 20KB output contract does not protect process memory.

### 6. Key verdicts by the exact prepared input

The view's UTF-8 bytes will produce `judge_input_hash`. `fact_session` will record the
current hash, and `fact_verdict` will key rows by `(session_id, judge_input_hash,
rubric_version, judge_model)`. Historical verdicts remain available, while cache queries
match only the current input.

Finalization will compare the scored hash with the session's current hash inside the write
transaction. A concurrent re-ingest therefore turns an in-flight score into a stale result
instead of attaching it to changed facts.

Deleting all verdicts during re-ingest was rejected because unchanged prepared input
should remain a cache hit and old verdicts remain useful provenance.

### 7. Validate verdicts once, outside any specific backend

A named verdict factory or validation function in the judge protocol layer will enforce
the exact dimension set, integer score range, derived overall, evidence and fix bounds,
known enums, concrete model identity, and finite non-negative accounting. The scoring loop
will validate every backend result before persistence. The Claude backend will normalize
malformed numeric metadata into `JudgeError`.

Every model-controlled value used in an exception will pass through one bounded excerpt
helper. Structural errors will report the field and item index rather than serialize the
complete envelope.

Relying on `claude --json-schema` alone was rejected because custom backends, mocks, and
future CLI behavior can bypass that boundary.

### 8. Claim scoring work and account for one attempt budget

The store will add an expiring scoring-claim relation keyed by the target verdict identity
and owned by a generated run ID. Claim acquisition will be atomic. The owner will finalize
or release after each call; another run can recover an expired claim.

Before alias resolution, the requested alias participates in the claim key. The loop will
try candidates in stable order until one successful call reveals the concrete model, while
session-specific failures consume attempts and become skips. It will then re-query against
the concrete model and current input hashes. One remaining-attempt counter covers both
stages.

A process-wide or store-wide lock was rejected because it would block safe scoring of
different sessions and would not recover cleanly after a crash.

### 9. Keep CLI orchestration thin and make status machine-readable

The scoring loop will own cap and alias progression. The CLI will validate positive
integer options, handle confirmation, render progress, and translate result state to exit
status. Any skipped or aborted batch work exits non-zero after printing retained successes.
The ingest command follows the same partial-success rule. Expected domain failures become
`ClickException` instances at the command boundary.

`agentlens --version` will read installed distribution metadata. The package initializer
will return to the required empty state.

### 10. Require a report verdict cohort

Report construction will receive or deterministically resolve one rubric version and
concrete model. SQL will join on session, current input hash, rubric, and model. The result
will include a typed per-session collection for every spawn and derive aggregate scores
from those selected rows. The JSON payload will name the cohort.

Choosing the last row was rejected because SQLite does not guarantee that order and an
`ORDER BY` would still mix incomparable cohorts. Averaging each cohort side by side remains
a future capability.

The DDL will add indexes beginning with `(session_kind, session_date, agent_type)` and a
parent-lens-supporting index if query-plan tests show it is needed.

### 11. Validate physical store safety before opening SQLite

Store-path resolution will canonicalize existing ancestors, reject any physical `.claude`
ancestor, create parent directories with owner-only access, and open new database files
with owner-only permissions. It will check existing database permissions deliberately and
ensure SQLite sidecars inherit the same boundary.

The grain replacement API will validate every child record's session ID against the parent
before its first `DELETE`. This keeps caller mistakes from mutating two grains.

### 12. Audit traceability

The artifacts cover these findings:

- CLI boundary: `BUG-01`, `BUG-02`, `ARCH-01`, `ERR-01`, and `ERR-02`.
- Discovery/parser/ingest: `BUG-01` through `BUG-04`, `ERR-01`, `ERR-02`, and `PERF-01`.
- Store: `BUG-01`, `BUG-02`, `SEC-01`, `SEC-02`, and `ARCH-01`.
- Judge: `BUG-01` through `BUG-03`, `PERF-01`, `ERR-01`, `ERR-02`, `SEC-01`,
  `SEC-02`, and `ARCH-01`.
- Aggregation/reporting: `BUG-01` through `BUG-03`, `ARCH-01`, and `PERF-01`.

The store `STYLE-01` finding is Low and remains outside this change by request.

## Risks / Trade-offs

- [Qualified IDs break existing cache references] → Rebuild the disposable cache and keep
  raw IDs as explicit display and lookup fields.
- [Project bucket identity can change if Claude changes its on-disk convention] → Isolate
  project-key derivation in one helper and store the source tuple alongside its hash.
- [Strict parse health can skip a transcript with recoverable historical damage] → Report
  exact health counters and permit a later policy change without weakening stale-write
  protection.
- [Claims can strand work after a crash] → Use owner IDs and bounded expiry with synthetic
  recovery tests.
- [Streaming changes parser and view-builder APIs together] → Land one streaming record
  source and reuse it in ingestion and judging rather than maintain two decoders.
- [Private permission checks differ across platforms] → Assert POSIX mode behavior on
  supported Unix systems and keep path-containment tests platform-neutral.
- [One report cohort requires a selection rule] → Default to the current rubric and one
  requested concrete model; fail on ambiguity instead of guessing.

## Migration Plan

1. Land the new DDL, identity helpers, record models, and synthetic schema tests.
2. Delete or move the old cache and rebuild it from `.claude`; do not attempt row migration.
3. Ingest definitions and sessions with qualified identities and source revisions.
4. Enable input-bound scoring, claims, and report cohort selection.
5. Run the complete quality gate and targeted concurrency, malformed-input, memory-bound,
   permission, and query-plan probes.

Rollback requires reverting the code and recreating a cache with the prior DDL. New and old
cache files are not schema-compatible, so rollback must not reuse the new cache.
