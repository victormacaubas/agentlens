## Why

A code audit of `src/agentlens/judge` (`.code-audit/2026-07-13-judge-9caceed-dirty.json`) found two critical issues: the Claude CLI judge runs prompt-injected transcript content with `Read,Grep` filesystem tools enabled (`--permission-mode dontAsk`), so a malicious transcript can read any file the user's Claude process can see; and the backend trusts an LLM-supplied `overall_score` that is only type-checked, so a response can persist an impossible score (e.g. 99) into the core scoring output. Two lower-severity issues compound the risk: the prepared transcript view has no enforceable size budget, and expected transcript I/O failures escape per-session isolation in the scoring loop.

## What Changes

- **BREAKING (spec):** The Claude CLI judge no longer grants `Read,Grep` (or any) filesystem/shell tools. It invokes `claude -p` with an explicit empty tool set and treats the transcript view as untrusted data that must never be executed as instructions.
- The judge derives `overall_score` locally as the mean of the four dimension scores instead of trusting the model-supplied value; each dimension score is range-validated (0-5) so any `Judge` backend upholds the invariant.
- The prepared transcript view gains a deterministic byte budget: the final report and tool-sequence sections are bounded with a visible truncation marker while all six section headers and every error/denial are preserved.
- The scoring loop normalizes expected transcript I/O errors (`OSError`, `UnicodeError`) into `JudgeError` so a single unreadable/missing transcript is counted as one skipped session and the loop continues under the consecutive-failure policy.

## Capabilities

### New Capabilities
<!-- none -->

### Modified Capabilities
- `judge-interface`: the Claude CLI backend requirement drops the `Read,Grep` tool grant in favor of an empty tool set; a new requirement mandates that `overall_score` is derived locally (never trusted from model output) and that dimension scores are range-validated.
- `rubric-scoring`: the verdict JSON schema and rubric prompt stop treating `overall_score` as a model-computed field; the prepared transcript view gains an enforceable size budget with truncation.

## Impact

- Code: `src/agentlens/judge/claude_cli.py` (args, verdict building, error normalization), `src/agentlens/judge/transcript_view.py` (size budget), `src/agentlens/judge/scoring.py` (I/O error isolation), `src/agentlens/judge/rubric.py` (schema/prompt).
- Specs: `judge-interface`, `rubric-scoring` delta files.
- Tests: `tests/unit/test_claude_cli.py`, `tests/unit/test_transcript_view.py`, `tests/unit/test_scoring.py`, `tests/unit/test_rubric.py`.
- No store schema, CLI surface, or dependency changes.
