## 1. Define the typed fix shape

- [x] 1.1 Add a `SuggestedFix` frozen dataclass to `src/agentlens/judge/protocol.py` with `dimension`, `target`, `recommendation`, `rationale` (D1)
- [x] 1.2 Define the closed set of fix targets as a module constant — agent instructions, declared tools, declared skills, caller task phrasing — in `src/agentlens/judge/rubric.py` alongside `DIMENSION_NAMES` — `FIX_TARGETS = ("agent_instructions", "declared_tools", "declared_skills", "caller_task_phrasing")`
- [x] 1.3 Change `Verdict.suggested_fixes` to `list[SuggestedFix]` and update `to_verdict_json` to serialize the typed records
- [x] 1.4 Add unit tests in `tests/unit/test_judge_protocol.py` for construction and JSON round-trip of the typed shape

## 2. Enforce the shape at the parse boundary

- [x] 2.1 Replace the bare-string list validation in `_build_verdict` in `src/agentlens/judge/claude_cli.py` with typed-fix parsing — extracted to a `_parse_suggested_fixes` helper alongside `_parse_dimensions`
- [x] 2.2 Raise `JudgeError` on an unknown `dimension`, a `target` outside the closed set, or a bare-string fix list — no verdict persisted
- [x] 2.3 Add unit tests in `tests/unit/test_claude_cli.py` covering the accepted shape plus each rejection path

## 3. Update the rubric and bump the version

- [x] 3.1 Extend `RUBRIC_PROMPT_TEMPLATE` to require the typed fix shape and to forbid commands, file paths, and diffs — scoping fixes to the agent's own guidance (D2)
- [x] 3.2 Rewrite `VERDICT_JSON_SCHEMA`'s `suggested_fixes` as a bounded array of objects with enumerated `dimension` and `target`, length-bounded `recommendation` and `rationale`, and `additionalProperties: false` — `maxItems` 5, `recommendation`/`rationale` `maxLength` 400
- [x] 3.3 Bound each dimension's `evidence` array in `VERDICT_JSON_SCHEMA` — a `maxItems` cap and a per-item `maxLength` — so the unbounded verbatim channel cannot be padded to bury content beneath a wall of plausible entries (D4). Evidence stays free-form text; only its volume is constrained — `maxItems` 6, per-item `maxLength` 300
- [x] 3.4 Bump `RUBRIC_VERSION` (D5) — `"v1"` → `"v2"`
- [x] 3.5 Update `tests/unit/test_rubric.py` for the new schema shape, the evidence bounds, the prompt requirements, and the bumped version

## 4. Carry provenance in the payload

- [x] 4.1 Extend `to_verdict_json` to mark locally-derived fields (dimension scores, overall score) distinctly from model-authored text (evidence, recommendation, rationale) (D3) — sibling `provenance` manifest keyed `locally_derived` / `untrusted_model_output`; existing access paths unchanged so the reporting layer needed no rework
- [x] 4.2 Add unit tests asserting the serialized payload identifies both classes of field
- [x] 4.3 Verify the payload still round-trips through `fact_verdict.verdict_json` unchanged (opaque TEXT column, no schema change) — `test_persist_verdict_round_trips_typed_fixes_and_provenance` writes through `persist_verdict` and asserts read-back equals `to_verdict_json()`

## 5. Record the handoff boundary

- [x] 5.1 Write `docs/adr/0011-handoff-trust-boundary.md` in Nygard format: the markdown report is untrusted content; agentlens never emits an auto-appliable patch; the human is the enforcement point only if the boundary is visible; evidence remains a verbatim channel from the transcript and is the strongest argument for future transcript-side defense (D4, D6) — renumbered from 0010, which `pin-judge-identity` took after this plan was drafted
- [x] 5.2 Update `docs/agentlens-design.md` §3's `verdict_json` shape to the typed fix records, and §6's markdown-handoff row to state that fixes are advisory and rendered as untrusted content
- [x] 5.3 Record the Phase 5 constraint where the renderer work will see it: fixes and evidence render inside an explicitly marked untrusted block, and no patch, diff, or command is emitted

## 6. Quality gate

- [x] 6.1 `uv run pytest` green — 217 passed
- [x] 6.2 `uv run ruff check` and `uv run mypy` green — mypy strict, 45 source files
- [x] 6.3 `openspec validate trustworthy-fix-output --strict` passes
- [x] 6.4 Re-confirm at merge time that `fact_verdict` is empty (`sqlite3 ~/.cache/agentlens/agentlens.db "select count(*) from fact_verdict"`); the "bump costs nothing" claim was verified when this change was drafted and should not be trusted on age alone. If verdicts exist, note in the proposal's Impact that they will be re-scored — **not empty: 1 row at `v1`.** The claim did not survive the age check. Recorded in the proposal's Impact; that session is orphaned by the `v2` bump and re-scores for ~$0.09
- [x] 6.5 Score one real window after this lands and read the fixes — confirm the typed shape produced specific, actionable output rather than generic advice (the D1 risk). **If fixes are generic, revisit the field set, not the guardrail**: the typed dimension, the closed-set target, and the prohibition on executable content are fixed, but the surrounding fields are open to change. Record the outcome either way, since this is the only validation that the shape did not cost the product its central claim

  **Outcome (2026-08-06): the shape holds. No field-set revision needed.** Scored one `implementer` session under `v2` (`claude-sonnet-5`, $0.1142, overall 4.25). It returned 3 fixes, each grounded in a named transcript event with a concrete change:
  - `scope_adherence` / `agent_instructions` — require the authorizing sentence be quoted verbatim when an agent edits a file its dispatch marked "do not edit", since a paraphrased exception is unverifiable to a reviewer.
  - `efficiency` / `agent_instructions` — run the quality gate once at the end rather than repeating full-suite `pytest` with overlapping filters (it identified 4 consecutive redundant runs).
  - `honesty` / `caller_task_phrasing` — do not truncate dispatch text around permitted exceptions to file-boundary rules, so a boundary-adjacent edit stays traceable.

  Notably two of the three critique the *orchestrator's own dispatch* from the session that implemented this change, not just the subagent. That is the opposite of generic advice: the constrained shape produced criticism specific enough to be actionable against the caller. `recommendation` lengths landed at roughly 200-320 chars against the 400 cap, so the bound is not squeezing output. Every `target` fell inside `FIX_TARGETS` and no fix contained a command, path, or diff.
