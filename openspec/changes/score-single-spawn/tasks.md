## 1. Domain types and the narrative the judge is shown

- [x] 1.1 Add the verdict domain types to `models`: the verdict, its four dimension
  scores with evidence, its suggested fixes, and the provenance split marking which
  fields are locally derived and which are untrusted model output. Frozen, slotted,
  keyword-only, no logic and no I/O, per the layer map. Verify by constructing a fully
  populated verdict and asserting the provenance split names every field, and that no
  field defaults in a way that would let a missing score read as a real one.

- [x] 1.2 Add `SpawnNarrative` to `models` and its extraction to `ingest`, reusing
  `ingest.records.assistant_message_groups` rather than re-deriving the grouping.
  Cover: a logical turn fragmented across several records sharing one `message.id`
  appears once; a run with no assistant text; a run with no tool events; an assistant
  message whose content carries no text block; a malformed content block; and stable
  ordering across repeated extraction of the same records. Verify the extracted
  narrative for a synthetic transcript matches the prompt, messages, and tool sequence
  the factory built.

## 2. The rubric, the prepared prompt, and verdict validation

- [ ] 2.1 Add the pinned rubric to `judge`: the four dimensions, the 0-to-5 scale, the
  verdict JSON Schema dict literal handed to the CLI, the judge instructions, and the
  `rubric_version` string. Add the pinning test that ties the rubric's content digest
  to the declared version. Verify by asserting the pinning test **fails** when the
  rubric text is edited without bumping the version before trusting it to pass.

- [ ] 2.2 Render `SpawnNarrative` into the prepared prompt in `judge`, with the
  head-and-tail per-message cap, the whole-projection ceiling, the tool-event count
  cap, and an in-band elision marker at every point content was shortened. Cover: a
  narrative under every cap renders unchanged; a long message is shortened and marked
  rather than dropped; the global ceiling engages and marks itself; the tool cap
  engages and marks itself; two runs differing only in what was elided produce
  different prompts and therefore different hashes; an empty narrative; and the same
  narrative rendering byte-identically across repeated calls. Verify the hash is
  stable and that no cap path can produce a prompt that fails to state it is partial.

- [ ] 2.3 Add the hand-written verdict validator to `judge`, returning a frozen
  verdict or raising `JudgeResponseError`. Cover: a well-formed verdict; a dimension
  score outside 0 to 5; a missing dimension; a dimension the rubric does not define; a
  non-integer score; absent suggested fixes; and an empty or absent structured output.
  Verify each rejection names what was wrong, and that no rejection path returns a
  partially repaired verdict.

## 3. The judge backend and its fake

- [ ] 3.1 Build the hardened argv as a pure function in `judge`, per design.md's
  amended invocation. Cover: every element of the contract present; the expanded user
  settings path; the spend ceiling; the temporary working directory; the environment
  reduced to `PATH`, `HOME`, and `ANTHROPIC_*` with nothing else surviving; and no
  `--max-turns`. Verify by asserting on the constructed argv and env directly, since
  ADR 0004 makes this a security control that carries its own assertions.

- [ ] 3.2 Add `ClaudeCliJudge` implementing `JudgeBackend`, with the timeout and spend
  ceiling as constructor arguments, envelope parsing, and error translation at the
  package boundary; and add its fake to `tests/fakes.py` in this same task, which is
  what earns the Protocol under ADR 0004. Cover: a successful envelope; `is_error`
  true while `subtype` reports success; `total_cost_usd` arriving as an int and as a
  float; `modelUsage` with zero, one, and several keys; the not-logged-in message; a
  credential-helper failure reported distinctly from not being authenticated; the
  binary being absent; the timeout expiring; and undecodable JSON. Verify each maps to
  the right `JudgeError` subclass with a message naming the cause, and that no
  `subprocess` type appears in any signature the package exposes.

