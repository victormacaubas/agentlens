## Why

The August 2026 code audit confirmed 31 Critical, High, and Medium findings across
`src/agentlens`. The defects can lose or misattribute session facts, pair changed
transcripts with stale verdicts, mix incomparable scores, repeat paid judge calls,
and report partial work as success despite a green test suite.

## What Changes

- **BREAKING** Replace raw session IDs with a globally unique source identity that
  distinguishes project, session kind, and raw Claude ID. Carry that identity through
  facts, lineage, skills, verdicts, lookup, and reporting.
- Version agent definitions by scope, project, and definition hash. Record the effective
  definition on each session so declared-skill attribution remains historically correct.
- Track a stable transcript/judge-input revision. Reject degraded or stale ingest snapshots
  and prevent cached or concurrently written verdicts from attaching to changed input.
- Make discovery and parsing tolerate unreadable paths, malformed records, mixed timestamp
  forms, and active-file races without replacing a sound stored grain.
- Stream transcript discovery, parsing, and judge-view preparation with bounded state.
  Enforce the prepared-view byte limit across every section, including errors and denials.
- Enforce judge response invariants at the backend-independent boundary, including bounded
  evidence and fixes, finite non-negative accounting, bounded error excerpts, and a locally
  derived overall score.
- Make floating-alias scoring progress across healthy sessions, honor one invocation-wide
  cap, avoid duplicate concurrent judge spend, and keep concrete model identity.
- Return non-zero CLI status for skipped or aborted batch work, reject negative limits,
  translate expected domain failures into actionable messages, and source the displayed
  package version from package metadata.
- Require reports to select one explicit comparable verdict cohort, include one
  deterministic row per spawn, normalize session dates to UTC, count touched files by
  normalized path identity, and use indexes that support window queries.
- Harden store paths and records by resolving symlinks before the `.claude` guard, creating
  private database files, and rejecting child records whose session identity conflicts
  with the grain being replaced.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `session-parser`: Qualify source identity, expose parse health and source revision, preserve
  valid grains on degraded or stale input, isolate discovery failures, normalize timestamps,
  and support bounded streaming.
- `store-schema`: Store qualified session and versioned definition identities, bind verdicts
  to judge-input revisions, enforce record identity and private path/file invariants, and add
  report-window indexes.
- `session-aggregation`: Derive one row per qualified spawn, count files by normalized path
  identity, and derive session dates from complete UTC-normalized timestamps.
- `skill-usage-bridge`: Resolve declared skills from the effective project/user definition
  version attached to each session.
- `judge-interface`: Build bounded streaming transcript views and enforce all verdict,
  accounting, volume, and error-redaction invariants for every judge backend.
- `rubric-scoring`: Claim scoring work safely, bind verdicts to input revisions, resolve
  aliases through healthy candidates, and apply one cap across resolution and scoring.
- `score-cli`: Validate limits, delegate resolution-aware caps to the scoring loop, expose
  concrete model identity, and return failure status for incomplete work.
- `cli-scaffold`: Define batch partial-failure exits, actionable domain-error rendering,
  positive ingest limits, and one authoritative package-version source.
- `windowed-reporting`: Select an explicit rubric/model/input cohort, emit every spawn's
  deterministic row, and keep aggregates consistent with the selected verdict identity.

## Impact

The change touches every production subpackage under `src/agentlens`, the SQLite DDL, CLI
contracts, report JSON, and synthetic fixtures. The disposable cache policy permits a schema
rebuild instead of a migration. Qualified IDs and the report payload are breaking data-contract
changes; command names and the measured-versus-modeled separation remain intact. The change adds
no external dependency and keeps `.claude` read-only.
