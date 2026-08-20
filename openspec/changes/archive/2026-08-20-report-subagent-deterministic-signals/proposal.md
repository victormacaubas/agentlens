## Why

`agentlens session --file` can analyze one subagent spawn, but it cannot answer
whether an agent is improving across a week of work. Phase 2 needs a
subagent-only `report` path that discovers the relevant runs, derives skill and
health signals, and compares equal windows without calling an LLM.

## What Changes

- Add bulk discovery and ingestion for subagent transcripts and their sidecars
  under Claude project trees. Main-session transcripts remain out of scope until
  after 1.0.0, but a subagent may retain its qualified parent identifier as
  metadata.
- Scan effective user and project agent definitions into a content-addressed
  catalog. Bind a spawn only when the observed definition predates the spawn;
  otherwise record the historical definition as unknown rather than
  misattributing the current file.
- Record one session-skill row for each relevant skill, with independent
  `declared`, `available`, and `fired` states. Declaration and availability may
  be unknown when current files cannot prove historical state. A `SKILL.md`
  read alone does not count as a fired skill.
- Add the deterministic `fact_session` fields needed by reporting, including
  the raw agent identifier, effective agent-definition identifier, qualified
  parent identifier, task-prompt length, and fired-skill count.
- Add `agentlens report` with mutually exclusive window selectors, including
  `--since 7d`, optional agent filtering, configurable store location,
  machine-readable JSON output, and dry-run behavior for every write.
- Aggregate subagent spawns by agent type for a resolved window, compare them
  with the previous equal-length window, show the spawn count beside every
  aggregate, and suppress trend indicators below the low-volume threshold.
- Emit a deterministic report containing one typed row for every covered
  subagent spawn plus current-window agent rollups. The report does not create
  scores, verdicts, or fix proposals and never invokes the judge.

## Capabilities

### New Capabilities

- `subagent-discovery`: Discover and ingest subagent transcripts, sidecars, and
  effective agent definitions across Claude project trees without ingesting
  main-session transcripts.
- `agent-definition-catalog`: Version user- and project-scoped agent
  definitions and bind spawns only when historical applicability is provable.
- `session-skill-signals`: Record declared, available, and fired skill states
  at session-skill grain without converting missing history into false values.
- `report-command`: Define the subagent-only deterministic report CLI,
  selectors, filtering, output streams, dry-run behavior, and exit behavior.
- `report-aggregation`: Define current and prior windows, agent rollups,
  low-volume guards, and spawn-count semantics.
- `report-output`: Define the versioned deterministic window document, complete
  subagent-spawn rows, agent rollups, and stable report artifacts.

### Modified Capabilities

- `session-parser`: Enrich parsed subagent sessions with the raw agent,
  effective definition, qualified parent, task-prompt, and skill-signal facts
  required by deterministic reporting.
- `store-schema`: Persist the agent-definition catalog, session-skill bridge,
  and additional reproducible session facts, and support windowed analytical
  reads.

## Impact

- `cli` gains a `report` command and window parsing.
- `core` gains bulk ingest and deterministic report orchestration.
- `ingest` gains subagent discovery, agent-definition scanning, richer
  name/parent resolution, and skill-state derivation.
- `store` gains reproducible columns and tables plus analytical window queries.
- `models` gains typed report, definition, skill, and window values.
- `render` gains deterministic window JSON and terminal output while preserving
  the existing single-session surfaces.
- The runtime dependency set stays unchanged. The change reads `.claude/`
  without writing to it, does not invoke the judge, and does not ingest or score
  main sessions.
