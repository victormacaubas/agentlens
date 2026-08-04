## 1. Resolve the concrete model in the backend

- [ ] 1.1 Add an envelope extraction helper in `src/agentlens/judge/claude_cli.py` that reads the single `modelUsage` entry's **map key** as the concrete identifier (falling back to that entry's `canonicalModel` only when the key is unusable) and raises `JudgeError` on a missing, empty, or multi-entry map. The key is preferred because it carries the dated snapshot where the two diverge — `haiku` resolves to key `claude-haiku-4-5-20251001` vs `canonicalModel: claude-haiku-4-5` — and keying on the undated family name would reintroduce this change's own bug one level down (D1)
- [ ] 1.2 Set `Verdict.judge_model` from the extracted concrete identifier in `_build_verdict` instead of the configured alias
- [ ] 1.3 Expose the resolved identifier on `ClaudeCliJudge` after a successful call, so the scoring loop can key its unscored-set query on it (D3)
- [ ] 1.4 Add unit tests with recorded envelopes: alias resolves to concrete ID, pinned ID passes through, a key/`canonicalModel` divergence resolves to the **key**, and missing/empty/multi-entry `modelUsage` each raise `JudgeError`

## 1b. Fix judge token accounting

- [ ] 1b.1 Compute `judge_input_tokens` in `_build_verdict` from the resolved `modelUsage` entry as `inputTokens + cacheCreationInputTokens + cacheReadInputTokens`, and `judge_output_tokens` from its `outputTokens`, replacing the reads of the envelope's top-level `usage` map (D6)
- [ ] 1b.2 Add a unit test with a recorded envelope where `usage.input_tokens` is 1 while `modelUsage` reports ~12.8K cache-creation tokens, asserting the persisted input-token count reflects the real consumption rather than 1
- [ ] 1b.3 Confirm `judge_cost_usd` continues to come from the envelope's `total_cost_usd` (already correct) and is not changed by this fix

## 2. Stop the scoring loop from overwriting identity

- [ ] 2.1 Remove `judge_model=self.judge_model` from the `replace(...)` call in `ScoringLoop._score_session`, keeping `session_id` and `rubric_version` (D2)
- [ ] 2.2 Key `find_unscored_sessions` on the backend's resolved model once available, using the one-call resolution order described in D3
- [ ] 2.3 Add unit tests: a fake judge returning a concrete ID has that ID persisted, and a session scored under one concrete ID is reported unscored when the backend later resolves to a different one

## 3. Re-base cost estimates

- [ ] 3.1 Re-base `PER_SESSION_COST_ESTIMATE` in `src/agentlens/cli.py` **upward**: `sonnet` $0.025 → $0.08 (above the $0.071 worst case measured), `opus` stays $0.15 ($0.119 measured, already conservative), and `DEFAULT_PER_SESSION_COST` $0.05 → $0.15 since an unrecognized model may be opus-class (D4)
- [ ] 3.2 Update the comment to record that these figures come from measured calls against a realistic ~18KB transcript view in minimal mode, that they are deliberately rounded above the observed maximum because a `--max-turns 3` retry can multiply a single-turn cost, and that they need re-measuring if the transcript view's size budget changes materially
- [ ] 3.3 Present the pre-scoring estimate as an upper bound ("up to N sessions") on a run where the resolved model is not yet known, since no verdict row can carry a concrete ID before the first call resolves one (D3)
- [ ] 3.4 Name the resolved concrete model alongside the user-supplied alias in the `score` final summary (D4 reporting, open question resolved as yes)
- [ ] 3.5 Update `tests/unit/test_score_cli.py` for the new estimate values, the upper-bound framing, and the summary line

## 4. Record the comparability invariant

- [ ] 4.1 Write `docs/adr/0009-verdict-comparability.md` in Nygard format: rubric version, concrete model, and judge context all have to hold; the first two are in the primary key, the third is held constant by minimal mode plus pinned setting sources; making the context configurable would require promoting it to a key column (D5)
- [ ] 4.2 Update `docs/agentlens-design.md` §2's model-pinning note to state that the stored `judge_model` is the resolved concrete identifier, and §4's caching note to match

## 5. Quality gate

- [ ] 5.1 `uv run pytest` green
- [ ] 5.2 `uv run ruff check` and `uv run mypy` green
- [ ] 5.3 `openspec validate pin-judge-identity --strict` passes
- [ ] 5.4 Confirm `fact_verdict` is still empty (`sqlite3 ~/.cache/agentlens/agentlens.db "select count(*) from fact_verdict"`) before merging; if verdicts exist by then, add a re-score note to the proposal's Impact
- [ ] 5.5 Score one real session after this lands and check the persisted row: `judge_model` holds a concrete identifier and `judge_input_tokens` is in the thousands rather than 1 — the two defects this change exists to fix, confirmed against real data rather than recorded envelopes
