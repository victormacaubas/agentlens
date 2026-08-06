## Why

The design doc says "Pin `--model`; it's part of the cache key." The code defaults to the alias `"sonnet"` and stores that alias verbatim as `judge_model` in the `fact_verdict` primary key. Aliases float: `sonnet` resolves to whatever the current Sonnet release is. Two runs a month apart therefore share a cache key while being graded by materially different models — the store treats them as interchangeable, and the prior-window deltas that make `report` a health check instead of a snapshot become noise.

The cache key is the tool's integrity guarantee. If it lies, the only thing agentlens claims to do — show whether an agent got better or worse across a window — silently stops being true, with no error and no visible symptom.

Separately, the cost estimate shown at the confirmation prompt understates. `PER_SESSION_COST_ESTIMATE` was hardcoded before any real judge call existed. Measured against a realistic ~18KB transcript view with the hardened flags, a `sonnet` judge call costs $0.025–$0.071 (mean ≈ $0.045) against an estimate of $0.025 — so the gate quotes a floor, not a ceiling, and a run can cost 2–3× what the user approved. `--max-turns 3` means a schema retry can push it further, since every measurement returned a single turn.

Third, `fact_verdict.judge_input_tokens` would record `1` for a call consuming ~12.8K input tokens. The envelope's top-level `usage.input_tokens` is 1 on these calls because a large uncached prompt is booked as cache *creation*; `_build_verdict` reads that field. The design doc sells these fields as agentlens being "honest about what a run costs" — and `judge_cost_usd` is correct, so the error is invisible: right dollars, meaningless tokens.

Doing this now is nearly free: the store currently holds zero verdicts, so nothing needs re-scoring and no migration is required. The same change after a few hundred sessions have been scored means invalidating real work.

## What Changes

- The Claude CLI backend resolves the configured model to the concrete model ID the CLI actually used, read from the response envelope's `modelUsage` **map key** (with that entry's `canonicalModel` as a fallback), and reports that concrete ID as the verdict's `judge_model`. The key is preferred because it carries the dated snapshot where the two diverge — `haiku` resolves to key `claude-haiku-4-5-20251001` against `canonicalModel: claude-haiku-4-5` — and the undated family name would leave the same drift one level down.
- **BREAKING (data):** `fact_verdict.judge_model` now holds a concrete model ID (e.g. `claude-sonnet-5`) rather than a possibly-floating alias (e.g. `sonnet`). Verdicts written under an alias would no longer match; the store is empty, so there is nothing to migrate.
- The `--judge-model` CLI flag continues to accept aliases for convenience — an alias is an input, never an identity. The scoring loop's unscored-session query keys on the resolved ID, so re-running after the alias moves correctly identifies previously-scored sessions as needing a re-score under the new model.
- `PER_SESSION_COST_ESTIMATE` is re-based **upward** from measured per-session costs instead of the pre-implementation placeholders (`sonnet` $0.025 → $0.08, default $0.05 → $0.15, `opus` unchanged at $0.15), so the confirmation gate quotes a ceiling rather than a floor. On a first run with a given alias the session count is presented as an upper bound, since no verdict can carry a resolved concrete ID before the first call produces one.
- **Fixed:** `judge_input_tokens` / `judge_output_tokens` are computed from the resolved `modelUsage` entry (summing input, cache-creation, and cache-read tokens) rather than the envelope's top-level `usage` map, which reports 1 input token for a call consuming thousands.
- **Fixed:** the judge invocation now passes the user settings file to `--settings`, without which `--bare` has no credential channel on a machine authenticating by `apiKeyHelper` — `score` fails with "Not logged in" on every call today. Discovered while probing the envelope for the model-identity work; folded in here because it touches the same `_build_args()` and because the change's final verification step (score a real session) cannot pass without it.
- A new ADR records what makes two verdicts comparable: the same rubric version, the same concrete model, and the same judge system context (`--bare` plus pinned setting sources). Any of the three floating means the cache key cannot be trusted.

## Capabilities

### New Capabilities
<!-- none -->

### Modified Capabilities
- `judge-interface`: gains a minimal-mode authentication requirement (`apiKeyHelper` reaches the judge only via `--settings`); the Claude CLI backend requirement gains model-identity resolution — the verdict's `judge_model` is the concrete model ID from the envelope, not the configured alias — and the judge's self-reported token counts must reflect actual consumption including cached input.
- `rubric-scoring`: the scoring-loop and verdict-persistence requirements gain the rule that verdict identity uses the resolved concrete model, so an alias that moves invalidates prior verdicts rather than silently colliding with them.
- `score-cli`: the cost-estimate requirement is re-based on measured per-session costs and required to be an upper bound rather than a best guess.

## Impact

- Code: `src/agentlens/judge/claude_cli.py` (`--settings` auth channel, envelope model extraction, `Verdict.judge_model`), `src/agentlens/judge/scoring.py` (`judge_model` threading — the loop currently overwrites the backend's value with the configured alias via `replace`), `src/agentlens/cli.py` (`PER_SESSION_COST_ESTIMATE`, resolved-model reporting in the summary).
- Docs: new ADR on verdict comparability; `docs/agentlens-design.md` §2 model-pinning note.
- Specs: `judge-interface`, `rubric-scoring`, `score-cli` deltas.
- Tests: `tests/unit/test_claude_cli.py`, `tests/unit/test_scoring.py`, `tests/unit/test_score_cli.py`.
- Store: no schema change (`judge_model` is already `TEXT`), and no migration needed — `fact_verdict` is empty.
- Depends on `harden-judge-invocation` landing first; both touch `_build_args()` and the `--bare` decision that this change's comparability ADR depends on.