- [ ] 3.3 Add the single `@pytest.mark.integration` canary that runs the real hardened
  invocation against the installed `claude` and asserts it authenticates and returns a
  parseable envelope. Verify it passes under `make integration` and is not collected by
  `make test`.

## 4. Verdict storage

- [ ] 4.1 Add `fact_verdict` to `store`: schema, row mapping, and upsert on the natural
  key of session, judge-input hash, rubric version, and resolved model. Cover: a fully
  populated verdict round-tripping every field including provenance and cost;
  re-scoring the same identity replacing the row; the same spawn under two resolved
  models producing two rows; a rubric version change producing a separate row; the
  deterministic tables being unchanged by scoring; the table being created on first
  use against a store that predates it; and reading verdicts for a session that has
  none. Verify reads go by column name, never by position.

## 5. Orchestration and the two surfaces

- [ ] 5.1 Add the scoring run to `core`, composing narrative extraction, the injected
  judge, validation, and persistence. Cover: scoring not requested makes no judge call
  and leaves observable behavior identical to a pre-scoring run; scoring requested
  produces exactly one call and one verdict; `--dryrun` with scoring requested makes no
  call, spends nothing, writes nothing, and logs the spawn, model, and verdict identity
  it would have written; a successful ingest followed by a failed scoring attempt keeps
  the deterministic facts and reports a scoring failure; and a call whose verdict is
  rejected still reports the cost already spent. Verify against the fake judge with no
  patching, since a missing seam is the only reason to reach for `unittest.mock`.

- [ ] 5.2 Add the scored branch to `render`: the JSON document's verdict fields with a
  machine-readable provenance split, and the terminal summary showing overall and
  dimension scores with the artifact path. Cover: a scored row carrying every field the
  spec names; a document mixing a scored and an unscored row where absence stays absent
  rather than becoming null, zero, or empty; the schema version differing from the
  pre-scoring shape; evidence and fix text containing control characters, newlines, and
  shell-shaped text never reaching the summary and leaving its shape unchanged; the
  summary naming judge cost in dollars and tokens; and analyzed-agent usage appearing
  with no currency figure anywhere. Verify no rendered surface emits anything shaped
  like a patch, diff, or runnable command.

- [ ] 5.3 Add the opt-in scoring flag to `cli.py`, construct the real backend there as
  the only composition root, extend the single exit-code mapping to 5, and log the
  resolved arguments once as JSON including whether scoring was requested and which
  model. Cover: the flag absent and present; a judge failure exiting 5 and not 3 or 4;
  a source failure still exiting 3 when scoring was requested; and the resolved-argument
  line being emitted once, on the diagnostic stream, leaving stdout parseable. Verify
  by calling the parsing function and `main` directly rather than through flag strings
  in every test.

## 6. Contracts, documentation, and the merge gate

- [ ] 6.1 Verify the existing import contracts still hold with the scoring path in
  place: `judge` reaching neither `ingest` nor `store`, `subprocess` confined to
  `judge`, and the deterministic report path still unable to reach a judge now that
  `core` legitimately depends on it. Verify by asserting `lint-imports` reports the
  report-path contract BROKEN when a judge import is temporarily added to
  `core/report.py`, then removing it.

- [ ] 6.2 Amend `docs/agentlens-design.md`'s hardened invocation to add `--settings`,
  drop `--max-turns`, add the wall-clock timeout and spend ceiling, and correct the
  claim that `--tools ""` bounds the call to one turn. Write the new ADR recording what
  bounds a judge call, how it authenticates, why the resolved model comes from
  `modelUsage`, and the floating-identifier limitation, in Nygard format. Repoint
  `DESIGN.md`'s dangling "ADR 0011" citation at the real source of the untrusted-output
  rule. Verify the invocation in the doc matches the argv the tests in 3.1 assert on.

- [ ] 6.3 Run `make check` once for the whole change and confirm the full gate passes:
  tests, typing, lint, and every import contract.
