## Context

See `proposal.md` for motivation and `specs/` for the behavior contract.

The constraints that shape this design are already fixed by the baseline and are
not up for negotiation here:

- `ingest`, `store`, `judge`, and `render` are independent siblings and cannot
  import each other. Every cross-stage flow passes through `core`
  (`docs/adr/0001`, enforced by `lint-imports`).
- `sqlite3` may only be imported inside `store`, `click` only inside `cli`.
- The store is a rebuildable cache, so schema changes are handled by rebuild
  rather than migration (`docs/adr/0003`).
- Injection is required, never defaulted, and `cli` is the only composition root
  (`docs/adr/0004`).
- Fixtures are synthetic (`docs/adr/0006`), so this change's belief about the
  transcript format lives in `tests/factories.py` and is not verified against real
  data.

The one architectural question the baseline deliberately left open is where a
session row gets built. ADR 0001 says the first change that needs it must decide
and say so. This is that change.

## Goals / Non-Goals

**Goals:**

- Prove the wiring: one path through all five layers, exercised by real imports so
  the five import contracts are tested against something rather than nothing.
- Establish the handoff types between `ingest`, `core`, and `store` in a way that
  does not require the sibling packages to know about each other.
- Give the `Clock` Protocol its second implementation, so it stops being
  speculative.
- Make the snapshot-soundness rule real rather than aspirational, since every later
  correctness property depends on it.

**Non-Goals:**

- Any query that spans more than one session. Windowed reporting has no design
  input from this slice beyond the row shape.
- Performance work. Correctness of the grain comes first; a transcript is a single
  file read once.
- A stable JSON schema. The document is versioned precisely so version 1 can be
  wrong.

## Decisions

### The session row is built in Python, inside `ingest`

`ingest` reads the file once and returns both the session row and its
tool-invocation rows as one value. `core` hands that value to `store`, which
writes it.

The design doc's "store this, derive the rest" argues for deriving the session row
in SQL, and that was the alternative considered. It does not survive contact with
the field list: the agent type, task description, spawning tool-use reference,
nesting depth, owning project, source revision, and name-resolution outcome are not
aggregations of any tool invocation. They come from the sidecar, the file path, and
the file's own stat. A pure-SQL derivation cannot produce them.

That leaves a split (counts in SQL, the rest in Python), which was also considered
and rejected: it constructs one row across two packages, so neither owns whether
the row is correct, and a reader has to visit both to understand one record.

The cost, accepted: recomputing session rows after a schema change means re-reading
source rather than re-running a query. `docs/adr/0003` already sanctions exactly
that, because the store is a cache.

This decision binds later changes and should be promoted to an ADR at archive time.

### `ParsedSession` is the handoff type, and it lives in `models`

A frozen dataclass holding one session row and its ordered tool-invocation rows.
Placing it in `models` is what lets `ingest` produce it and `store` consume it
while remaining unable to import each other. Both depend on the type, neither on
the other.

The alternative, having `store` accept loose arguments, would push the row's field
list into a call signature that changes every time a column is added.

### Streaming read with a bounded pairing buffer

The parser hashes and parses line by line rather than reading the file into memory,
because transcript size is unbounded.

Pairing an invocation with its result requires holding unmatched invocations until
their result arrives. That buffer is keyed by tool-use reference and is bounded by
the number of calls in flight at once, which is small. Anything still unmatched at
end of file becomes a row with empty result fields, which is the behavior the spec
requires.

### Soundness is stat, read, stat, compare

Capture the revision, stream the file while computing the content hash, capture the
revision again, and compare. A mismatch means the snapshot is unsound and nothing
is written.

Alternatives rejected: file locking is not portable and the source is not ours to
lock, and copying to a temporary file first doubles the I/O to buy the same
guarantee this comparison already gives.

### Upsert deletes the session's invocation rows before reinserting

Replacing a session is one transaction: delete the tool-invocation rows for that
session key, insert the new ones, then upsert the session row.

Row-by-row `INSERT OR REPLACE` was considered and is wrong. A newer snapshot can
contain *fewer* invocations than the one it replaces, for example when a transcript
is re-read after being truncated. Replacing by ordinal would leave the surplus rows
from the previous snapshot in place, silently inflating every count for that
session.

### Staleness is decided by content hash first, then modification time

If the incoming content hash equals the stored one, the snapshot is the same data
and the write is skipped, which is what makes a re-run free. Otherwise, if the
incoming modification time is older than the stored one, the incoming snapshot is
stale and refused. Otherwise it replaces.

Comparing content before time is deliberate: modification times can move without
content changing, and a re-run should not pay to rewrite identical rows.

### Definitions that would otherwise be guessed per call site

- A **turn** is one assistant record in the transcript.
- An **input fingerprint** is a hash of the invocation's arguments serialized
  canonically, with sorted keys and no insignificant whitespace, so two
  semantically identical invocations fingerprint alike and repeated-call detection
  is not defeated by key ordering.
- A **file identity** is a hash of the normalized absolute path only, independent
  of offsets, ranges, or replacement text, so distinct-file counts are not inflated
  by repeated reads of different regions of one file.
- The **default store path** is under the user's cache directory. `--store` overrides it.
- `--format json` streams to standard output and writes no file. Without it, the
  artifact is written and a human summary is printed.

### A transcript that is not a subagent run is refused

This slice handles subagent transcripts only. A path that does not name one, or
whose location does not identify an owning project, is refused with the
source-error exit code and a message naming what was expected.

Guessing a project or synthesizing an identity was rejected: an unqualified key can
collide with a real one, and a wrong row is worse than a refused run.

## Risks / Trade-offs

- **The synthetic-fixture format belief is unverified.** If the real transcript
  shape differs, every test passes and the tool fails on real data. → Keep the
  belief in `tests/factories.py` alone so there is one place to correct, and make
  parsing degrade rather than crash: unreadable lines are counted, a missing sidecar
  falls back, an unmatched invocation still produces a row.
- **Project derivation depends on Claude Code's directory layout.** If that
  encoding changes, identity derivation breaks. → Fail loudly with exit 3 rather
  than fall back to an unqualified key, so the breakage is visible immediately
  instead of producing colliding rows.
- **Deriving in Python means the store cannot recompute session rows on its own.**
  → Accepted; re-reading source is the sanctioned rebuild path.
- **Refusing a whole snapshot on zero usable records could reject a file a more
  tolerant parser would partly read.** → The spec draws the line at "no usable
  records or no identity", not at "any bad line", so a mostly-good file is still
  ingested with its error count reported.
- **`reports/` is created in the working directory**, so running from an unexpected
  directory scatters artifacts. → The summary always prints the artifact path, so
  where it went is never a guess.
- **First real use of the import contracts may reveal the layer map is awkward**,
  most likely in `core` carrying all cross-stage wiring. → That friction was
  predicted in ADR 0001's consequences. Report it rather than working around it
  with a contract exception.

## Open Questions

- Which tool names map to which category counter (`n_reads`, `n_edits`, `n_writes`,
  `n_bash`). A lookup table that will grow as tools are added; getting it
  incomplete affects counter values but changes no spec, no interface, and no task.
- Whether the store path should honor `XDG_CACHE_HOME` rather than a fixed cache
  directory. Affects one default, resolvable later without touching behavior.
