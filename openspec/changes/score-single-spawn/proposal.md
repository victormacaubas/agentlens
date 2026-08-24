## Why

Phase 3's exit criterion is that sessions get scored and produce fix proposals, and
nothing in the repo scores anything today: `judge/` is empty, and `JudgeBackend` /
`JudgeResponse` are declared but never constructed, with no test fake. ADR 0004 says
a Protocol earns its place by having two implementations, so that seam is currently
unearned. This change is the tracer bullet that makes the rest of Phase 3
incremental: one spawn, one judge call, one verdict row, no reuse.

It also closes a stale contract. The hardened `claude` invocation in
`docs/agentlens-design.md` was re-verified against CLI 2.1.241 during scoping and
found to be wrong in two ways rather than one: `--max-turns` no longer exists, and
the invocation as written **does not authenticate at all** because `--bare` reads an
`apiKeyHelper` only from `--settings`, not from `--setting-sources "user"`. A change
that builds the judge without fixing the contract would ship a judge that cannot
run.

## What Changes

- `agentlens session --file <path>` gains an opt-in scoring flag. Scoring is never
  default, because a default that spends money on a command users already run for
  deterministic facts is a trap.
- A new `judge` package: a pure argv-construction function for the hardened
  invocation, a thin subprocess execution wrapper, the pinned rubric, the prepared
  transcript projection, and a hand-written verdict validator returning a frozen
  dataclass.
- A `ClaudeCliJudge` implementation of `JudgeBackend`, and its fake in
  `tests/fakes.py` in the same change, which is what earns the Protocol.
- A `fact_verdict` table keyed per ADR 0003 on
  `session_id + judge_input_hash + rubric_version + judge_model`.
- The scored branch of both output surfaces: the JSON artifact carries the verdict
  with its provenance split, and the terminal summary shows scores only.
- Exit code 5 (`JudgeError`) becomes reachable from `agentlens session` for the
  first time.
- **Amendment to `docs/agentlens-design.md`**: the hardened invocation gains
  `--settings <user settings path>`, drops `--max-turns`, and gains a wall-clock
  timeout plus `--max-budget-usd`. Recorded as an ADR because it binds every future
  judge call.

## Capabilities

### New Capabilities

- `spawn-scoring`: scoring one spawn on demand. Covers the hardened judge
  invocation and its read-only and reproducibility guarantees, the pinned versioned
  rubric, the prepared transcript projection that is hashed as the verdict's
  identity, local validation of the returned verdict, the provenance split between
  locally derived scores and untrusted model text, agentlens's own cost accounting,
  and fail-fast behavior when the CLI is missing, unauthenticated, hanging, or
  answering unusably.

### Modified Capabilities

- `session-command`: gains the opt-in scoring flag, the resolved-argument logging of
  it, `--dryrun` reporting what would be scored without calling the judge or
  writing, and exit code 5 for `JudgeError`, which this spec does not currently
  carry.
- `session-report`: gains the scored branch. The spec today specifies only the
  unscored case, where no score, verdict, or fix field is present or defaulted
  anywhere. That promise must hold unchanged while the scored case gains a shape.
- `store-schema`: gains `fact_verdict`, its natural key, and its upsert behavior on
  re-scoring the same identity.

## Impact

**Code.** New `agentlens/judge/` package. New table in `store` (`schema.py`,
`rows.py`, `operations.py`). New orchestration in `core` for the scoring run.
`render` gains the scored branch of the terminal summary and the JSON document.
`cli.py` gains the flag and composes the real backend for the first time.
`models` gains the verdict domain types.

**Docs.** `docs/agentlens-design.md` invocation contract amended. A new ADR records
what bounds a judge call and how it authenticates. `DESIGN.md` cites a
non-existent "ADR 0011" for the untrusted-output rule; that dangling reference is
corrected to point at the real source.

**Dependencies.** None added. The rubric's JSON Schema is a dict literal we author
and hand to the CLI, which ADR 0002 already anticipated when it excluded
`jsonschema`.

**Cost.** This is the first code path in the project that spends money. Every test
in the change runs against the fake; the one test that touches the real CLI is
`@pytest.mark.integration` and opt-in via `make integration`.

**Known limitation carried forward.** The most specific model identifier the
response envelope offers is `modelUsage`'s key, empirically `claude-sonnet-5`, which
carries no date stamp and so floats across point releases. ADR 0003 wants verdicts
comparable under a concrete model; this is the closest the envelope allows, and the
gap is recorded rather than papered over.
