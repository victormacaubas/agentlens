## 1. Types and leaf helpers

- [x] 1.1 Add `FactToolEvent` and `FactSession` row types to `models` as frozen, slotted, keyword-only dataclasses carrying the fields named in `specs/session-parser` and `specs/session-report`
- [x] 1.2 Add `SessionFacts` to `models`, holding one session row and its ordered tool-invocation rows. Produced by `ingest`, returned by `store`'s read, consumed by `render`
- [x] 1.3 Add hashing helpers to `utils`: SHA-256 of text, a canonical-JSON fingerprint using sorted keys and compact separators, and a normalized-absolute-path identity. No domain knowledge in this module
- [x] 1.4 Add `SystemClock` to `utils` returning `datetime.now(UTC)`, and confirm mypy accepts it wherever `Clock` is expected
- [x] 1.5 Add `FakeClock` to `tests/fakes.py` returning a fixed instant, giving `Clock` its second implementation
- [x] 1.6 Unit-test the hashing helpers: stable across processes, independent of key order, and path identity unaffected by offsets, ranges, or replacement text
- [x] 1.7 Add keyword-only builders to `tests/factories.py` for domain rows and for synthetic transcripts and sidecars, modelled on the observed format in `design.md`. Must be able to build: a fragmented turn (several assistant records sharing one `message.id` with re-emitted cumulative `usage`), a successful result with `is_error` absent, a failing result with it present, both `tool_result.content` shapes, a root-level `toolDenialKind`, an unmatched invocation at end of file, an unparseable line, and a sidecar with and without its optional `parentAgentId` and `model` keys

## 2. Store

- [x] 2.1 Write the schema for `fact_tool_event` keyed on session plus ordinal, and `fact_session` keyed on the qualified session key, created if absent when the store is opened
- [x] 2.2 Expose the connection as a context manager so teardown cannot be skipped, and translate `sqlite3.Error` into `StoreError` at the package boundary
- [x] 2.3 Implement session upsert as one transaction: delete the session's existing invocation rows, insert the new ones, then upsert the session row
- [x] 2.4 Implement the staleness rule: skip when the incoming content hash matches the stored one, refuse when the incoming modification time is older, otherwise replace. Return an outcome value for skip and refuse rather than raising
- [x] 2.5 Implement the single-session read used by the report
- [x] 2.6 Test that the store and its tables are created on first use, and that a caller-supplied location is honoured
- [x] 2.7 Test that four ingested spawns are four rows, none merged by agent type
- [x] 2.8 Test that re-ingesting a session leaves no duplicate rows, and that a snapshot with **fewer** invocations than its predecessor leaves no orphan invocation rows
- [x] 2.9 Test that an older snapshot is refused, an identical snapshot is a no-op, and an unsound snapshot writes nothing
- [x] 2.10 Test the read path for a session key that is not present

## 3. Ingest

