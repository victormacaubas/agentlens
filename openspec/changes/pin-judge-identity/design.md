## Context

`fact_verdict`'s primary key is `(session_id, rubric_version, judge_model)`. That triple is the tool's integrity guarantee: it is what lets `report` claim that a score moved between two windows because the *agent* changed, not because the measurement changed.

Today `judge_model` holds whatever string reached `--judge-model`, defaulting to the alias `"sonnet"`. `ScoringLoop._score_session` then overwrites whatever the backend produced with that same configured alias via `replace(verdict, judge_model=self.judge_model)`, so even a backend that knew the real model could not report it.

Aliases float by design. Probes against the installed CLI confirm the envelope exposes the resolution:

| `--model` | `modelUsage` key | `canonicalModel` |
|---|---|---|
| `sonnet` | `claude-sonnet-5` | `claude-sonnet-5` |
| `opus` | `claude-opus-5` | `claude-opus-5` |
| `haiku` | `claude-haiku-4-5-20251001` | `claude-haiku-4-5` |

The haiku row matters more than it looks. For the two frontier aliases the map key and `canonicalModel` agree, which made them look interchangeable; for haiku they **diverge**, and the map key is the dated snapshot while `canonicalModel` is the undated family name. That settles which field to read — see D1.

So when `sonnet` advances to a new release, verdicts scored before and after collide on the same key. The store reports a delta; the delta is measurement drift. There is no error, no warning, and no way to tell after the fact which model produced a given row.

Two conditions make now the right moment. `fact_verdict` and `fact_session` both hold zero rows, so there is nothing to migrate or re-score. And `docs/agentlens-design.md` §2 already states the intent ("Pin `--model`; it's part of the cache key") — this change closes the gap between the stated design and the code rather than introducing a new position.

The cost estimate is a smaller, adjacent defect in the same files. `PER_SESSION_COST_ESTIMATE` was written before any judge call existed: `sonnet` at $0.025, `opus` at $0.15, default $0.05.

An earlier draft of this change claimed a real judge call costs ~$0.0006 and proposed re-basing the table down by roughly 40×. **That figure was wrong**, measured against a trivial one-line prompt rather than a prepared transcript view. Re-measured against an 18KB synthetic view (the size the design doc specifies) with the hardened flags from `harden-judge-invocation`:

| model | measured per-session cost |
|---|---|
| sonnet | $0.0246, $0.0249, $0.0663, $0.0710 (mean ≈ $0.045) |
| opus | $0.119 |

So the direction is the opposite of the earlier draft's: the existing `sonnet` estimate of $0.025 is roughly **2–3× too low**, not 40× too high, and is at the very bottom of the observed range. `opus` at $0.15 is about right, slightly conservative. The default of $0.05 is plausible for sonnet-class work and low for opus-class.

Two things drive the spread. Output tokens vary run to run (1207–1523 observed on sonnet) and dominate cost at these input sizes. And `--max-turns 3` means a schema-validation retry can multiply a call, while every measurement above returned `num_turns: 1` — so observed single-turn costs are a floor, not a ceiling.

The defect is therefore real but inverted: the gate currently **understates**, which is the dangerous direction. It quotes a number the run will exceed.

## Goals / Non-Goals

**Goals:**

- A verdict row records the concrete model that produced it, so two rows sharing a key were graded by the same model.
- When a floating alias advances, previously-scored sessions are correctly identified as unscored under the new model rather than silently colliding.
- Aliases remain usable at the CLI — convenience at the input boundary, precision in the stored identity.
- The confirmation-gate estimate is within an order of magnitude of reality.
- What makes two verdicts comparable is written down once, as an ADR, because three separate mechanisms (rubric version, model identity, judge context) all have to hold and only one of them is obvious.

**Non-Goals:**

- **Judge-context identity as a stored field.** `--bare` plus pinned setting sources (from `harden-judge-invocation`) make the judge context fixed, so it does not need to be a key column. The ADR records *why* it is safe to leave out; if a future change makes context configurable, that change adds the column.
- **Rubric-quality calibration.** Measuring judge variance across identical re-runs, and maintaining hand-labelled anchor sessions to evaluate a rubric bump, are real gaps but they are evaluation work rather than a code change. Noted here so they are not mistaken for solved.
- **Re-scoring or migration tooling.** Unnecessary while the store is empty; a future change that needs it can add it against real data.
- **Changing `--max-turns`, the rubric, or the verdict schema.**

## Decisions

### D1: Read the concrete model from the envelope's `modelUsage` **key**, not `canonicalModel`

The response envelope's `modelUsage` map is keyed by concrete model ID and each entry carries a `canonicalModel` field. The backend reads the **map key** and reports it as the verdict's `judge_model`, falling back to `canonicalModel` only when the key is unusable.

