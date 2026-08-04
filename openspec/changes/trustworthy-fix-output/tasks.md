## 1. Define the typed fix shape

- [ ] 1.1 Add a `SuggestedFix` frozen dataclass to `src/agentlens/judge/protocol.py` with `dimension`, `target`, `recommendation`, `rationale` (D1)
- [ ] 1.2 Define the closed set of fix targets as a module constant — agent instructions, declared tools, declared skills, caller task phrasing — in `src/agentlens/judge/rubric.py` alongside `DIMENSION_NAMES`
- [ ] 1.3 Change `Verdict.suggested_fixes` to `list[SuggestedFix]` and update `to_verdict_json` to serialize the typed records
- [ ] 1.4 Add unit tests in `tests/unit/test_judge_protocol.py` for construction and JSON round-trip of the typed shape

## 2. Enforce the shape at the parse boundary

- [ ] 2.1 Replace the bare-string list validation in `_build_verdict` in `src/agentlens/judge/claude_cli.py` with typed-fix parsing
- [ ] 2.2 Raise `JudgeError` on an unknown `dimension`, a `target` outside the closed set, or a bare-string fix list — no verdict persisted
- [ ] 2.3 Add unit tests in `tests/unit/test_claude_cli.py` covering the accepted shape plus each rejection path

## 3. Update the rubric and bump the version

- [ ] 3.1 Extend `RUBRIC_PROMPT_TEMPLATE` to require the typed fix shape and to forbid commands, file paths, and diffs — scoping fixes to the agent's own guidance (D2)
- [ ] 3.2 Rewrite `VERDICT_JSON_SCHEMA`'s `suggested_fixes` as a bounded array of objects with enumerated `dimension` and `target`, length-bounded `recommendation` and `rationale`, and `additionalProperties: false`
- [ ] 3.3 Bound each dimension's `evidence` array in `VERDICT_JSON_SCHEMA` — a `maxItems` cap and a per-item `maxLength` — so the unbounded verbatim channel cannot be padded to bury content beneath a wall of plausible entries (D4). Evidence stays free-form text; only its volume is constrained
- [ ] 3.4 Bump `RUBRIC_VERSION` (D5)
- [ ] 3.5 Update `tests/unit/test_rubric.py` for the new schema shape, the evidence bounds, the prompt requirements, and the bumped version

## 4. Carry provenance in the payload

- [ ] 4.1 Extend `to_verdict_json` to mark locally-derived fields (dimension scores, overall score) distinctly from model-authored text (evidence, recommendation, rationale) (D3)
- [ ] 4.2 Add unit tests asserting the serialized payload identifies both classes of field
- [ ] 4.3 Verify the payload still round-trips through `fact_verdict.verdict_json` unchanged (opaque TEXT column, no schema change)

## 5. Record the handoff boundary

- [ ] 5.1 Write `docs/adr/0010-handoff-trust-boundary.md` in Nygard format: the markdown report is untrusted content; agentlens never emits an auto-appliable patch; the human is the enforcement point only if the boundary is visible; evidence remains a verbatim channel from the transcript and is the strongest argument for future transcript-side defense (D4, D6)
- [ ] 5.2 Update `docs/agentlens-design.md` §3's `verdict_json` shape to the typed fix records, and §6's markdown-handoff row to state that fixes are advisory and rendered as untrusted content
- [ ] 5.3 Record the Phase 5 constraint where the renderer work will see it: fixes and evidence render inside an explicitly marked untrusted block, and no patch, diff, or command is emitted

## 6. Quality gate

- [ ] 6.1 `uv run pytest` green
- [ ] 6.2 `uv run ruff check` and `uv run mypy` green
- [ ] 6.3 `openspec validate trustworthy-fix-output --strict` passes
- [ ] 6.4 Re-confirm at merge time that `fact_verdict` is empty (`sqlite3 ~/.cache/agentlens/agentlens.db "select count(*) from fact_verdict"`); the "bump costs nothing" claim was verified when this change was drafted and should not be trusted on age alone. If verdicts exist, note in the proposal's Impact that they will be re-scored
- [ ] 6.5 Score one real window after this lands and read the fixes — confirm the typed shape produced specific, actionable output rather than generic advice (the D1 risk). **If fixes are generic, revisit the field set, not the guardrail**: the typed dimension, the closed-set target, and the prohibition on executable content are fixed, but the surrounding fields are open to change. Record the outcome either way, since this is the only validation that the shape did not cost the product its central claim
