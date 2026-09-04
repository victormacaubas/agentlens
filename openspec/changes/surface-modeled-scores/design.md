## Context

See `proposal.md` — Why for motivation. What shapes the approach is four facts
about the code as it stands after #28.

**The rubric version does not self-disambiguate.** `judge/prompt.py::render_prompt`
renders the narrative alone and carries no rubric text; the dimensions, the JSON
schema, and the instructions reach the judge through a separate surface. Bumping
`RUBRIC_VERSION` therefore leaves `judge_input_hash` unchanged, and
`fact_verdict`'s primary key permits a `v1` and a `v2` row for one spawn at one
hash simultaneously. Cohort ambiguity is the ordinary case after a rubric bump.

**The report path is forbidden from importing `judge`.** `pyproject.toml` holds a
`forbidden` contract, "The deterministic report path never reaches the judge",
naming `agentlens.core.report` and `agentlens.core.ingest_run` against all of
`agentlens.judge`, with indirect imports deliberately disallowed. The join key
this change needs is `hash_text(render_prompt(narrative))`, so the contract is
directly in the way.

**The join already exists.**
`core/spawn_scoring.py::SpawnScoringPreview.check_reusable(bundle, stored)` renders
the prompt, hashes it, and returns the matching stored verdict or `None`.
`WindowScoringPreviewRun` already calls it against a synthetic
`SessionFacts(session=row, tool_events=(), skill_signals=())`, so it needs only a
stored row and a source bundle. Its one limitation is that
`_build_scoring_inputs` reads `RUBRIC_VERSION` from the module rather than taking
it as an argument.

**Deterministic aggregation is SQL and cannot be extended.**
`store/reporting.py` aggregates with `SUM`/`COUNT ... GROUP BY agent_type` over
`fact_session` alone, and both its module docstring and `store-schema`'s
requirement state that verdict data is never joined there. `store` also cannot
import `judge`, so the modeled join key is unreachable from SQL.

## Goals / Non-Goals

**Goals:**

- Make cohort identity explicit at every point a modeled number is produced, so
  incomparability has to be requested rather than fallen into.
- Add the modeled surface without recomputing, relocating, or re-deriving a single
  measured figure.
- Reuse the existing hash-and-lookup rather than writing a second one that can
  drift from the scoring path's.
- Leave `store/reporting.py` untouched, so its rule holds literally rather than by
  reinterpretation.

**Non-Goals:**

- Concurrency. The modeled read is sequential like everything else here.
- A rubric migration path. Verdicts under an old rubric stay readable by naming
  that cohort; nothing rewrites or back-fills them.
- Aggregating findings across spawns. Which fix recurs across a window is a
  separate capability.
- Exposing the trend threshold as a flag. It stays at 5 and stays internal.

## Decisions

### The import contract narrows to the invoking surface

`forbidden_modules` for the report path changes from `agentlens.judge` to
`agentlens.judge.cli_backend` and `agentlens.judge.invocation`. `judge.prompt` and
`judge.rubric` become reachable; the backend does not. Indirect imports stay
disallowed.

The invariant that survives is the one the contract existed to protect: `report`
spends no money and needs no credentials. It is held twice over, because seams are
injected and never defaulted, so the composition root simply never hands
`core.report` a `JudgeBackend`. `core.ingest_run` stays in `source_modules` and
stays judge-free. Per this repo's rule, the amended contract is asserted failing
before it is trusted to pass.

`core/spawn_scoring.py` already imports only `judge.prompt`,
`judge.verdict_validation`, and `judge.rubric` — never `judge.cli_backend` — so
`core.report` reaching the join through it satisfies the narrowed contract without
`allow_indirect_imports`.

*Alternatives considered.* Moving `render_prompt` out of `judge` into `models` or a
new leaf package would make the contract pass, but the prepared prompt is the
judge's input and the layer map assigns `judge` "the prepared prompt view"
explicitly; relocating code to satisfy a contract hides the layering fact the
contract was written to expose. Storing `judge_input_hash` on `fact_session` at
ingest time would keep `core.report` judge-free, but it makes a deterministic
table's contents depend on the judge's prompt template and forces `ingest` to
import `judge` — the same violation, one layer down, plus a measured table that
changes when the rubric's projection changes.

