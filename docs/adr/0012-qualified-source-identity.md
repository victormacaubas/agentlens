# ADR 0012: Session and definition provenance is qualified and versioned

## Status

Accepted

## Context

Claude's raw session IDs and subagent IDs are unique only within their source context.
The same value can name a main transcript, a subagent transcript, or inputs in different
project buckets. Using that value as the global primary key lets one successful ingest
silently replace another.

An ID alone also says nothing about which bytes were parsed. Session logs can grow while
agentlens reads them, two ingest processes can commit in the opposite order from their
reads, and malformed re-ingest can contain fewer valid facts than a previously stored
snapshot. SQLite serializes writes but cannot identify which snapshot is newer without
source provenance.

Agent definitions have the same contextual problem. A project definition can override a
user definition with the same agent type, and either definition can change over time. One
overwrite-only row per agent type cannot attribute a historical run or declared-skill
bridge to the instructions active for that run.

## Decision

Discovery creates a source identity tuple of `(source_project, session_kind,
raw_session_id)`. A canonical encoding of that tuple produces a deterministic SHA-256
`session_id` used by facts, lineage, skills, verdicts, and scoring claims. The store retains
the tuple fields alongside the hash. Qualified subagent parent IDs use the same source
project and raw parent ID with session kind `main`.

The parser streams each input while computing parse-health counters and a source revision
containing modification time, size, and content hash. It compares file metadata before and
after reading. Persistence checks the incoming revision in the same transaction that
replaces a grain. A degraded, changed-during-read, older, or conflicting snapshot cannot
replace a newer complete grain.

Agent definitions are versioned by `(agent_type, scope, source_project,
definition_hash)`. Project scope takes precedence over user scope for sessions from that
project. Each session stores the effective definition identity selected at ingest, and the
skill bridge derives declared skills from that bound version.

Raw-ID CLI lookup remains available. When more than one qualified input matches, the
command reports the project and kind choices instead of selecting one.

## Consequences

- The SQLite schema and every session foreign identity change. The cache is disposable, so
  rollout requires deleting or moving the old cache and rebuilding it from `.claude`
  rather than migrating rows.
- Same-named inputs from different projects or kinds coexist without overwrite, and parent
  lineage cannot cross a project boundary.
- Concurrent ingest can reject stale writers without holding a database transaction during
  file I/O.
- Malformed historical input becomes a visible degraded result. It may require source
  repair before a first complete grain can be stored.
- Historical sessions retain the effective definition version that produced their declared
  skills even after project or user definitions change.
- Project identity depends on Claude's project-bucket convention. Derivation lives in one
  helper, and the source tuple remains stored so a future convention change can be
  diagnosed and revised.