- [x] 3.1 Derive the owning project and raw transcript ID from the file's path, and refuse a path whose location does not identify an owning project
- [x] 3.2 Derive the qualified session key from project, session kind, and raw ID, retaining all three components on the row
- [x] 3.3 Capture the source revision before the read, compute the content hash while streaming, capture it again afterwards, and mark the snapshot unsound on mismatch
- [x] 3.4 Stream the transcript line by line, counting lines that cannot be parsed instead of aborting or discarding them silently
- [x] 3.5 Pair each tool invocation with its result through a buffer keyed by tool-use reference, emitting one row per invocation with an ordinal
- [x] 3.6 Emit any invocation still unpaired at end of file as a row with empty result fields
- [x] 3.7 Read the metadata sidecar when present and tolerate its absence
- [x] 3.8 Implement two-link name resolution, sidecar first and a derived fallback second, recording which link won in `name_source`
- [x] 3.9 Derive the session row from the invocations and the sidecar: turn and invocation counts, per-category counters, distinct files touched, repeated invocations, error and denial counts, duration, and token counts including both cache reads and cache creation
- [x] 3.10 Group assistant records by `message.id` so a fragmented turn counts once, and take each group's token figures from its trailing fragment rather than summing every record. See `design.md`, "One assistant turn spans several records"
- [x] 3.11 Raise a `SourceError` subclass when the snapshot changed mid-read, yielded no usable records, or has no derivable identity. Never return a flagged-but-populated `ParsedSession`. See `design.md`, "An unsound snapshot raises"
- [x] 3.12 Test identity: the same transcript twice yields one key, and the same raw ID under two projects yields two keys with their own owning projects
- [x] 3.13 Test soundness: a file that changes between the two revision reads is rejected and writes nothing
- [x] 3.14 Test pairing: an invocation with a result, an invocation with none, and that the total row count equals the invocation count with no filtering
- [x] 3.15 Test resolution: sidecar wins and is recorded; with no sidecar the fallback is used, recorded, and the session is not dropped
- [x] 3.16 Test parse health: a transcript with some unreadable lines is ingested with the count reported, and a transcript with none usable is rejected
- [x] 3.17 Test the boundaries: an empty file, a transcript with exactly one invocation, and a transcript with repeated identical invocations
- [x] 3.18 Test the fragmented turn against a fixture whose assistant records share one `message.id` with re-emitted cumulative `usage`: assert the turn count is 1, not the record count, and that token totals equal the trailing fragment's figures rather than their sum
- [x] 3.19 Test that `is_error` is read as present-and-true rather than as a defaulted boolean, and that a successful result with the key absent is not counted as an error
- [x] 3.20 Test that result size is computed for both content shapes, a plain string and an array of text blocks

## 4. Render

- [x] 4.1 Build the JSON document: schema version, generation timestamp from the injected clock, one row per qualified spawn, and the unscored marker
- [x] 4.2 Write the artifact to a stable path derived from the session key, overwriting in place
- [x] 4.3 Build the terminal summary naming the agent type, task, volume and health counts, cache-read proportion, unscored state, and artifact path
- [x] 4.4 Test that no score, verdict, or fix key appears anywhere in the document, at any nesting depth
- [x] 4.5 Test that an ingested-but-unscored spawn is present with its deterministic fields rather than omitted
- [x] 4.6 Test that the generation timestamp is timezone-aware and in UTC, using the injected fake clock
- [x] 4.7 Test that repeated runs leave exactly one artifact file for a session

## 5. CLI and orchestration

- [x] 5.1 Implement the `core` flow: take a parsed session, persist it, read it back, and hand it to the renderer
- [x] 5.2 Add the `session` command with `--file`, `--format`, and `--store`, keeping the command body to argument assembly and a single hand-off
- [x] 5.3 Factor argument parsing into its own testable function rather than testing through the parser
- [x] 5.4 Map errors to exit codes in one place using the existing `EXIT_CODES` table, walking the MRO for the family
- [x] 5.5 Implement `main(argv) -> int` with `sys.exit(main())`, constructing `SystemClock` and the store at the composition root and injecting both
- [x] 5.6 Log the resolved arguments once at startup, on the diagnostic stream
- [x] 5.7 Add the `[project.scripts]` console-script entry so `uvx agentlens` works
- [x] 5.8 Test the happy path end to end: a synthetic transcript tree in a temporary directory produces a populated store, an artifact, and exit 0
- [x] 5.9 Test exit 3 for an unreadable path and exit 3 for a transcript outside a project tree, asserting nothing was written in either case
- [x] 5.10 Test that `--format json` puts only the JSON document on standard output while warnings go to the diagnostic stream
- [x] 5.11 Test that a second identical run changes no stored row count and re-renders equivalent content
- [x] 5.12 Test that no file under a fixture `.claude/` tree is created, modified, or removed, on both the success and failure paths

## 6. Gate and verification

- [x] 6.1 Run `make check` and get it green
- [x] 6.2 Re-verify each import contract now that real imports exist: inject `ingest` importing `judge`, `core` importing `sqlite3`, and `render` importing `click`, confirm each breaks the build, then revert
- [x] 6.3 Confirm no runtime dependency was added beyond the closed set, and that `reports/` and the store path stay out of version control
- [x] 6.4 Report any friction the layer map caused, particularly `core` carrying all cross-stage wiring, rather than resolving it with a contract exception
- [x] 6.5 Promote the session-derivation decision from `design.md` to an ADR, since it binds later changes