### The cohort is one selector value, split on the first separator

`report` gains `--cohort <rubric_version>/<judge_model>`, split on the first `/` so
a provider-prefixed judge model containing a slash still parses. Matching is exact
string equality against the stored `rubric_version` and `judge_model`, with no
alias resolution, so an alias can only ever name an empty cohort — which is a
`ConfigError`, not a silent empty report.

One flag rather than two because "exactly one cohort" then has exactly one value.
Two flags introduce a half-specified state (`--rubric-version v1` alone) that would
need its own resolution rule, and they would collide semantically with `score`,
where `--judge-model` means the *requested alias*. The single value is also what
the ambiguity error prints, so the error output is a working command line.

`score --judge-model` is the flag misnamed under this project's vocabulary rule.
Renaming a surface #28 just shipped is out of scope here.

### Cohort resolution happens in `core`, before any modeled value is computed

`core` reads the window's deterministic rows, reads every verdict for those
sessions in one store call, and groups them into cohorts. Zero cohorts yields a
report with no cohort named and no modeled rollup. One cohort is selected and
recorded as implied. More than one, with no selector, raises `ConfigError` listing
each cohort in selector form with its spawn coverage.

Cohorts are enumerated within the filtered scope, so `--agent` narrows what counts
as ambiguous. Coverage counts appear in the error to inform the human; they never
break the tie. "Most-covered wins" was rejected because it is still an implied
choice, and its failure mode — a report that silently switches cohorts between runs
as scoring progresses — is the incomparability this change exists to prevent.

### The join is the existing `check_reusable`, parametrized by rubric version

`_build_scoring_inputs` stops reading `RUBRIC_VERSION` from the module and takes it
as an argument; the scoring path passes the constant, and the report path passes
the resolved cohort's version. Nothing else about the lookup changes, so the report
and the scoring path can never disagree about whether a verdict matches a spawn.

Because cohort pins `rubric_version` and `judge_model`, the join pins
`judge_input_hash`, and the spawn is `session_id`, the four-part primary key of
`fact_verdict` is fully constrained: the lookup returns zero or one row by
construction. There is no tiebreak, no ordering, and nothing to dedupe. Tests
assert the property; they do not exercise a deduplicator.

`stale` falls out of the same batch read: a verdict in the cohort whose
`judge_input_hash` differs from the spawn's current hash. It is derived at read
time and never stored, which is the precedent `session-report` already set for
behind-current-input verdicts.

### One new store read, in `store/verdicts.py`, not `store/reporting.py`

`read_verdicts_for_sessions(session_ids)` mirrors the existing
`read_skill_signals_for_sessions`, which is already how the report batches a
per-session read across a window. Cohort enumeration comes from grouping that same
result set in `core`, so there is no second query and no cohort-listing SQL.

This is how modeled data enters the report without breaking `reporting.py`'s rule:
it does not enter `reporting.py` at all. Deterministic reads stay there, modeled
reads live in `verdicts.py`, and `core` joins the two result sets in memory. A
`LEFT JOIN` from `fact_session` to `fact_verdict` was rejected on both counts — it
would put modeled data in the module whose rule forbids it, and it cannot work
anyway, because the join predicate needs a hash only `judge` can compute.

### The modeled rollup is computed in `core`, and the asymmetry is forced

Deterministic aggregation stays SQL in `store/reporting.py`; modeled aggregation is
Python in `core`. That asymmetry is not a preference: the modeled join key needs
`judge.prompt.render_prompt`, and `store` cannot import `judge`. Stating it here so
it does not later read as an oversight to be tidied up.

### The modeled population is scored spawns, and the trend gate is duplicated

The deterministic population counts every qualifying spawn; the modeled population
counts only spawns whose verdict matched. Both appear in the document. Reusing
`n_spawns` for the modeled gate would present a trend over three observations as
comparable, which is the same class of error as averaging across cohorts.