The order matters, and an earlier draft had it backwards. Where the two differ, the key is the dated snapshot (`claude-haiku-4-5-20251001`) and `canonicalModel` is the undated family name (`claude-haiku-4-5`) — see the probe table. Keying identity on `canonicalModel` would reintroduce this change's own bug one level down: when a new snapshot ships within the same family, `canonicalModel` holds still, verdicts from two different models collide on one key, and `report` shows measurement drift as agent drift. The key is the most precise identifier the envelope offers, so it is the one that belongs in the primary key.

The alternative — maintaining an alias-to-ID table inside agentlens — was rejected because it would be a second source of truth that goes stale precisely when it matters most (the moment an alias advances), which is the failure this change exists to prevent. Reading the resolution from the process that performed it cannot drift.

Extraction is defensive: if `modelUsage` is absent, empty, or carries multiple entries, the backend raises `JudgeError` rather than guessing or silently falling back to the alias. A silent fallback would reintroduce exactly the ambiguity being removed. Multiple entries would mean the call used more than one model, which is not a situation where a single `judge_model` value is meaningful.

### D2: The scoring loop stops overwriting `judge_model`

`_score_session` currently forces `judge_model=self.judge_model` onto the returned verdict. That has to become a pass-through of the backend's resolved value — otherwise D1's extraction is discarded one call later. `session_id` and `rubric_version` continue to be set by the loop, since those are the loop's facts, not the backend's.

### D3: The unscored-session query keys on the resolved model, so alias movement forces a re-score

`find_unscored_sessions` filters on `fv.judge_model = ?` using the loop's configured value. Once identity is a concrete ID, the loop needs the resolved ID before it can ask which sessions are unscored — but resolution only happens as a side effect of a judge call.

Resolved by a **one-call resolution step**: the backend exposes the resolved model after its first successful call, and the loop performs its unscored-set query against the resolved ID. On the first invocation with a given alias this means one judge call happens before the set is known, which is acceptable — that call scores a real session, so nothing is wasted.

**What the confirmation gate shows on that first run.** This needs stating, because the naive reading is misleading. Before any call has resolved anything, no row in `fact_verdict` can carry a concrete ID, so an unscored-set query keyed on the alias matches *every* session in the window. The gate would therefore quote the full window on first use even if most sessions are about to turn out already-scored — an over-count, which is the safe direction for a spend gate but confusing if unexplained.

The resolution is to make the gate honest about its own uncertainty rather than to add a probe call: on a run where the resolved model is not yet known, the gate presents its figure as an upper bound and says so ("up to N sessions"), and the post-run summary reports what was actually scored. Subsequent runs with the same alias have a resolved ID cached in the store and quote an exact figure. This keeps the "no wasted spend" property of the one-call approach while ensuring the number the user approves is never *lower* than what the run will do.

*Alternative considered.* A cheap throwaway probe call purely to resolve the alias — rejected: it spends money and adds latency to produce information the first real call yields for free.

*Alternative considered.* Requiring users to pass concrete IDs and rejecting aliases — rejected: it pushes model-naming churn onto the user for no gain, and the envelope already answers the question.

The consequence is intended and worth stating plainly: when `sonnet` advances, every session in the window is unscored again and a full window re-scores. That is correct — the previous scores were produced by a different judge — and it is what the design doc's "changing the rubric bumps `rubric_version`, which invalidates the cache and forces re-scoring — correct behavior" already establishes as the project's stance. The confirmation gate and `--max-sessions` are the existing controls for the cost of that.

### D4: Re-base cost estimates **upward** from measured calls, so the gate cannot understate

Replace the placeholder table with values at or above the top of the measured range, not at the mean:

| key | current | revised | basis |
|---|---|---|---|
| `sonnet` | $0.025 | $0.08 | above the $0.071 worst case observed |
| `opus` | $0.15 | $0.15 | unchanged; $0.119 measured, already conservative |
| default | $0.05 | $0.15 | an unrecognized model may well be opus-class |

Rounding **up** is the whole point. An estimate that undershoots is a gate that lies in the direction of unapproved spend; an estimate that overshoots merely under-promises, and the authoritative figure is reported after the run from the envelope's `total_cost_usd`. The `--max-turns 3` retry budget reinforces this: a single-turn measurement is a floor, so the estimate should sit above the observed maximum rather than at its centre.

The estimate table stays keyed by the *alias or ID the user passed*, since the estimate is shown before any call has resolved anything. `DEFAULT_PER_SESSION_COST` remains the fallback for an unrecognized value, re-based to the conservative end rather than the middle.

Two couplings worth recording. These figures are only valid in minimal mode (`--bare`): a non-bare call loads a large, machine-dependent system context on every call and costs materially more, so dropping `--bare` invalidates this table — a second reason the comparability ADR (D5) should name the judge context explicitly. And they are only valid for a transcript view of roughly the current size; if `build_transcript_view`'s budget grows substantially, the table needs re-measuring.

*Alternative considered.* Fit the estimate to the measured mean for accuracy — rejected: the confirmation gate is a safety mechanism, and its failure mode is asymmetric. Being 2× high costs a user a moment's hesitation; being 2× low spends their money without consent.

### D6: Fix `judge_input_tokens`, which the envelope's top-level `usage` reports as 1

