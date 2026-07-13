## Context

A code audit of `src/agentlens/judge` (`.code-audit/2026-07-13-judge-9caceed-dirty.json`, verdict `request_changes`) surfaced two critical and two lower-severity issues, all verified against the current code at `main@9caceed-dirty`:

- **SEC-01 (critical):** `ClaudeCliJudge._build_args()` runs `claude -p` with `--permission-mode dontAsk --allowedTools "Read,Grep"`, and `score()` feeds the attacker-influenceable `transcript_view` as the prompt via stdin. A transcript can instruct the judge to read arbitrary files. The current `judge-interface` spec *mandates* these flags, so the spec must change too.
- **BUG-01 (critical):** `_build_verdict()` only type-checks the model-supplied `overall_score` (`isinstance(int|float)`) then persists `float(overall_score)`. The schema declares it as bare `number`. A response can persist an impossible score (probe returned `99.0`), corrupting reporting rollups.
- **PERF-01 (high):** `_extract_final_report()` returns the full assistant text and `_build_tool_sequence_section()` emits one unbounded line per call. No global size budget exists; a 30k-char report produced a 30,380-byte view.
- **ERR-01 (medium):** `ScoringLoop.run()` catches only `JudgeError`, but `_score_session → build_transcript_view → read_jsonl_records` opens the file and can raise `OSError`/`UnicodeError`, which escape and abort the loop mid-run.

The judge layer is otherwise cleanly typed, linted, and covered by a passing 167-test suite. This change is scoped to `src/agentlens/judge` plus the two OpenSpec specs it violates.

## Goals / Non-Goals

**Goals:**
- Remove the judge's filesystem/shell capabilities so prompt injection cannot exfiltrate files.
- Make `overall_score` a locally derived, always-valid value independent of model output.
- Give the prepared transcript view a deterministic, enforceable upper bound.
- Fold expected transcript I/O failures into the existing per-session skip/continue path.
- Keep the quality gate green (`pytest`, `ruff`, `mypy`) and update specs to match.

**Non-Goals:**
- No sandboxing beyond dropping tools (no seccomp/container work); env/cwd hardening is optional defense-in-depth, not required.
- No live `claude -p` integration test or dependency-CVE analysis (out of scope for this source-path fix).
- No change to the store schema, CLI surface, reporting, or the `Judge` Protocol signature.
- No re-scoring/backfill of already-persisted verdicts.

## Decisions

### D1 — Drop all judge tools (SEC-01)
Replace `--permission-mode dontAsk --allowedTools "Read,Grep"` with an empty allowed-tools set and no permissive permission mode. The judge is a pure grading call; it never needs to touch the filesystem. Prompt wording (marking the transcript as untrusted) is added as defense-in-depth in the rubric template, but the primary control is that no file-reading tool exists.

- **Alternative considered:** keep tools but sandbox the process (empty cwd, scrubbed env). Rejected as primary control — more complex and still leaves a capability an injected prompt can aim at. Kept only as optional hardening.
- **Open item:** confirm the installed `claude` CLI accepts an empty `--allowedTools ""` to mean "no tools"; if the flag can simply be omitted to grant nothing, prefer omission. Verified during implementation against the CLI in use.

### D2 — Derive `overall_score` locally, validate dimensions (BUG-01)
In `_build_verdict()`, compute `overall_score = sum(d.score for d in dimensions.values()) / len(DIMENSION_NAMES)` and persist only that. Remove `overall_score` from `VERDICT_JSON_SCHEMA` and from the rubric prompt so the model no longer supplies it. Add a 0-5 integer range check in `_parse_dimensions()` (reject NaN/inf/out-of-range/non-integer) so the invariant holds for any backend constructing a `Verdict`.

- **Alternative considered:** keep the model value but reject it when it disagrees with the mean. Rejected — deriving locally is simpler, removes a class of malformed input entirely, and matches the existing spec intent ("overall_score is the mean of dimensions").

### D3 — Byte-budgeted transcript view (PERF-01)
Introduce a module-level `VIEW_MAX_BYTES` (target ~20KB to match the existing "under 20KB" acceptance scenario). Build the fixed/small sections first (Task, Identity, Facts, Errors & Denials — all already bounded), then allocate the remaining budget to the two unbounded sections: truncate the Final Report to its share with `TRUNCATION_MARKER`, and cap the Tool Sequence to a bounded head/tail sample plus a total count. Errors & Denials are always retained in full (already capped per-entry at 300 chars) so critical facts survive truncation.

- **Alternative considered:** a token budget via a tokenizer. Rejected — adds a dependency; a byte budget is deterministic, dependency-free, and adequate as a hard ceiling.

### D4 — Normalize expected I/O errors in the loop (ERR-01)
Wrap the `build_transcript_view(...)` call in `_score_session()` with `except (OSError, UnicodeError) as exc: raise JudgeError(f"failed to read transcript for {session.session_id} at {jsonl_path}") from exc`. Programmer errors (e.g. `KeyError`, `TypeError`) stay outside the wrapper and still fail fast. Apply the same `OSError → JudgeError` normalization to the `subprocess.run` launch in `ClaudeCliJudge.score()`. Because `run()` already skips on `JudgeError`, this reuses the existing isolation and consecutive-failure policy with no new control flow.

## Risks / Trade-offs

- **Removing tools changes judge output quality** → The judge already scores only the prepared view (per design D1 of the original spec); it was never supposed to browse files, so removing tools aligns behavior with intent. Existing tests assert on args/verdict shape, not on tool use.
- **Truncating very long reports may drop tail content a judge would weigh** → Mitigated by keeping a visible truncation marker and always preserving the deterministic Facts and every Error/Denial entry, which carry the load-bearing signal; honesty/efficiency scoring leans on those sections.
- **`--allowedTools ""` semantics differ across CLI versions** → Resolved at implementation time by checking the installed CLI; fall back to omitting the flag if empty-string isn't accepted (D1 open item).
- **Schema change makes old model responses that include `overall_score` "extra"** → `additionalProperties: False` currently forbids extras; dropping `overall_score` from `required` while the model may still emit it could fail validation. Mitigation: remove the property entirely and rely on local derivation; if the model still emits it, keep the property optional but ignore its value.
