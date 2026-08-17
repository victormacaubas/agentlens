## 1. Types and leaf helpers

- [ ] 1.1 Add `FactToolEvent` and `FactSession` row types to `models` as frozen, slotted, keyword-only dataclasses carrying the fields named in `specs/session-parser` and `specs/session-report`
- [ ] 1.2 Add `ParsedSession` to `models`, holding one session row and its ordered tool-invocation rows, plus the parse-health count and a soundness flag
- [ ] 1.3 Add hashing helpers to `utils`: SHA-256 of text, a canonical-JSON fingerprint using sorted keys and compact separators, and a normalized-absolute-path identity. No domain knowledge in this module
- [ ] 1.4 Add `SystemClock` to `utils` returning `datetime.now(UTC)`, and confirm mypy accepts it wherever `Clock` is expected
- [ ] 1.5 Add `FakeClock` to `tests/fakes.py` returning a fixed instant, giving `Clock` its second implementation
- [ ] 1.6 Unit-test the hashing helpers: stable across processes, independent of key order, and path identity unaffected by offsets, ranges, or replacement text

## 2. Store

- [ ] 2.1 Write the schema for `fact_tool_event` keyed on session plus ordinal, and `fact_session` keyed on the qualified session key, created if absent when the store is opened
- [ ] 2.2 Expose the connection as a context manager so teardown cannot be skipped, and translate `sqlite3.Error` into `StoreError` at the package boundary
- [ ] 2.3 Implement session upsert as one transaction: delete the session's existing invocation rows, insert the new ones, then upsert the session row
- [ ] 2.4 Implement the staleness rule: skip when the incoming content hash matches the stored one, refuse when the incoming modification time is older, otherwise replace. Return an outcome value for skip and refuse rather than raising
- [ ] 2.5 Implement the single-session read used by the report
- [ ] 2.6 Test that the store and its tables are created on first use, and that a caller-supplied location is honoured
- [ ] 2.7 Test that four ingested spawns are four rows, none merged by agent type
- [ ] 2.8 Test that re-ingesting a session leaves no duplicate rows, and that a snapshot with **fewer** invocations than its predecessor leaves no orphan invocation rows
- [ ] 2.9 Test that an older snapshot is refused, an identical snapshot is a no-op, and an unsound snapshot writes nothing
- [ ] 2.10 Test the read path for a session key that is not present

## 3. Ingest

- [ ] 3.1 Derive the owning project and raw transcript ID from the file's path, and refuse a path whose location does not identify an owning project
- [ ] 3.2 Derive the qualified session key from project, session kind, and raw ID, retaining all three components on the row
- [ ] 3.3 Capture the source revision before the read, compute the content hash while streaming, capture it again afterwards, and mark the snapshot unsound on mismatch
- [ ] 3.4 Stream the transcript line by line, counting lines that cannot be parsed instead of aborting or discarding them silently
- [ ] 3.5 Pair each tool invocation with its result through a buffer keyed by tool-use reference, emitting one row per invocation with an ordinal
- [ ] 3.6 Emit any invocation still unpaired at end of file as a row with empty result fields
- [ ] 3.7 Read the metadata sidecar when present and tolerate its absence
- [ ] 3.8 Implement two-link name resolution, sidecar first and a derived fallback second, recording which link won in `name_source`
- [ ] 3.9 Derive the session row from the invocations and the sidecar: turn and invocation counts, per-category counters, distinct files touched, repeated invocations, error and denial counts, duration, and token counts including cache reads
- [ ] 3.10 Mark the snapshot unsound when the transcript yields no usable records or no derivable identity
- [ ] 3.11 Add keyword-only builders for synthetic transcripts and sidecars to `tests/factories.py`, as the single home for the project's belief about the source format
- [ ] 3.12 Test identity: the same transcript twice yields one key, and the same raw ID under two projects yields two keys with their own owning projects
- [ ] 3.13 Test soundness: a file that changes between the two revision reads is rejected and writes nothing
- [ ] 3.14 Test pairing: an invocation with a result, an invocation with none, and that the total row count equals the invocation count with no filtering
- [ ] 3.15 Test resolution: sidecar wins and is recorded; with no sidecar the fallback is used, recorded, and the session is not dropped
- [ ] 3.16 Test parse health: a transcript with some unreadable lines is ingested with the count reported, and a transcript with none usable is rejected
- [ ] 3.17 Test the boundaries: an empty file, a transcript with exactly one invocation, and a transcript with repeated identical invocations

## 4. Render

- [ ] 4.1 Build the JSON document: schema version, generation timestamp from the injected clock, one row per qualified spawn, and the unscored marker
- [ ] 4.2 Write the artifact to a stable path derived from the session key, overwriting in place
- [ ] 4.3 Build the terminal summary naming the agent type, task, volume and health counts, cache-read proportion, unscored state, and artifact path
- [ ] 4.4 Test that no score, verdict, or fix key appears anywhere in the document, at any nesting depth
- [ ] 4.5 Test that an ingested-but-unscored spawn is present with its deterministic fields rather than omitted
- [ ] 4.6 Test that the generation timestamp is timezone-aware and in UTC, using the injected fake clock
- [ ] 4.7 Test that repeated runs leave exactly one artifact file for a session

## 5. CLI and orchestration

- [ ] 5.1 Implement the `core` flow: take a parsed session, persist it, read it back, and hand it to the renderer
- [ ] 5.2 Add the `session` command with `--file`, `--format`, and `--store`, keeping the command body to argument assembly and a single hand-off
- [ ] 5.3 Factor argument parsing into its own testable function rather than testing through the parser
- [ ] 5.4 Map errors to exit codes in one place using the existing `EXIT_CODES` table, walking the MRO for the family
- [ ] 5.5 Implement `main(argv) -> int` with `sys.exit(main())`, constructing `SystemClock` and the store at the composition root and injecting both
- [ ] 5.6 Log the resolved arguments once at startup, on the diagnostic stream
- [ ] 5.7 Add the `[project.scripts]` console-script entry so `uvx agentlens` works
- [ ] 5.8 Test the happy path end to end: a synthetic transcript tree in a temporary directory produces a populated store, an artifact, and exit 0
- [ ] 5.9 Test exit 3 for an unreadable path and exit 3 for a transcript outside a project tree, asserting nothing was written in either case
- [ ] 5.10 Test that `--format json` puts only the JSON document on standard output while warnings go to the diagnostic stream
- [ ] 5.11 Test that a second identical run changes no stored row count and re-renders equivalent content
- [ ] 5.12 Test that no file under a fixture `.claude/` tree is created, modified, or removed, on both the success and failure paths

## 6. Gate and verification

- [ ] 6.1 Run `make check` and get it green
- [ ] 6.2 Re-verify each import contract now that real imports exist: inject `ingest` importing `judge`, `core` importing `sqlite3`, and `render` importing `click`, confirm each breaks the build, then revert
- [ ] 6.3 Confirm no runtime dependency was added beyond the closed set, and that `reports/` and the store path stay out of version control
- [ ] 6.4 Report any friction the layer map caused, particularly `core` carrying all cross-stage wiring, rather than resolving it with a contract exception
- [ ] 6.5 Run the `structure-review` skill and address its findings before archiving
- [ ] 6.6 Promote the session-derivation decision from `design.md` to an ADR, since it binds later changes