Probing a realistic judge call surfaced a defect in the same function this change already edits (`_build_verdict`):

```
total_cost_usd:                       0.0710
envelope usage.input_tokens:          1        ← what _build_verdict reads today
envelope usage.output_tokens:         1523
envelope usage.cache_read_input_tokens: 0
modelUsage cacheCreationInputTokens:  12843   ← where the input actually went
```

The prompt is large and uncached on a first call, so the CLI books nearly all input as **cache creation**, leaving `usage.input_tokens` at 1. `claude_cli.py:131` reads `usage.input_tokens`, so `fact_verdict.judge_input_tokens` would persist `1` for a call that consumed ~12.8K input tokens — off by four orders of magnitude, on every row.

This is silent, which is what makes it worth fixing now rather than noticing later: `judge_cost_usd` comes from `total_cost_usd` and is correct, so the dollars look right while the token counts are nonsense. The design doc (§3) sells these fields as "the tool's *own* footprint, so agentlens is honest about what a run costs" — a claim the current code cannot support.

The fix is to compute input tokens from the resolved `modelUsage` entry as `inputTokens + cacheCreationInputTokens + cacheReadInputTokens`, and output tokens from its `outputTokens`. This change already extracts that entry for D1, so the data is in hand and the two fixes share a code path.

Note this makes cache-read a *component* of the reported total rather than a separate signal. That is right for the judge's own footprint — the question these fields answer is "what did this call consume" — and it is unrelated to the analyzed-agent cache-read percentage the design doc treats as a quality signal, which comes from `fact_session` and is untouched.

*Alternative considered.* Add a separate `judge_cache_tokens` column — rejected as scope creep; the store schema is deliberately untouched by this change, and a single honest input figure satisfies the design doc's claim.

### D5: One ADR covering verdict comparability

Rather than three scattered notes, one ADR states the invariant: two verdicts are comparable when they share a rubric version, a concrete model ID, and a judge system context. It records that the first two are enforced by the primary key while the third is enforced structurally by `--bare` plus pinned setting sources, and that making the context configurable would require promoting it to a key column. This gives a future contributor a single place that explains why `judge_model` must not hold an alias.

## Risks / Trade-offs

- **An alias advancing triggers a full-window re-score, which costs money.** → Intended behavior, consistent with how `rubric_version` already works. Mitigated by the existing confirmation gate, `--max-sessions`, and — now that D4 lands — an estimate that no longer understates. The `score` summary names the resolved model so the user can see *why* previously-scored sessions came back.
- **`modelUsage` is an undocumented envelope field that could change shape.** → Extraction raises `JudgeError` rather than falling back silently, so a shape change surfaces as a loud failure on the first call instead of quietly reverting to alias-keyed rows. A unit test pins the expected shape with a recorded envelope. Note this field is now load-bearing for three things — model identity (D1), token accounting (D6), and by extension the cost figures in D4 — so a shape change is a single point of failure worth the strict handling.
- **One judge call precedes the unscored-set query on first use of an alias (D3).** → That call scores a real session, so no spend is wasted. The gate presents its first-run figure as an upper bound (see D3) so the approved number is never below what the run performs.
- **Cost estimates are measured on one machine against a gateway endpoint, and on a synthetic transcript view.** → Deliberately rounded above the observed maximum rather than fitted (D4); the post-run figure from the envelope is authoritative, and the estimate's only job is preventing unapproved spend. Re-measurement is warranted if the transcript view's size budget changes materially.
- **The token-accounting fix (D6) depends on the same undocumented `modelUsage` entry.** → If the entry is missing, D1's strict extraction already raises `JudgeError` before token accounting is reached, so there is no path where tokens are silently mis-recorded again. The failure is loud by construction.

## Migration Plan

None required. `fact_verdict` holds zero rows and `fact_session` holds zero rows, verified directly against the store. No schema change: `judge_model` is already `TEXT`.

Rollback is reverting the extraction and the loop pass-through; any rows written under a concrete ID would then be re-scored under the alias, which is wasteful but not corrupting.

Land after `harden-judge-invocation` — both touch `_build_args()`, and this change's comparability ADR depends on `--bare` and the pinned setting sources being settled there.

## Open Questions

- Should the `score` summary line print the resolved model ID alongside the alias the user typed? It costs a word and makes an unexpected full re-score self-explanatory. Resolved as **yes**; treated as part of D4's reporting work rather than a separate decision.
- What is the judge's actual variance across identical re-runs? Partially answered as a side effect of D4's cost probes: identical calls over identical input returned 1207–1523 output tokens, so the judge's *text* varies materially run to run. Whether the resulting **scores** vary is still unmeasured, and that is what bounds the minimum meaningful delta in `report`. Out of scope here (Non-Goals), but there is now concrete reason to expect non-trivial variance, which strengthens the case for that follow-up.
- Do other aliases diverge between the `modelUsage` key and `canonicalModel` the way `haiku` does? Only three were probed. D1 handles divergence correctly either way by preferring the key, so this is curiosity rather than a blocker.
