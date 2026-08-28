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

- `agentlens session --file <path>` — analyze one subagent transcript, full detail. **Implemented** (`ingest-single-transcript`). Flags: `--format json` (JSON to stdout, nothing else on that stream), `--store <path>` (override the default store location), `--dryrun` (report what would be written without writing the store or the artifact). Lookup by an already-ingested `<id>` without `--file` is not yet built.
- `agentlens report --agent <name> --since 7d` — aggregate rollup across sessions in a window, grouped by agent, with prior-window deltas. Modeled scores name one explicit `(rubric_version, concrete judge_model)` cohort; ambiguous multi-model windows require `--judge-model`.

### Source identity and snapshot integrity

Raw Claude IDs are not globally unique: the same value can appear as a main-session ID,
a subagent ID, or in more than one project bucket. Discovery therefore derives an internal
`session_id` as a deterministic SHA-256 of `(source_project, session_kind, raw_session_id)`.
The tuple remains stored for display and unambiguous lookup. Qualified parent IDs use the
same project and the `main` kind, so lineage cannot cross projects accidentally. See

Each parse is a versioned snapshot. The parser streams JSONL while recording parse-health
counters and a source revision `(mtime_ns, size, content_hash)`, then verifies that the file
did not change during the read. A malformed, incomplete, changed-during-read, or stale
snapshot cannot replace a newer sound grain. The store remains a disposable cache, so this
schema change uses rebuild-from-source rather than an in-place migration.

### Judge coupling

The LLM judge shells out to the user's existing `claude` CLI in headless mode. Behind a **pluggable interface** so an `ANTHROPIC_API_KEY` backend can be added for CI. Pin `--model`; what goes into the cache key is the resolved concrete model identifier read from the response envelope, not the alias as typed. See 

**Auth:** the judge runs with `--bare` (see below), and `--bare` skips keychain reads entirely. Under `--bare`, auth is strictly `ANTHROPIC_API_KEY` or an `apiKeyHelper` configured via user settings, and `--setting-sources "user"` alone is not enough to make that work: `--bare` reads the `apiKeyHelper` only from an explicit `--settings <path>`, never from `--setting-sources` alone and never from the keychain. Without `--settings`, a `--bare` call with `--setting-sources "user"` still returns `Not logged in` even on a machine that is otherwise authenticated. OAuth login and keychain credentials are never read, regardless of what's logged in on the machine. An unauthenticated environment fails fast with a `Not logged in` response rather than hanging or silently degrading;

### Confirmed CLI contract (hardened; see `harden-judge-invocation`)

Invocation for a read-only judge:

```bash
claude -p "<prompt>" \
  --output-format json \
  --model sonnet \
  --json-schema "<verdict JSON schema>" \
  --bare \
  --tools "" \
  --setting-sources "user" \
  --settings "<expanded path to the user's settings file>" \
  --max-budget-usd "<spend ceiling in USD>" \
  --append-system-prompt "<judge instructions; return JSON verdict>"
```

launched with an explicit temporary-directory `cwd` and an environment filtered to `PATH`, `HOME`, and any `ANTHROPIC_*` variable, never the process's inherited cwd or full environment.

Flag notes for the implementation:

- **`-p` / `--print`** — headless mode. Prompt is a **positional arg**; extra context can pipe via **stdin** (≤10MB — write large transcripts to a file and reference the path instead).
- **`--output-format json`** — single JSON envelope (vs. `stream-json`, `text`). Use `json` for one-shot.
- **`--tools ""`**: the enforcing mechanism for a read-only, side-effect-free judge. `--tools <tools...>` selects from the built-in set; `""` disables all of them. This is stronger than an empty `--allowedTools`: `--allowedTools` (or omitting it) is a permission decision layered over a tool set that is still loaded, so a prompt-injected transcript can still reach a tool the allowlist forgot to exclude or that the CLI adds later. `--tools ""` removes the tools themselves, so there is nothing to aim at regardless of what the prompt requests. Verified empirically: the same canary-file-read prompt that succeeded under the old `--allowedTools`-omitted invocation returned `NO_TOOLS` once `--tools ""` was added.
- **`--setting-sources "user"`**: drops `project` and `local`, so a `.claude/settings.local.json` in whatever directory agentlens happens to run from cannot reconfigure the judge. `user` must be kept (not `""`): it is the only setting source `--bare` accepts auth through, and `--setting-sources ""` alongside `--bare` was probed and returns `Not logged in`.
- **`--settings "<path>"`**: the expanded path to the invoking user's own `settings.json`. This is what makes `--bare` authentication work at all: `--bare` reads an `apiKeyHelper` only from an explicit `--settings` path, never from `--setting-sources "user"` alone and never from the keychain. Probed without it, an otherwise-authenticated machine still returns `Not logged in`.
- **`--max-budget-usd <ceiling>`**: bounds spend for one call. There is no `--max-turns` flag; structured output via `--json-schema` is implemented as an internal tool call, so a schema-constrained call reports `num_turns: 2` regardless of `--tools ""`, and nothing bounds turns directly. The spend ceiling is the backstop against a run that stays within its turn floor but is unexpectedly expensive.
- **Explicit `cwd` and filtered `env`**: the subprocess gets a temporary directory as `cwd` and an environment forwarding only `PATH`, `HOME`, and `ANTHROPIC_*`-prefixed variables (covers `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`). This is defense in depth: with `--tools ""` there is nothing to reach, but it is the control that holds if a future change reintroduces a tool.
- **Wall-clock bound**: there is no `--timeout` flag. The subprocess caller enforces its own wall-clock timeout on the call and treats an expired one as the judge being unavailable; the CLI contributes no bound on how long a single call may run.
- **`--append-system-prompt`** (not `--system-prompt`, which *replaces* and drops Claude Code's foundation).
- **`--model`** — aliases `opus` \| `sonnet` \| `haiku` \| `opusplan` (auto-update), or pin a full string like `claude-opus-4-8`. An alias is accepted here as an input convenience only: what goes into the cache key is the resolved concrete identifier the backend reads back from the response envelope, never the alias as typed.
- **`--bare`** — skips auto-discovery of hooks/skills/plugins/MCP/CLAUDE.md. Retained for reproducibility, not just speed: a non-bare call's inherited hooks, CLAUDE.md, plugin context, and auto-memory vary by machine and working directory, which would make the judge's system context, and therefore its verdicts, incomparable across runs. The cost is that OAuth-only users cannot authenticate the judge; see the auth note above.

**Response envelope** — parse `result` (the model's text) as the verdict; `is_error` (bool) to detect failures; `session_id`, `total_cost_usd`, `usage`, `duration_ms` for logging. If you later use `--json-schema` structured outputs, the validated object is in `structured_output` instead of `result`.

> Docs: [headless](https://code.claude.com/docs/en/headless), [CLI reference](https://code.claude.com/docs/en/cli-reference), [permission modes](https://code.claude.com/docs/en/permission-modes). Re-verified against CLI 2.1.241 during `score-single-spawn`, which is what fixed the invocation above; see `docs/adr/0009-judge-invocation-bounds-and-model-resolution.md` for what changed and why. Re-verify again if flags shift in a future CLI release.

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
| `file_path_hash` | normalized path identity for distinct-file counts; independent of offsets, ranges, or replacement text |
| `output_bytes` | |

Everything in `fact_session` is an aggregation of this — store this, derive the rest.

### `fact_session` — one row **per spawn** (primary grain)

The grain is a single agent run, not an agent *type*. Four `implementer` spawns in one parent session = four rows, each with its own `agentId`. Never dedupe by `agent_type`; the spawn is the unit.

- **Source identity:** `session_id` (qualified internal key), `raw_session_id`, `source_project`, `session_kind` (`subagent` \| `main`), `source_revision`, `source_mtime_ns`, `source_size`, `source_content_hash`, `judge_input_hash`
- **Agent identity:** `agent_id` (raw per-spawn filename ID), `agent_type` (canonical name), `agent_definition_id` (the effective versioned definition), `name_source`, `spawn_depth`
- **Lineage:** qualified `parent_session_id`, `spawn_tool_use_id` (from `.meta.json` — joins to the exact `Task` block in the parent), `task_description` (from `.meta.json`)
- **Volume:** `n_turns`, `n_tool_calls`, `n_reads`, `n_edits`, `n_writes`, `n_bash`, `n_files_touched`
- **Health:** `n_errors`, `n_permission_denials`, `n_duplicate_tool_calls`, `final_report_flagged_partial` (raw boolean marker — *not* a completion verdict;
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
| `fired` | `Skill` tool_use and/or injected skill marker; a `SKILL.md` read alone does not count |

Whether a *missing* fire was a mistake is a judgment call → belongs in the judge layer, not here.

### Dimensions

- **`dim_agent`** (versioned from `.claude/agents` scans): keyed by agent type, scope, source project for project-scoped definitions, and `definition_hash`; stores `name`, `model`, `effort`, `declared_tools[]`, and `declared_skills[]`. Each session binds the effective definition version at ingest. Project scope overrides user scope only inside that project, so later definition edits do not rewrite historical attribution.
- **`dim_date`**, **`dim_tool`** — conformed dims for cheap slicing.

### `fact_verdict` — LLM judge output (separate, never mixed with deterministic facts)

- Key: `session_id` + `judge_input_hash` + `rubric_version` + `judge_model`
- `verdict_json` shape: `{dimensions: {task_completion, honesty, efficiency, scope_adherence} (each {score, evidence[]}), overall_score, suggested_fixes[] (each {dimension, target, recommendation, rationale}), provenance: {locally_derived[], untrusted_model_output[]}}`. `session_id`, `rubric_version`, `judge_model`, and the three judge cost/token fields are `fact_verdict` columns, not part of the JSON blob. `provenance` marks which fields are locally derived and validated (scores) versus untrusted model output (evidence, and each fix's recommendation/rationale)
- **Judge run-cost:** `judge_cost_usd`, `judge_input_tokens`, `judge_output_tokens` (from the `claude -p` envelope's `total_cost_usd` / `usage`) — the tool's *own* footprint, so agentlens is honest about what a run costs.

`judge_input_hash` is the SHA-256 of the exact prepared transcript view. An unchanged
re-ingest remains a cache hit; changed judge input creates a distinct verdict identity.
Verdict finalization re-renders the prompt from source and compares `judge_input_hash`
specifically — not `revision_content_hash` and not `derivation_fingerprint`, both of which
move for reasons the judge never saw. A verdict produced while an ingest landed is stored
under the hash the judge was actually shown and reported as behind the current input; the
identity is what prevents it attaching to the newer ingest, so the recheck reports rather
than guards. Concurrent scorers use expiring owner-scoped SQLite claims and never hold a
transaction during the external judge call. See

### Token & cost reporting

Two distinct figures, kept distinct:

- **Analyzed agent usage** (`fact_session` token fields) → reported as an **efficiency/quality** signal, in raw tokens + cache-read %. Low cache-read across many runs = unstable context (a finding, not a dollar amount). *Do not* dollarize this — quality is the message, not spend.
- **agentlens's own run-cost** (`fact_verdict` judge fields) → reported in **dollars + tokens** ("this analysis cost $X"). A reused verdict costs **exactly** zero, not near-zero: no judge is invoked. The surfaces report this run's spend as zero and mark the verdict as reused alongside its original `scored_at`, so free-because-reused never reads as nothing-happened, and the historical cost of the call that first bought the verdict is never presented as what this run spent.

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

Cache identity = `session_id + judge_input_hash + rubric_version + judge_model`, stored in `~/.cache/agentlens/`, where `judge_model` is the resolved concrete model identifier, never the alias supplied at the CLI. Only a current-input miss calls the judge, and the lookup happens in `core`, above the judge seam, so no backend ever learns that a cache exists.

**A floating alias cannot be resolved without a call.** Because the identity carries the *resolved* model and that identifier is only observable in a response envelope, requesting `sonnet` can never short-circuit to a verdict stored under `claude-sonnet-5`: whether the alias still resolves to that concrete model is not knowable in advance. A request under a floating alias therefore always calls the judge. Pin a concrete identifier to get cache hits. (Bounded retry across model candidates is deferred to batch scoring, which is where an invocation has more than one spawn for a failed candidate to affect.)

**Atomic expiring claims** are SQLite rows in `verdict_claim`, carrying an owner and an expiry instant. A claim's key shares three components with a verdict's — session, judge-input hash, rubric version — and deliberately differs on the fourth: a claim is taken *before* the call, so it can only carry `requested_model`, while a verdict carries the concrete `judge_model` from the envelope. Two scorers coordinate only when they requested the same string, which is the other reason to pin a concrete identifier. The mechanism behind "atomic":

- **`PRAGMA journal_mode=WAL`** — a reader proceeds while a writer is active, which matters once two processes are expected rather than merely tolerated. WAL puts `-wal` and `-shm` sidecar files next to the database, so anything cleaning up the store must expect them.
- **An explicit lock-wait bound** (`PRAGMA busy_timeout`, 10s) rather than the implicit five-second default nobody chose. Contention waits the stated bound instead of failing at once.
- **`BEGIN IMMEDIATE` for claim acquisition only** — the liveness check and the write become one decision because the write lock is taken before the read. Under a deferred `BEGIN`, both racers read the identity as unclaimed and the second fails on lock upgrade, which surfaces as a store error rather than as a lost race. Ingest's batch writes stay deferred; taking the write lock earlier there would only widen contention.

The **lease is derived from the judge's wall-clock timeout** plus a margin, never set independently — a lease shorter than the call it guards invites a second scorer to pay for work already in flight, and a fixed number would drift silently the first time the timeout is tuned. The owner is a random token minted once per invocation and logged in the resolved-argument line, carrying no hostname, username, or path.

Losing a claim race is an **outcome, not an error**: the spawn is skipped, reported as claimed elsewhere, and the run exits successfully. Exit code 5 continues to mean a judge failure.

---

## 5. Windows & aggregation

- Flags: `--since 7d|30d|<date>`, `--window this-week`, `--from/--to`.
- Group by `agent_type` within window; show N next to every aggregate.
- **Prior-window delta:** compare to the previous equal-length span (this is what makes it a health check, not a snapshot).
- **Low-volume guard:** below `min_sessions_for_trend` (default 5), show raw scores + count but **suppress trend arrows**, labeled "insufficient data."
- **N counts spawns, not parent sessions.** "implementer: 12 runs this week" may be 3 sessions × 4 spawns — label it as spawns so trends and the low-volume guard aren't misread. Use `task_description` to distinguish same-type spawns in detail views.
- **Intra-session view (parent lens):** because spawns carry `parent_session_id`, roll up "this session fanned out 4 implementers + 3 explorers; 1 failed, 1 hit denials" — a health lens per parent session, not just per agent type.
- **One comparable verdict cohort:** a report selects one rubric version and concrete model, joins verdicts on the session's current input hash, and averages at most one score per spawn. It never combines model or rubric cohorts or chooses a row by insertion order.
- **Complete deterministic slice:** JSON includes one typed row for every qualified spawn, including unscored spawns, then derives agent and parent rollups from those rows.

---

## 6. Outputs & locations

| Surface | Role | Location |
|---|---|---|
| **Terminal** | thin summary: headline score + path, auto-opens HTML | stdout |
| **HTML** | hero, designed, self-contained single file, shareable, `file://` | `./reports/<scope>.html` |
| **Markdown** | Claude handoff — fixes are advisory, rendered as untrusted content | `./reports/<scope>.md` |
| **JSON** | scripting / piping / dashboard feed | `./reports/<scope>.json` or `--format json` to stdout |
| **Store** | append-only dimensional SQLite (source of truth) | the platform's app-data directory (`click.get_app_dir("agentlens")`), overridable with `--store <path>` |

Never write into `.claude/` — read-only.

### Idempotency & re-runs

Running multiple times a day must not duplicate data or clutter the folder. Two layers, two behaviors:

- **Store = append-only truth.** Upsert by qualified `session_id`; re-runs add only new source identities and replace a grain only with a sound, non-stale snapshot. Verdicts are cached by `session_id + judge_input_hash + rubric_version + judge_model` (`judge_model` being the resolved concrete identifier, not the configured alias), so repeat runs re-pay the judge only for changed prepared input, rubric, or model.
- **Reports = overwrite-in-place views.** History lives in the store, *not* in report files — trends and prior-window deltas are always read from the store, never from stale HTML. So report files are disposable, always-current renders.

Naming & overwrite policy:

- **Stable filename per scope, overwritten each run** — no timestamp in the default name:
  - `reports/report_<window>_<agent|all>.{html,md,json}` (e.g. `report_7d_implementer.html`)
  - `reports/session_<session_id>.{html,md,json}` (changes only if re-scored under a new rubric)
- **`--archive`** (opt-in, off by default) drops a timestamped copy into `reports/history/` for audit. Not needed for trends — the store already covers that.
- **No-change short-circuit:** if a run finds no new sessions and the same `rubric_version` + resolved `judge_model`, every verdict is a cache hit, report "no changes since last run" and re-render instantly (free), or skip regeneration entirely.

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

**Single-session slice shipped** (`ingest-single-transcript`, archived 2026-08-18): `agentlens session --file <path>` parses one subagent transcript end to end — qualified identity, snapshot soundness, tool-invocation pairing, two-link name resolution (`.meta.json` sidecar, then an `agent_id`-hash fallback), and a `fact_session`/`fact_tool_event` write to SQLite, with a JSON artifact and terminal summary. The behavior contract now lives in `openspec/specs/{session-command,session-parser,session-report,store-schema}/spec.md`; the "row derived in Python, inside `ingest`" decision is `docs/adr/0008-session-row-derivation.md`.

Still open within Phase 1: bulk discovery under `projects/**` (this slice takes one `--file` at a time, not a directory walk), `main` sessions, `dim_agent`/agent-definition scanning, `bridge_session_skill`, parent lineage through `spawn_tool_use_id`, and name-resolution links 2–3 (`attribution_agent`, the parent's `Task` `subagent_type`). The exit criterion above is therefore only partly met: the store is correctly populated, but parent lineage is not yet resolved.

A few column names landed differently than the sketch in §3 below: on `fact_tool_event`, `seq`→`ordinal`, `input_hash`→`input_fingerprint`, `file_path_hash`→`file_identity`, `output_bytes`→`result_size`; on `fact_session`, `spawn_tool_use_id`→`spawning_tool_use_id`, `n_tool_calls`→`n_invocations`, `n_permission_denials`→`n_denials`, `n_duplicate_tool_calls`→`n_repeated_invocations`. `agent_id`, `agent_definition_id`, `parent_session_id`, `final_report_flagged_partial`, `task_prompt_len`, and `n_skills_fired` are not yet columns — each depends on one of the still-open items above. Treat §3 as the original sketch and the `openspec/specs/` files as the authoritative names going forward.

### Phase 2 — Deterministic signals & aggregation
Derive `fact_session` from events, build `bridge_session_skill` (declared/available/fired), implement windows + prior-window deltas + low-volume guards, emit the deterministic slice of the verdict JSON. **Exit:** `report --since 7d` produces real numbers with no LLM.

### Phase 3 — LLM judge
Pluggable judge interface, `claude -p` backend, rubric v1 (pinned + versioned), caching, `fact_verdict`. **Exit:** sessions get scored + fix proposals; re-runs hit cache.

### Phase 4 — Design system ⟂ (*COMPLETED*)
Dedicated session. Visual identity, design tokens, component library (score/verdict cards, timeline, trend charts, fix-proposal cards), static HTML mockups with fake data. Use the `impeccable` skill. **Exit:** an approved, reusable design spec + mockups. Deliverables: `PRODUCT.md`, `DESIGN.md`, `design/report-mockup.html` (dark theme, primary), `design/report-mockup-light.html` (reference).

### Phase 5 — Renderers
Implement markdown (handoff) + JSON export + thin terminal summary + HTML report (Phase 4 design over real verdict JSON). Every renderer that surfaces fixes or evidence must present them inside an explicitly marked untrusted block and must not emit anything shaped like a patch, diff, or command for direct application. **Exit:** all four surfaces render from one verdict core. **Depends on:** 3 + 4.

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

- **Main-session scoring.** Main sessions are parsed and stored from Phase 1 (`session_kind = main`), but *scoring* them needs an adapted rubric (open-ended conversation, no single delegated task). No data-model change required, just a rubric variant.
- **Sanitized committed fixtures** to close the synthetic-vs-real drift gap in §8.
- **Split `n_spawns_with_errors`** into tool-errors and self-reported-partial as two metrics (§5).
- **Store: Parquet** for warehouse/S3 export.
