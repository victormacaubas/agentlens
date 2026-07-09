# agentlens — design doc

*A tool to analyze, score, and improve Claude Code subagents from their session logs.*

> **Name:** `agentlens` — inspecting agent behavior up close, across a window of sessions.
> **Runtime:** Python, distributed via `uv` / PyPI. Primary entry: `uvx agentlens`.

---

## 1. Problem & goal

You have a growing set of custom subagents (`.claude/agents/*.md`). Today there's no way to see, across many sessions, which agents perform well, which go off-track, and what to fix. Claude Code already records everything as JSONL under `.claude/projects/`. This tool reads that data, scores each subagent run, and produces **actionable fix proposals** you can hand back to Claude Code.

**What it is:** a local CLI that turns raw session logs into (a) a machine-readable handoff for Claude to patch agents, and (b) a well-designed report for a human to read.

**What it is not:** a cost dashboard (codeburn already does that), a live monitor, or a hosted service. It runs on demand, locally.

### Design principles

1. **One data core, many thin renderers.** A dimensional store is the source of truth; terminal, markdown, HTML, and a future dashboard are all queries over it.
2. **Measured vs. modeled, kept separate.** Deterministic facts (what happened) are immutable and reproducible. LLM verdicts (how good it was) are subjective, versioned, and re-scoreable. They never mix in the same table.
3. **The killer output is the fix, not the score.** A single number invites gaming and hides the *why*. The product is per-session findings + concrete fixes.
4. **Design is part of the product.** The HTML report's look is a first-class deliverable with its own phase, not an afterthought.

---

## 2. Architecture

Four stages, each a thin layer over the previous. Read-only against the user's `.claude/`.

```
.claude/projects/**/*.jsonl              (main sessions)     ─┐
.claude/projects/**/<sid>/subagents/
        agent-<agentId>.jsonl            (subagent runs)     ─┤
        agent-<agentId>.meta.json        (spawn sidecar)     ─┤──▶  PARSE  ──▶  store (SQLite)
.claude/agents/**/*.md                   (agent defs)        ─┘
                                              │
                                              ├──▶  SCORE  (deterministic aggregation
                                              │            + pluggable LLM judge)
                                              │            ──▶ fact_verdict
                                              │
                                              └──▶  RENDER  ──▶ terminal (thin summary)
                                                              ──▶ markdown  (Claude handoff) *
                                                              ──▶ json      (scripting)      *
                                                              ──▶ html      (hero, designed)
                                                              ──▶ dashboard (later, cumulative)

* markdown + json are non-negotiable artifacts.
```

### Entry commands

- `agentlens session <id|--file path>` — analyze one session, full detail. The primitive; everything else builds on it. Cheapest path, ideal for "how did my new agent do?"
- `agentlens report --agent <name> --since 7d` — aggregate rollup across sessions in a window, grouped by agent, with prior-window deltas.

### Judge coupling

The LLM judge shells out to the user's existing `claude` CLI in headless mode. Behind a **pluggable interface** so an `ANTHROPIC_API_KEY` backend can be added for CI. Pin `--model`; it's part of the cache key.

**Auth:** headless `claude -p` uses the user's existing login/stored credentials by default — no key needed. If `ANTHROPIC_API_KEY` is set in the subprocess env, it takes precedence (that's the CI backend).

### Confirmed CLI contract (verified against current Claude Code docs)

Invocation for a read-only judge:

```bash
claude -p "<prompt>" \
  --output-format json \
  --model opus \
  --append-system-prompt "<judge instructions; return JSON verdict>" \
  --permission-mode dontAsk \
  --allowedTools "Read,Grep" \
  --max-turns 3 \
  --bare
```

Flag notes for the implementation:

- **`-p` / `--print`** — headless mode. Prompt is a **positional arg**; extra context can pipe via **stdin** (≤10MB — write large transcripts to a file and reference the path instead).
- **`--output-format json`** — single JSON envelope (vs. `stream-json`, `text`). Use `json` for one-shot.
- **`--permission-mode dontAsk`** — required, or the subprocess **hangs** waiting for approval. Prefer this over `--dangerously-skip-permissions` (which shows a one-time interactive warning on first use).
- **`--allowedTools "Read,Grep"` + `--max-turns 3`** — keep the judge read-only and bounded.
- **`--append-system-prompt`** (not `--system-prompt`, which *replaces* and drops Claude Code's foundation).
- **`--model`** — aliases `opus` \| `sonnet` \| `haiku` \| `opusplan` (auto-update), or pin a full string like `claude-opus-4-8`. The chosen value goes into the cache key.
- **`--bare`** — skips auto-discovery of hooks/skills/plugins/MCP/CLAUDE.md; faster and cleaner for this call.

**Response envelope** — parse `result` (the model's text) as the verdict; `is_error` (bool) to detect failures; `session_id`, `total_cost_usd`, `usage`, `duration_ms` for logging. If you later use `--json-schema` structured outputs, the validated object is in `structured_output` instead of `result`.

> Docs: [headless](https://code.claude.com/docs/en/headless), [CLI reference](https://code.claude.com/docs/en/cli-reference), [permission modes](https://code.claude.com/docs/en/permission-modes). Re-verify before Phase 3 — CLI flags do shift across versions.

---

## 3. Data model (deterministic layer)

A star schema. Keeping grains separate is what makes windows trivial (date filters) and prior-window deltas trivial (self-joins).

### `fact_tool_event` — finest grain, one row per tool_use / tool_result

| column | source / notes |
|---|---|
| `session_id` | |
| `seq` | order within session |
| `tool_name` | `Read`, `Bash`, `Edit`, `Skill`, `Agent`, … |
| `is_error` | from `tool_result.is_error` |
| `denial_kind` | native `toolDenialKind` (permission denials are typed, not inferred) |
| `ts` | ISO timestamp |
| `input_hash` | to detect retry loops (same tool + same args) |
| `output_bytes` | |

Everything in `fact_session` is an aggregation of this — store this, derive the rest.

### `fact_session` — one row **per spawn** (primary grain)

The grain is a single agent run, not an agent *type*. Four `implementer` spawns in one parent session = four rows, each with its own `agentId`. Never dedupe by `agent_type`; the spawn is the unit.

- **Identity:** `session_id`, `agent_id` (per-spawn hash from the filename), `agent_type` (canonical name), `name_source`, `session_kind` (`subagent` \| `main`), `spawn_depth`
- **Lineage:** `parent_session_id` (the `<sid>` folder the `subagents/` dir sits under), `spawn_tool_use_id` (from `.meta.json` — joins to the exact `Task` block in the parent), `task_description` (from `.meta.json`)
- **Volume:** `n_turns`, `n_tool_calls`, `n_reads`, `n_edits`, `n_writes`, `n_bash`, `n_files_touched`
- **Health:** `n_errors`, `n_permission_denials`, `n_retry_loops`, `claimed_status` (complete \| partial)
- **Cost/time:** `duration_sec`, `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_creation_tokens`
- **Context:** `task_prompt_len`, `n_skills_fired`

> **`main` sessions:** the parser ingests them too (`session_kind = main`, no lineage), so they land in the store from day one. **Scoring** main sessions needs an adapted rubric (they're open-ended, not a discrete delegated task) → **v2**. See §9.

### The `.meta.json` sidecar — spawn identity & pairing

Each `agent-<agentId>.jsonl` has a sibling `agent-<agentId>.meta.json`:

```json
{"agentType":"researcher","description":"Research Snowflake tag propagation through views","toolUseId":"toolu_...","spawnDepth":1}
```

It's a first-class parser input and the **authoritative** source for:

- `agentType` → `agent_type` / `dim_agent` key (beats parsing `attributionAgent` out of the transcript).
- `description` → `task_description` — what the agent was asked to do; the yardstick for the judge and a human-readable label to tell same-type spawns apart.
- `toolUseId` → `spawn_tool_use_id` — the join to the parent's `Task` tool_use, so you can inspect what the parent did **after** the subagent returned (re-spawn / correct / accept) = precise parent-correlation signal.
- `spawnDepth` → `spawn_depth` — nesting level; flags deep fan-out.

### `bridge_session_skill` — declared-vs-fired, one row per (session, skill)

| column | notes |
|---|---|
| `session_id` | |
| `skill_name` | |
| `declared` | referenced in the agent's `.md` |
| `available` | present in `.claude/skills/` (incl. plugins) |
| `fired` | `Skill` tool_use and/or `SKILL.md` read and/or injected content (best-effort) |

Whether a *missing* fire was a mistake is a judgment call → belongs in the judge layer, not here.

### Dimensions

- **`dim_agent`** (SCD from `.claude/agents` scan, keyed on `agent_type`): `name`, `model`, `effort`, `declared_tools[]`, `declared_skills[]`, `definition_hash`. Hash lets you attribute score shifts to definition changes. Resolves flat (`<name>.md`) and nested (`<name>/<name>.md`), project- and user-level.
- **`dim_date`**, **`dim_tool`** — conformed dims for cheap slicing.

### `fact_verdict` — LLM judge output (separate, never mixed with deterministic facts)

- Key: `session_id` + `rubric_version` + `judge_model`
- Per-dimension scores + overall + evidence quotes + suggested fixes (structured JSON)
- **Judge run-cost:** `judge_cost_usd`, `judge_input_tokens`, `judge_output_tokens` (from the `claude -p` envelope's `total_cost_usd` / `usage`) — the tool's *own* footprint, so agentlens is honest about what a run costs.

### Token & cost reporting

Two distinct figures, kept distinct:

- **Analyzed agent usage** (`fact_session` token fields) → reported as an **efficiency/quality** signal, in raw tokens + cache-read %. Low cache-read across many runs = unstable context (a finding, not a dollar amount). *Do not* dollarize this — quality is the message, not spend.
- **agentlens's own run-cost** (`fact_verdict` judge fields) → reported in **dollars + tokens** ("this analysis cost $X"). Near-zero on re-runs thanks to verdict caching.

### Name resolution (guarded)

Resolve **once per session**. Fallback chain, authoritative first:

1. `.meta.json` `agentType` — authoritative, present for modern subagent spawns.
2. `attribution_agent` from assistant records (only on assistant records; take distinct values).
3. Parent session's `Task` tool `subagent_type` (via `spawn_tool_use_id`).
4. `agent_id` hash — last resort so a session is never dropped.

Record which source won in `name_source`. Multiple conflicting values → flag ambiguous.

---

## 4. Rubric (scoring)

Rubric-first (absolute, pinned, **versioned**), comparative as a derived view. Changing the rubric bumps `rubric_version`, which invalidates the cache and forces re-scoring — correct behavior.

Starting dimensions (0–5 each), tune during Phase 3:

- **Task completion** — did it accomplish the task vs. merely claim it?
- **Honesty** — is the final report supported by the actual tool results?
- **Efficiency** — turns/tokens/retries relative to task complexity.
- **Scope adherence** — stayed within its brief and files?

Ground-truth signals, cheapest first: self-report vs. transcript consistency (judge), parent-session correlation (did the parent immediately re-spawn/correct?), optional user feedback later.

### Caching

Cache key = `hash(session_id + rubric_version + judge_model)`, stored in `~/.cache/agentlens/`. Only miss → call judge. Re-running a 30-day window never re-pays for already-scored sessions.

---

## 5. Windows & aggregation

- Flags: `--since 7d|30d|<date>`, `--window this-week`, `--from/--to`.
- Group by `agent_type` within window; show N next to every aggregate.
- **Prior-window delta:** compare to the previous equal-length span (this is what makes it a health check, not a snapshot).
- **Low-volume guard:** below `min_sessions_for_trend` (default 5), show raw scores + count but **suppress trend arrows**, labeled "insufficient data."
- **N counts spawns, not parent sessions.** "implementer: 12 runs this week" may be 3 sessions × 4 spawns — label it as spawns so trends and the low-volume guard aren't misread. Use `task_description` to distinguish same-type spawns in detail views.
- **Intra-session view (parent lens):** because spawns carry `parent_session_id`, roll up "this session fanned out 4 implementers + 3 explorers; 1 failed, 1 hit denials" — a health lens per parent session, not just per agent type.

---

## 6. Outputs & locations

| Surface | Role | Location |
|---|---|---|
| **Terminal** | thin summary: headline score + path, auto-opens HTML | stdout |
| **HTML** | hero, designed, self-contained single file, shareable, `file://` | `./reports/<scope>.html` |
| **Markdown** | Claude handoff — the fix report | `./reports/<scope>.md` |
| **JSON** | scripting / piping / dashboard feed | `./reports/<scope>.json` or `--format json` to stdout |
| **Store** | append-only dimensional SQLite (source of truth) | `~/.cache/agentlens/` (or configurable) |

Never write into `.claude/` — read-only.

### Idempotency & re-runs

Running multiple times a day must not duplicate data or clutter the folder. Two layers, two behaviors:

- **Store = append-only truth.** Upsert by `session_id`; re-runs add only new sessions, never duplicates. Verdicts are cached by `session_id + rubric_version + judge_model`, so repeat runs re-pay the judge **only** for genuinely new sessions.
- **Reports = overwrite-in-place views.** History lives in the store, *not* in report files — trends and prior-window deltas are always read from the store, never from stale HTML. So report files are disposable, always-current renders.

Naming & overwrite policy:

- **Stable filename per scope, overwritten each run** — no timestamp in the default name:
  - `reports/report_<window>_<agent|all>.{html,md,json}` (e.g. `report_7d_implementer.html`)
  - `reports/session_<session_id>.{html,md,json}` (changes only if re-scored under a new rubric)
- **`--archive`** (opt-in, off by default) drops a timestamped copy into `reports/history/` for audit. Not needed for trends — the store already covers that.
- **No-change short-circuit:** if a run finds no new sessions and the same `rubric_version` + `judge_model`, every verdict is a cache hit — report "no changes since last run" and re-render instantly (free), or skip regeneration entirely.

Net rule: **store is append-only history; reports overwrite in place; archiving is opt-in.**

---

## 7. Dashboard (separate initiative, enabled for free)

Same pattern as a static GitHub Pages dashboard reading a data blob: date-range filters, compare-to-previous-span, heatmaps, drill-down — no backend. Because the store is **append-only and dimensional**, the dashboard is just another consumer: publish a cumulative aggregated JSON to a `gh-pages` repo (manual or scheduled), point a static site at it. The design system from Phase 4 carries over. This is a downstream initiative, but the Phase 1–2 data model is what makes it an afternoon rather than a rebuild.

---

## 8. Phases

Each phase is independently valuable and testable — one focused Claude Code session (or a small OpenSpec change) each. `⟂` = can run in parallel.

### Phase 0 — Scaffold & contracts
Repo init, language/runtime decision (see Open Decisions), CLI skeleton with `session`/`report` stubs, the SQLite schema DDL, and the verdict-JSON shape. **Exit:** empty pipeline runs end-to-end and writes an empty store.

### Phase 1 — Parser core (deterministic, no LLM)
Discover main sessions (`projects/**/*.jsonl`), subagent runs (`projects/**/<sid>/subagents/agent-*.jsonl`) + their `.meta.json` sidecars, and agent defs (`.claude/agents/**`). Parse into `fact_tool_event` + `dim_agent`, persist to SQLite. Path-based parent linkage + `.meta.json` pairing + name-resolution fallback chain. Ingest `main` sessions too (`session_kind`), even though they aren't scored until v2. Single-session path first. **Exit:** point it at the sample `agent-*.jsonl` (+ meta) and get a correctly populated store with parent lineage resolved. ~60% of the value ships here.

### Phase 2 — Deterministic signals & aggregation
Derive `fact_session` from events, build `bridge_session_skill` (declared/available/fired), implement windows + prior-window deltas + low-volume guards, emit the deterministic slice of the verdict JSON. **Exit:** `report --since 7d` produces real numbers with no LLM.

### Phase 3 — LLM judge
Pluggable judge interface, `claude -p` backend, rubric v1 (pinned + versioned), caching, `fact_verdict`. **Exit:** sessions get scored + fix proposals; re-runs hit cache.

### Phase 4 — Design system ⟂ (starts after Phase 0)
Dedicated session. Visual identity, design tokens, component library (score/verdict cards, timeline, trend charts, fix-proposal cards), static HTML mockups with fake data. Use the `impeccable` skill. **Exit:** an approved, reusable design spec + mockups.

### Phase 5 — Renderers
Implement markdown (handoff) + JSON export + thin terminal summary + HTML report (Phase 4 design over real verdict JSON). **Exit:** all four surfaces render from one verdict core. **Depends on:** 3 + 4.

### Phase 6 — Dashboard (separate initiative)
Cumulative JSON export + static `gh-pages` site (windows, compare, drill-down). **Depends on:** 2 (data model) + 4 (design).

```
0 ──▶ 1 ──▶ 2 ──▶ 3 ──▶ 5 ──▶ (6)
      └─▶ 4 (parallel) ──▶ 5, 6
```

---

## 9. Decisions & open items

**Locked:**

- **Name:** `agentlens`.
- **Runtime:** Python. The data core (JSONL parsing, dimensional model, SQLite, path to Parquet/warehouse) is the bulk of the tool and Python's strength; pairs with the `python-engineering-standards` skill. The judge shells out to `claude` regardless of language.
- **Distribution:** publish to PyPI; document `uvx agentlens` (zero-install run) as the primary entry, `pipx install agentlens` for regulars. Note: the dashboard is a static JS front-end reading a JSON blob — language-independent — so it does not influence this choice.

**Still open:**

- **Rubric dimensions & weights** — finalize in Phase 3 against real scored sessions.

**Deferred to v2:**

- **Main-session scoring.** Main sessions are parsed and stored from Phase 1 (`session_kind = main`), but *scoring* them needs an adapted rubric (open-ended conversation, no single delegated task). Subagent scoring ships v1; main-session scoring is v2 — no data-model change required, just a rubric variant.
- **Store: Parquet.** Parquet later for warehouse/S3 export.
```
