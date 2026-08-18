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

### An unsound snapshot raises; it is not a flag on the result

`ParsedSession` holds only a session row and its invocations, and an instance is
always safe to persist. A snapshot that changed mid-read, yielded no usable
records, or carried no derivable identity raises a `SourceError` subclass at the
point of detection.

The alternative considered was an `is_sound` boolean on `ParsedSession`, with the
unsound case carrying a best-effort session row nobody may write. Rejected: it
creates a populated-looking value whose validity depends on every consumer
remembering to check a flag, and the specs already require these cases to reach the
user as the source-error exit code, so the exception has to be raised somewhere
regardless. Raising at detection makes an unsound parse impossible to persist
rather than merely forbidden, and the taxonomy in `errors.py` exists for exactly
this.

Note the asymmetry with staleness, which *is* an outcome value rather than an
exception: a stale snapshot is a normal, successful decision to keep better data,
while an unsound one is a failure to read at all.

The count of unreadable lines lives on the session row only, not on both types. A
sound parse can report it above zero, so it is health data rather than a soundness
verdict.

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

### Source format facts these decisions rest on

Observed directly from real transcripts under `~/.claude/projects/*/*/subagents/`, not
from the design doc's summary. Where the two disagree, this section is authoritative.

- A transcript holds exactly two record types at the root, `user` and `assistant`,
  each wrapping a `message` object.
- **A tool invocation** is an `assistant` record's `message.content[i]` with
  `type == "tool_use"`, carrying `name`, `input`, and `id`.
- **A tool result** is a `user` record's `message.content[i]` with
  `type == "tool_result"`, carrying `tool_use_id`.
- **`is_error` is omitted when false.** It appears only on failure, so it must be
  read as "present and true", never as a defaulted boolean.
- **`toolDenialKind` sits at the record root**, a sibling of `message`, not inside
  the content item. Two values were observed (`automode-blocked`,
  `permission-rule`) from too small a sample to treat the set as closed, so it is
  stored as free text rather than a constrained enum.
- **`tool_result.content` has two shapes in the wild**: a plain string, or an array
  of `{"type": "text", "text": ...}` blocks. Both occur within a single session,
  varying by which tool answered.
- **A subagent's own identity is the root key `agentId`**, which matches the
  filename. The root key `sessionId` holds the *parent* session's UUID, which is
  the directory containing `subagents/`. These are two different identifiers and
  conflating them would attribute every spawn to its parent.
- The sidecar always carries `agentType`, `description`, `toolUseId`, and
  `spawnDepth`. It optionally carries `parentAgentId` (only at depth 2 or more) and
  `model` (only when the spawn pinned a non-default model). Optional keys must be
  read as absent, not as null.
- Records form a linked list through `uuid` and `parentUuid`, with exactly one
  record per file having `parentUuid == null`.
- **The corpus is live.** Files belonging to an in-flight session are appended to
  while being read. This is not a theoretical hazard, which is why the soundness
  rule above is load-bearing rather than defensive.

### One assistant turn spans several records, and token counts are not additive

A single logical turn is frequently written as several consecutive `assistant`
records sharing one `message.id`: one per content block when the response contains
thinking plus tool calls. Interior fragments carry `stop_reason: null` and the
trailing fragment carries the resolved value.

Two consequences, both of which produce silently wrong numbers if missed:

- **Turns are counted as distinct `message.id` values** among assistant records,
  never as a count of assistant records.
- **`message.usage` is re-emitted cumulatively on each fragment, not split across
  them.** The same `output_tokens` value repeats across consecutive fragments and
  then jumps. Token totals are therefore taken from the *trailing* fragment of each
  `message.id` group and summed across groups. Summing every assistant record's
  `usage` overcounts badly.

This is not a hypothetical. The pre-reboot implementation incremented a turn
counter once per assistant record, so both errors were already present in this
project once and neither would have been caught by a synthetic fixture that models
one record per turn. `tests/factories.py` must be able to build a fragmented turn,
and a test must pin both counts against it.

### Definitions that would otherwise be guessed per call site

