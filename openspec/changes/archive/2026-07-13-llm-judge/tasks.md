## 1. Verdict dataclass and Protocol

- [x] 1.1 Create `src/agentlens/judge/__init__.py` (empty) and `src/agentlens/judge/protocol.py` with `DimensionScore` (frozen dataclass: score int 0-5, evidence list[str]), `Verdict` (frozen dataclass: session_id, rubric_version, judge_model, dimensions dict, overall_score float, suggested_fixes list[str], judge_cost_usd, judge_input_tokens, judge_output_tokens), and `to_verdict_json()` method.
- [x] 1.2 Define the `Judge` Protocol class with a single `score(self, transcript_view: str, rubric_version: str) -> Verdict` method.
- [x] 1.3 Add `JudgeError`, `JudgeTimeoutError`, `JudgeUnavailableError` to `src/agentlens/errors.py`.
- [x] 1.4 Write tests in `tests/unit/test_judge_protocol.py`: Verdict serialization, overall_score computation (mean of dims), DimensionScore bounds.

## 2. Prepared transcript view

- [x] 2.1 Create `src/agentlens/judge/transcript_view.py` with `build_transcript_view(parsed: ParsedSession, jsonl_path: Path) -> str` that produces the structured text (Task, Agent Identity, Deterministic Facts, Tool Sequence, Errors & Denials, Final Report).
- [x] 2.2 Implement tool input summarization: Read → path only, Write → path + size, Edit → path + "N edits", Bash → first 120 chars + exit code.
- [x] 2.3 Implement task description extraction from the raw JSONL first user record (reconstruct from char-by-char streaming), truncate at 2000 chars. Fall back to `parsed.task_description` from .meta.json.
- [x] 2.4 Implement final report extraction (last assistant text block from raw JSONL).
- [x] 2.5 Implement error excerpt inclusion (first 300 chars of tool_result output for error steps).
- [x] 2.6 Write tests in `tests/unit/test_transcript_view.py`: view from synthetic session, truncation behavior, missing final report, error excerpts, total size stays under 20KB for a 79-tool-call session.

## 3. Rubric definition

- [x] 3.1 Create `src/agentlens/judge/rubric.py` with `RUBRIC_VERSION = "v1"` constant and `RUBRIC_PROMPT_TEMPLATE` string (the append-system-prompt content instructing the judge on scoring criteria for task_completion, honesty, efficiency, scope_adherence).
- [x] 3.2 Define `VERDICT_JSON_SCHEMA` (a dict suitable for `--json-schema`) that validates the structured output: dimensions (4 required, each with score 0-5 and evidence array), overall_score, suggested_fixes.
- [x] 3.3 Write tests in `tests/unit/test_rubric.py`: schema validates a good verdict, schema rejects missing dimension, schema rejects score > 5.

## 4. Claude CLI backend

- [x] 4.1 Create `src/agentlens/judge/claude_cli.py` with `ClaudeCliJudge` class implementing the `Judge` Protocol. Constructor accepts `model: str` (default "sonnet") and `timeout_seconds: int` (default 60).
- [x] 4.2 Implement `_check_claude_available()` that verifies `claude` is on PATH, raises `JudgeUnavailableError` if not.
- [x] 4.3 Implement `score()`: build subprocess args (`claude -p` with all flags per design D1), write transcript_view to a temp file (passed as the `-p` positional arg referencing the file via stdin pipe), parse the JSON envelope, extract `structured_output` as the verdict, extract `total_cost_usd`/`usage` for judge cost fields.
- [x] 4.4 Handle failure modes: subprocess timeout → `JudgeTimeoutError`; non-zero exit → `JudgeError`; `is_error: true` in envelope → `JudgeError`; JSON parse failure → `JudgeError`.
- [x] 4.5 Write tests in `tests/unit/test_claude_cli.py`: mock subprocess to test envelope parsing, timeout handling, error propagation, unavailable-claude detection. No real `claude` calls in unit tests.

## 5. Scoring loop and verdict persistence

- [x] 5.1 Create `src/agentlens/judge/scoring.py` with `ScoringLoop` class that holds a `Judge` instance, a `sqlite3.Connection`, and config (rubric_version, max_sessions, consecutive_failure_limit=3).
- [x] 5.2 Implement `find_unscored_sessions(window, agent_type) -> list[SessionRecord]` that queries `fact_session LEFT JOIN fact_verdict` for sessions missing a verdict with the current (rubric_version, judge_model).
- [x] 5.3 Implement `run(unscored_sessions) -> ScoringResult` loop: build view, call judge, persist verdict, track progress. Skip on per-session failure, abort on 3 consecutive.
- [x] 5.4 Implement `persist_verdict(verdict: Verdict)` that upserts into `fact_verdict` (INSERT OR REPLACE).
- [x] 5.5 Write tests in `tests/unit/test_scoring.py`: loop with mock judge (all succeed, single failure skipped, 3 consecutive abort), idempotent re-run, find_unscored filters correctly.

## 6. Score CLI command

- [x] 6.1 Add `score` subcommand to `src/agentlens/cli.py` with options: `--since`, `--from/--to`, `--agent`, `--judge-model` (default sonnet), `--max-sessions`, `--no-confirm`, `--dry-run`, `--today`.
- [x] 6.2 Implement cost estimation: `n_sessions × PER_SESSION_COST_ESTIMATE[model]` with hardcoded conservative estimates (sonnet: 0.025, opus: 0.15).
- [x] 6.3 Implement confirmation gate: display count + estimated cost, prompt Y/n (skip with --no-confirm).
- [x] 6.4 Implement dry-run: list unscored sessions (agent_type, task_description) and estimated cost, exit without judging.
- [x] 6.5 Implement progress output on stderr and final summary (scored/total, cost, skipped).
- [x] 6.6 Write tests in `tests/unit/test_score_cli.py`: dry-run output, no-confirm bypass, max-sessions cap, error on missing claude.

## 7. Report verdict integration

- [x] 7.1 Extend `build_report()` in `src/agentlens/reporting/queries.py` to LEFT JOIN `fact_verdict` and include verdict scores in `AgentWindowResult` when available.
- [x] 7.2 Extend `render_terminal_summary()` in `src/agentlens/reporting/rendering.py` to display verdict scores (overall + per-dimension) alongside deterministic counts when present.
- [x] 7.3 Extend `to_verdict_slice()` JSON output to include verdict data per session when present.
- [x] 7.4 Write tests in `tests/unit/test_reporting.py` additions: report with no verdicts (unchanged), report with verdicts (scores included), mixed scored/unscored.

## 8. Verification

- [x] 8.1 Run `uv run pytest` — all tests pass.
- [x] 8.2 Run `uv run ruff check` — no lint errors.
- [x] 8.3 Run `uv run mypy` — clean under strict mode.
- [x] 8.4 Manual smoke test: `agentlens score --dry-run --since 30d` against real store shows unscored sessions correctly.
