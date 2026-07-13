## Context

agentlens Phases 0-2 are complete: the store schema (including `fact_verdict`), the parser, discovery, aggregation, and windowed reporting all work. The deterministic layer feeds clean signals per session: tool counts, token usage, duration, `n_duplicate_tool_calls`, `final_report_flagged_partial`, and the skill bridge. Phase 3 adds the LLM judge that consumes these signals plus the raw transcript to score sessions and suggest fixes.

The `claude` CLI's headless mode (`claude -p`) is the subprocess backend. It uses the user's existing auth (no API key needed locally) and supports `--json-schema` for structured output. Real subagent transcripts in `sample-data/` range from 68KB to 875KB (median ~253KB), containing 10-215 records.

Relevant ADRs:
- [0002](docs/adr/0002-fact-session-not-pure-rollup.md): fact_session is derived from two sources (events + transcript-read fields).
- [0003](docs/adr/0003-deterministic-layer-emits-counts-not-verdicts.md): The deterministic layer emits counts, never verdicts. Verdicts belong to the judge.

## Goals / Non-Goals

**Goals:**
- Score subagent sessions against a pinned, versioned rubric with four dimensions.
- Generate actionable fix suggestions per session.
- Cache verdicts so re-runs never re-pay for already-scored sessions.
- Provide cost visibility and guardrails (confirmation gate, max-sessions cap).
- Keep the judge backend pluggable for a future `ANTHROPIC_API_KEY` CI backend.