- A **turn** is one distinct `message.id` among the transcript's assistant records.
- An **input fingerprint** is a hash of the invocation's arguments serialized
  canonically, with sorted keys and no insignificant whitespace, so two
  semantically identical invocations fingerprint alike and repeated-call detection
  is not defeated by key ordering.
- A **file identity** is a hash of the normalized absolute path only, independent
  of offsets, ranges, or replacement text, so distinct-file counts are not inflated
  by repeated reads of different regions of one file.
- The **cache-read proportion** is `cache_read / (input + cache_read + cache_creation)`.
  The three token fields the source reports are disjoint: `input_tokens` counts
  uncached tokens only. Omitting cache creation from the denominator would report a
  run that rewrote its cache on every turn as having a healthy cache-read rate,
  which inverts the signal the design doc wants from it.
- **Result size** is the character length of the result content: the string itself
  when the content is a string, or the summed length of the text blocks when it is
  an array. Both shapes occur, so one branch is not enough.
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

- **The fixtures are synthetic, but the format they model is now observed**, not
  assumed: the facts above come from real transcripts across 160 files. Two
  behaviors were *not* observed and remain specified from intent rather than
  evidence: a line that fails to parse, and an invocation left unmatched at end of
  file. Both are required behaviors regardless of how often they occur in one
  person's corpus, so they are built and tested; the residual risk is only that
  their real-world shape differs from the fixture's. → Keep the belief in
  `tests/factories.py` alone so there is one place to correct, and make parsing
  degrade rather than crash: unreadable lines are counted, a missing sidecar falls
  back, an unmatched invocation still produces a row.
- **Fixtures can drift from the format after this change.** Nothing re-checks them
  against real data. → The observed facts are written down in this design rather
  than only in code, so a future mismatch can be diagnosed against a stated
  baseline instead of rediscovered.
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

### Friction the import contracts actually produced (found during Section 5)

The prediction above was right, in a specific and mechanical way. The three
"forbidden module" contracts (`sqlite3`, `subprocess`, `click`) defaulted to
`import-linter`'s indirect-checking mode, which walks the whole dependency graph
rather than only direct imports. That mode is incompatible with the layer map's
own requirement the moment a forbidden-module owner is *used* by anyone: as soon
as `core` legitimately calls into `store` for orchestration — which ADR 0001
requires — the chain `core → store → store.connection → sqlite3` is an indirect
import of `sqlite3` from `core`, and the contract broke on real wiring that the
layer map itself mandates. This was not a design flaw in `core`; it was the
contract checking the wrong thing.

The fix applied: `allow_indirect_imports = "true"` on "Only store touches the
database driver," narrowing it to what `CLAUDE.md`'s rule actually says —
"nothing `sqlite3`-shaped leaves `store`" is a statement about direct imports and
driver types in signatures, not about whether a package may depend on `store` at
all. Re-verified (task 6.2) that a literal `import sqlite3` placed directly in
`core` or `cli` still breaks the contract after this change; only the legitimate
`core → store` chain is now permitted.

**The same latent issue exists, unfixed, in "Only cli parses arguments" and "Only
judge spawns processes."** `core` already imports `render` for real (`core.session`
calls `render.document`, `render.artifact`, `render.summary`), so the moment
`render` gains a real `jinja2`/HTML path that also needs `click` for anything, or
`judge` gains a real backend that `core` wires in, the same indirect-chain
contract failure will reproduce. This was not pre-emptively fixed, on the same
principle followed for the `sqlite3` case: fix the contract when the real
violation appears, not before, so the decision is made by whoever is looking at
the actual chain rather than guessed in advance. Whoever implements the `judge`
or HTML-`render` slice should expect to make the same one-line
`allow_indirect_imports` correction and can point to this note and to task 6.2's
re-verification method rather than rediscovering it.

## Open Questions

- Which tool names map to which category counter (`n_reads`, `n_edits`, `n_writes`,
  `n_bash`). A lookup table that will grow as tools are added; getting it
  incomplete affects counter values but changes no spec, no interface, and no task.
- Whether the store path should honor `XDG_CACHE_HOME` rather than a fixed cache
  directory. Affects one default, resolvable later without touching behavior.
