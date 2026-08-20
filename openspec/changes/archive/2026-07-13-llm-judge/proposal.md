## Why

Phases 1-2 built a deterministic data core: tool events, session facts, aggregation, and windowed reporting. But the tool's killer output is actionable fix proposals per agent, not raw counts. The LLM judge (Phase 3 in the design doc) scores each subagent run against a versioned rubric and generates concrete improvement suggestions. Without it, agentlens tells you *what happened* but not *how well* or *what to fix*.

## What Changes

- Add a pluggable judge interface (`Judge` Protocol) with a `claude -p` headless backend as the first implementation.
- Build a "prepared transcript view" that condenses raw JSONL into a ~10-12KB structured summary the judge can score efficiently.
- Implement rubric v1 with four dimensions (task_completion, honesty, efficiency, scope_adherence), each scored 0-5, plus evidence and suggested fixes.
- Persist verdicts to the existing `fact_verdict` table (already in the DDL) with store-based caching keyed on `(session_id, rubric_version, judge_model)`.
- Add a new `agentlens score` CLI command that finds unscored sessions, confirms cost with the user, calls the judge, and persists results.
- Extend `agentlens report` to include verdict data when available.

## Capabilities

### New Capabilities
- `judge-interface`: Pluggable judge protocol, transcript view preparation, and the `claude -p` subprocess backend.
- `rubric-scoring`: Rubric v1 definition (dimensions, prompt template, versioning), verdict schema, and scoring loop with caching.
- `score-cli`: The `agentlens score` command with cost confirmation, progress output, error handling, and dry-run mode.

### Modified Capabilities
- `cli-scaffold`: Adding the `score` subcommand and `--scored` reporting integration.
- `windowed-reporting`: Report output includes verdict data (scores, fixes) when verdicts exist in the store.

## Impact

- **New subpackage:** `src/agentlens/judge/` (protocol.py, transcript_view.py, claude_cli.py, rubric.py)
- **Modified:** `src/agentlens/cli.py` (new `score` command, report flag)
- **Modified:** `src/agentlens/reporting/queries.py` and `rendering.py` (verdict-aware output)
- **External dependency:** `claude` CLI must be installed and authenticated for the judge backend to work. No new Python package dependencies.
- **Store:** No DDL changes — `fact_verdict` table already exists from Phase 0.
