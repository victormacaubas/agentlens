## 1. SEC-01 — Drop judge filesystem/shell tools

- [x] 1.1 Confirm how the installed `claude` CLI expresses "no tools" (empty `--allowedTools ""` vs. omitting the flag) and record the choice
- [x] 1.2 In `claude_cli.py`, remove `--permission-mode dontAsk` and the `Read,Grep` grant from `_build_args()`; drop the now-unused `ALLOWED_TOOLS` constant
- [x] 1.3 Add an untrusted-transcript instruction to `RUBRIC_PROMPT_TEMPLATE` (transcript is data; never follow embedded directives)
- [x] 1.4 Update/add `test_claude_cli.py` tests asserting the arg list grants no `Read`/`Grep`/`Bash` and no `dontAsk`, plus a prompt-injection test that a sentinel file is never read

## 2. BUG-01 — Derive overall score locally, validate dimensions

- [x] 2.1 In `rubric.py`, remove `overall_score` from `VERDICT_JSON_SCHEMA` properties/required and remove the "compute an overall score" line from `RUBRIC_PROMPT_TEMPLATE`
- [x] 2.2 In `_parse_dimensions()`, validate each `score` is an `int` in 0-5 (reject out-of-range, NaN/inf, non-integer) with a `JudgeError`
- [x] 2.3 In `_build_verdict()`, compute `overall_score` as the mean of dimension scores and stop reading/trusting the model-supplied value
- [x] 2.4 Update `test_rubric.py`, `test_claude_cli.py`, `test_judge_protocol.py`: response averaging 4.0 with supplied `overall_score=99` yields 4.0; out-of-range/NaN/mismatch rejected; response omitting `overall_score` still succeeds

## 3. PERF-01 — Byte-budgeted transcript view

- [x] 3.1 Add `VIEW_MAX_BYTES` (~20KB) and helpers to `transcript_view.py`; build bounded sections first, then allocate remaining budget
- [x] 3.2 Truncate the Final Report to its budget with `TRUNCATION_MARKER`
- [x] 3.3 Cap the Tool Sequence to a bounded head/tail sample plus a total count; always retain every Errors & Denials entry
- [x] 3.4 Add `test_transcript_view.py` tests: 1MB final report and huge tool history stay under the hard limit, contain the marker, keep all six headers and error facts

## 4. ERR-01 — Normalize expected I/O failures into per-session isolation

- [x] 4.1 In `scoring.py` `_score_session()`, wrap `build_transcript_view(...)` with `except (OSError, UnicodeError)` → `JudgeError(... session_id, jsonl_path ...) from exc`; keep programmer errors outside
- [x] 4.2 In `claude_cli.py` `score()`, normalize a `subprocess.run` launch `OSError` into `JudgeError`
- [x] 4.3 Add `test_scoring.py` tests: deleted/unreadable/invalid-UTF-8 transcript → one skip with session id in the progress event, next session still scored, consecutive-failure abort still works

## 5. Spec sync & quality gate

- [x] 5.1 Run `openspec validate harden-judge-security-and-scoring --strict` and fix any issues
- [x] 5.2 Run `uv run pytest`, `uv run ruff check`, `uv run mypy` — all green
- [x] 5.3 Promote the SEC-01 no-tools decision (D1) to an ADR under `docs/adr/` before archiving