**Non-Goals:**
- Main-session scoring (deferred to v2 per design doc §9).
- Multi-pass scoring (one call per dimension). Single-pass is the v1 approach.
- A file-based verdict cache separate from the store.
- Rubric iteration/calibration tooling (that's manual prompt engineering, not code).
- Rendering verdict data in HTML/markdown reports (Phase 5).

## Decisions

### D1: Prepared transcript view, not raw JSONL

**Decision:** Build a structured text document (~10-12KB) from the transcript rather than passing raw JSONL to the judge.

**Why:** Raw JSONL is 60% JSON framing noise, contains full file contents from Read results (irrelevant to scoring), and the largest transcripts (875KB ≈ 220K tokens) would consume the entire context window. The prepared view includes only what the judge needs to score: task description, deterministic facts, tool call sequence with condensed inputs, error excerpts, and the final report.

**Alternative considered:** Pass raw JSONL via file path + Read tool. Rejected because it wastes tokens on noise and risks context-window overflow for large sessions.

**Prepared view structure:**
```
## Task
<task_description from .meta.json / first user record, truncated at 2000 chars>

## Agent Identity
- type: <agent_type>
- spawn_depth: <N>
- parent_session: <parent_session_id>

## Deterministic Facts
- turns: N, tool_calls: N, duration: Ns
- errors: N, permission_denials: N, duplicate_calls: N
- tokens: input=NK, output=NK, cache_read=NK
- final_report_flagged_partial: true/false

## Tool Sequence
1. Read <path>
2. Write <path> (new file, N bytes)
3. Bash: <first 120 chars of command> → exit <code>
...

## Errors & Denials
- [step N] <tool_name> error: <first 300 chars of output>

## Final Report
<full text of last assistant message>
```

**Tool input summarization rules:**
- `Read path` → path only
- `Write path` → path + "(new file, N bytes)" or "(overwrite)"
- `Edit path` → path + "(N edits)"
- `Bash cmd` → first 120 chars of command + exit code
- Error results → first 300 chars of output

### D2: Single Protocol, single-pass scoring

**Decision:** One `Judge` Protocol with a single `score(transcript_view, rubric_version) -> Verdict` method. All four rubric dimensions plus fix suggestions are produced in one judge call.

**Why:** The transcript view is ~3K tokens, the rubric prompt ~1-2K. Asking for a structured JSON with 4 dimensions is well within `--json-schema` capability. Splitting into 4 calls would be 4x cost and latency with marginal quality gain.

**Alternative considered:** Separate `score()` and `suggest_fixes()` methods. Rejected because fixes are grounded in scoring evidence — same context window is better. Can split later if fix quality is weak.

### D3: Default model is sonnet

**Decision:** Default `--judge-model` to `sonnet`. User can override with `opus` or a full model pin.

**Why:** At ~3K input + ~1K output per session: sonnet costs ~$0.02/session vs opus ~$0.12/session. Structured scoring against a rubric is well within sonnet's capability. The model is part of the cache key, so both can coexist — score with sonnet first, re-score interesting sessions with opus if needed.

### D4: Store-based caching with manual rubric versioning

**Decision:** The `fact_verdict` table IS the cache. Cache lookup is a SELECT by `(session_id, rubric_version, judge_model)`. Rubric version is a manual semver string (`v1`, `v2`, ...) bumped when the rubric changes intentionally.

**Why:** The table already exists with the right PK. A file-based cache would be redundant. Manual semver means cosmetic prompt edits during iteration don't force full re-scoring — only intentional version bumps do.

**Alternative considered:** Auto-hash of prompt template. Rejected because during rubric iteration, every tweak would invalidate all cached verdicts.

### D5: Separate `score` command, not a flag on `report`

**Decision:** New `agentlens score` command. `report` remains fast and deterministic, reading verdicts when present.

**Why:** `report` is currently fast, read-only, and free. Making it trigger LLM calls changes its character. Separation keeps the pipeline clear: `ingest` → `score` → `report`.

### D6: Fail-open per session, fail-closed on systemic errors

**Decision:** Per-session judge failures (timeout, malformed output) skip that session — next run retries. Three consecutive failures abort the loop (systemic issue like expired auth).

**Why:** The scoring loop is idempotent — persisted verdicts from prior iterations are kept, and re-run picks up where it left off. Storing failure markers adds invalidation complexity for no benefit.

### D7: Cost confirmation gate

**Decision:** Before scoring, show estimated cost and ask for confirmation. Skip with `--no-confirm`. Also support `--max-sessions N` to cap per invocation.

**Why:** A 30-day window with 200 unscored sessions could cost $4-24 depending on model. The user should opt in explicitly. The estimate uses a conservative per-session heuristic per model.

## Risks / Trade-offs

- **[Rubric quality]** The rubric prompt is the highest-risk artifact — if scoring is miscalibrated or fixes are generic, the feature is cosmetically complete but useless. → Mitigation: Phase 3b is dedicated rubric iteration against real sessions. The plumbing ships first (3a) so iteration is fast.
- **[claude CLI availability]** The judge backend requires `claude` to be installed and authenticated. If it's not present, `score` fails immediately with a clear error. → Mitigation: Check for `claude` on PATH before starting the loop. Future `ANTHROPIC_API_KEY` backend doesn't need the CLI.
- **[Transcript view lossy]** The prepared view discards full file contents and thinking blocks. A judge might miss subtle bugs in written code. → Mitigation: Include Write inputs (the code written) in a future rubric iteration if needed. The view is a module — easy to enrich.
- **[--json-schema reliability]** If the model consistently fails to produce valid structured output, scoring silently skips sessions. → Mitigation: Log the raw response on schema failures. The consecutive-failure abort catches systematic issues.
- **[Subprocess overhead]** Each `claude -p` call has cold-start overhead (~2-5s). Scoring 50 sessions takes 2-4 minutes. → Mitigation: Acceptable for a batch CLI tool. Progress output keeps the user informed.

## Open Questions

- **Rubric prompt wording** — Finalized during Phase 3b against real scored sessions. The exact prompt template is not designed here; only the structure (dimensions, scale, evidence format) is locked.
- **Write input inclusion** — Should the prepared view include the *content* of Write/Edit tool inputs (the code the agent wrote)? This would let the judge assess code quality but increases view size significantly. Deferred to rubric iteration.