The gate itself is not extracted into a shared helper. It is a two-term comparison
against a threshold; the deterministic one stays in
`store/reporting.py::_build_agent_rollup` and the modeled one lives in `core`. A
shared helper would have to sit in `models`, which holds no logic by rule, or in
`utils`, which is for leaf primitives, and neither placement beats one duplicated
comparison. What *is* shared is what must not drift: the `TrendStatus` enum and
`min_sessions_for_trend` on `ResolvedWindow`. A test asserts both families gate at
the same threshold.

### `PreparedIngestBatch` carries the bundle map

`prepare_ingest_batch` already discovers and parses every subagent source, then
discards the bundles. `core/window_scoring.py::_WindowWorklistBuilder.build`
repeats that discover-and-parse to keep `bundles_by_session_id`. The report path
needs the same map, so it goes on `PreparedIngestBatch` and
`_WindowWorklistBuilder` consumes it instead of rebuilding it.

This edits code #28 just shipped, deliberately: the alternative leaves three copies
of the same discovery, and the third would be the one that drifts.

The prior comparison window needs hashing too, since the modeled trend needs prior
averages. That costs nothing extra to discover, because `prepare_ingest_batch`
already parses everything under `projects_root` regardless of window. What is added
per spawn is one bounded string render — capped at `PROJECTION_CEILING_BYTES`,
400 KB — and one SHA-256. Against a transcript parse that already happens, it is
noise.

### Rendering reuses the one untrusted boundary that exists

Report spawn rows reuse `render/document.py::_build_verdict_row`, so
`VERDICT_PROVENANCE` marks `evidence`, `recommendation`, `rationale`, and `target`
as untrusted model output in exactly one place rather than two that can diverge.
The terminal summary follows the session-summary precedent: cohort, scores, and
per-state counts, and none of that text.

`REPORT_SCHEMA_VERSION` goes 1 to 2. `render/document.py`'s `SCHEMA_VERSION = 3`
versions the single-session document and is not touched. `--format json` stays the
only machine-readable format; HTML is Phase 5 and jinja2 stays unused.

## Risks / Trade-offs

**A narrowed import contract is weaker than the one it replaces** → The guarantee
moves from "cannot import the package" to "cannot import the invoking modules",
which a future module inside `judge` could slip past if it grows a subprocess call.
Mitigated by naming `cli_backend` and `invocation` explicitly rather than
allow-listing `prompt`, so a *new* invoking module is caught by review rather than
silently permitted — and by the composition root never handing `core.report` a
backend, which no import contract is responsible for.

**The two trends disagreeing will read as a bug** → A forty-spawn window showing a
comparable deterministic trend beside an `insufficient_data` modeled one looks
wrong until you notice the populations differ. Mitigated by carrying both
populations in the document and in the terminal summary, so the reason is on the
same screen as the symptom.

**The first rubric bump is now a breaking user experience** → Anyone who bumps
`RUBRIC_VERSION` and re-scores gets a `ConfigError` on their next `report` until
they pass `--cohort`. This is the intended behavior, not a regression, but it will
arrive unannounced. Mitigated by the error naming both cohorts with their coverage
in copy-pasteable selector form.

**Parametrizing `_build_scoring_inputs` touches the scoring path** → A mistake there
breaks scoring, not just reporting. Mitigated because it is a pure signature
change with the constant threaded through from the one existing caller, and #27's
and #28's reuse and claim tests cover the behavior it must preserve.

**Rendering prompts during `report` couples reporting to the projection** → A change
to `render_prompt` now changes what `report` considers stale, not only what the
judge is sent. That coupling is real and unavoidable: it is the same coupling that
makes the hash a valid cache key, and hiding it would mean the report matching
verdicts by a rule the scoring path does not use.

## Migration Plan

No data migration. The store is a disposable cache rebuilt from source, and no
stored column changes.

`REPORT_SCHEMA_VERSION` advancing to 2 is the only consumer-visible break, and it
is additive: every field version 1 defined keeps its meaning and its location. A
consumer pinned to version 1 fails its version check rather than misreading a
document, which is the point of advancing it.

Rollback is reverting the change. Verdicts written before it remain readable
afterward, and verdicts written after it are unaffected by reverting, because
nothing about `fact_verdict` changes.
