# agentlens — design doc

*A tool to analyze, score, and improve Claude Code subagents from their session logs.*

> **Name:** `agentlens` — inspecting agent behavior up close, across a window of sessions.
> **Runtime:** Python, distributed via `uv` / PyPI. Primary entry: `uvx agentlens`.

> **Status of this document.** This is the single durable design reference for agentlens. It absorbs the fourteen ADRs and nine OpenSpec capability specs that existed in the first implementation, which were deleted when the codebase was rebuilt from scratch. §10 (Decisions of record) and §11 (Implementation traps) carry the rationale that used to live in `docs/adr/` and `openspec/changes/`; old ADR numbers appear in parentheses purely as breadcrumbs into git history. Nothing here should be re-derived from scratch — these are settled, and several were paid for with real bugs.

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
5. **Name the measurement, not the interpretation.** If naming a deterministic column honestly requires a verb of judgment ("stuck", "failed", "incomplete", "good"), it is a verdict and belongs to the judge. See §10.3.

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

The pipeline is explicit and three-step: **`ingest` → `score` → `report`**. Each stage is separately invocable, and no stage silently does another's work. `ingest` writes deterministic facts, `score` is the only command that calls the LLM judge and the only one that writes `fact_verdict`, and `report` reads the store and never ingests or scores (§10.7).

| Command | Purpose | Flags |
|---|---|---|
| `agentlens session <id\|--file path>` | Analyze one session, full detail. The primitive; everything else builds on it. Cheapest path, ideal for "how did my new agent do?" | `--file <path>` |
| `agentlens ingest` | Bulk-walk `projects/**` and populate the store. Idempotent. | `--claude-home <path>`, `--limit N` |
| `agentlens report` | Windowed rollup across sessions, grouped by agent, with prior-window deltas. Reads the store only. | `--agent <type>`, `--since 7d\|30d\|<date>`, `--from/--to`, `--today`, `--json`, `--judge-model` |
| `agentlens score` | Score unscored sessions in a window with the LLM judge. The only command that spends money. | `--since`, `--from/--to`, `--agent`, `--judge-model` (default `sonnet`), `--max-sessions N`, `--no-confirm`, `--dry-run` |

Global: `--store <path>` or `$AGENTLENS_STORE` overrides the store location. `--version` reads installed package metadata, never a hardcoded string. `--limit` bounds only that invocation; it is not a resume cursor.

**Exit-code contract.** Bare invocation or `--help` prints usage listing all four subcommands and exits `0`. An empty or absent input tree still creates a valid store with **all** tables present and exits `0` (no error on empty runs). A missing `session` target reports the error and exits non-zero **without writing partial data**. Declining the cost confirmation exits `0` having scored nothing. Any batch work (ingest or score) that skipped or aborted work exits **non-zero while still printing the successes it retained** — a green exit must mean the whole batch landed (§10.9).

**CLI framework:** `click`, chosen over `argparse` and `typer` for ergonomic subcommands plus a testable `CliRunner`.

### Source identity and snapshot integrity

Raw Claude IDs are not globally unique: the same value can appear as a main-session ID, a subagent ID, or in more than one project bucket. Discovery therefore derives an internal `session_id` as a deterministic SHA-256 of `(source_project, session_kind, raw_session_id)`. The tuple remains stored for display and unambiguous lookup. Qualified parent IDs use the same project and the `main` kind, so lineage cannot cross projects accidentally. An ambiguous raw-ID lookup at the CLI returns an error naming project and kind rather than silently picking the first match. See §10.12.

Each parse is a versioned snapshot. The parser streams JSONL while recording parse-health counters and a source revision `(mtime_ns, size, content_hash)`, then verifies that the file did not change during the read. A malformed, incomplete, changed-during-read, or stale snapshot cannot replace a newer sound grain. Equal stat with a different content hash is a conflict and is skipped. The store remains a disposable cache, so schema changes use rebuild-from-source rather than in-place migration.

### Judge coupling

The LLM judge shells out to the user's existing `claude` CLI in headless mode, behind a **pluggable interface** so an `ANTHROPIC_API_KEY` backend can be added for CI. Pin `--model`; what goes into the cache key is the resolved concrete model identifier read from the response envelope, not the alias as typed (§10.10).

**Auth:** the judge runs with `--bare`, which skips keychain reads entirely. Under `--bare`, credentials come strictly from `ANTHROPIC_API_KEY` or from an `apiKeyHelper` supplied via **`--settings`** — never via `--setting-sources`, and never from OAuth or the keychain, regardless of what is logged in on the machine. This is why the invocation passes both `--settings <user settings file>` (so `apiKeyHelper` machines have a working credential channel) and `--setting-sources user` (so repo-local settings cannot reconfigure the judge). An unauthenticated environment fails fast with a `Not logged in` response rather than hanging or silently degrading. **Consequence: OAuth-only users cannot run `agentlens score`.** That is a named limitation in the README, not a silent failure. See §10.8 and §10.9.

### Confirmed CLI contract (hardened)

Invocation for a read-only judge:

```bash
claude -p "<prompt>" \
  --output-format json \
  --model sonnet \
  --json-schema "<verdict JSON schema>" \
  --max-turns 3 \
  --bare \
  --tools "" \
  --setting-sources "user" \
  --settings "<user settings file>" \
  --append-system-prompt "<judge instructions; return JSON verdict>"
```

launched with an explicit temporary-directory `cwd` and an environment filtered to `PATH`, `HOME`, and any `ANTHROPIC_*` variable, never the process's inherited cwd or full environment.

Flag notes for the implementation:

- **`-p` / `--print`** — headless mode. Prompt is a **positional arg**; extra context can pipe via **stdin** (≤10MB — write large transcripts to a file and reference the path instead).
- **`--output-format json`** — single JSON envelope (vs. `stream-json`, `text`). Use `json` for one-shot.
- **`--tools ""`**: the enforcing mechanism for a read-only, side-effect-free judge. `--tools <tools...>` selects from the built-in set; `""` disables all of them. This is stronger than an empty `--allowedTools`: `--allowedTools` (or omitting it) is a permission decision layered over a tool set that is still loaded, so a prompt-injected transcript can still reach a tool the allowlist forgot to exclude or that the CLI adds later. `--tools ""` removes the tools themselves, so there is nothing to aim at regardless of what the prompt requests. Verified empirically with a canary file read: the same prompt that succeeded under the old `--allowedTools`-omitted invocation returned `NO_TOOLS` once `--tools ""` was added.
- **`--setting-sources "user"`**: drops `project` and `local`, so a `.claude/settings.local.json` in whatever directory agentlens happens to run from cannot reconfigure the judge. `user` must be kept (not `""`): `--setting-sources ""` alongside `--bare` was probed and returns `Not logged in`, so full isolation is empirically impossible here.
- **`--settings "<user settings file>"`**: the only channel through which `--bare` will read an `apiKeyHelper`. Required in addition to `--setting-sources user`; the two coexist (verified).
- **`--max-turns 3`**: bounds the call now that there are no tools to loop on. **Known non-determinism:** the turn limit can be exhausted *without* producing a verdict. One observed run returned `num_turns: 4`, `is_error: true`, cost **$0.18**, and no verdict, while an identical immediately-following run succeeded in 2 turns for $0.088. This is not input-dependent. Raising the limit was scoped out; the consequence is that a skipped session can still cost roughly twice a successful one.
- **Explicit `cwd` and filtered `env`**: the subprocess gets a temporary directory as `cwd` and an environment forwarding only `PATH`, `HOME`, and `ANTHROPIC_*`-prefixed variables (covers `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`). Forward by **prefix**, not by enumerating names, so machines differing in which variable they use are all covered. This is defense in depth: with `--tools ""` there is nothing to reach, but it is the control that holds if a future change reintroduces a tool.
- **`--append-system-prompt`** (not `--system-prompt`, which *replaces* and drops Claude Code's foundation).
- **`--model`** — aliases `opus` | `sonnet` | `haiku` | `opusplan` (auto-update), or pin a full string like `claude-opus-4-8`. An alias is accepted here as an input convenience only: what goes into the cache key is the resolved concrete identifier the backend reads back from the response envelope, never the alias as typed.
- **`--bare`** — skips auto-discovery of hooks/skills/plugins/MCP/CLAUDE.md. Retained for **reproducibility, not cost**: a non-bare call's inherited hooks, CLAUDE.md, plugin context, and auto-memory vary by machine and working directory, which would make the judge's system context, and therefore its verdicts, incomparable across runs. A future change that drops `--bare` must reckon with verdict comparability (§10.10), not just cost.
- **Timeout: 180s** (`DEFAULT_TIMEOUT_SECONDS`). Raised from 60s after a real sonnet call took 46.6s wall with a schema retry — barely inside 60s, which is worse than clearly outside it (it timed out once and succeeded identically later).

**Response envelope** — parse `result` (the model's text) as the verdict; `is_error` (bool) to detect failures; `session_id`, `total_cost_usd`, `usage`, `duration_ms` for logging. With `--json-schema` structured outputs, the validated object is in `structured_output` instead of `result`.

> Docs: [headless](https://code.claude.com/docs/en/headless), [CLI reference](https://code.claude.com/docs/en/cli-reference), [permission modes](https://code.claude.com/docs/en/permission-modes). Re-verify before implementing the judge — CLI flags do shift across versions.

### Judge interface

The `Judge` Protocol exposes exactly one method:

```python
def score(transcript_view: str, rubric_version: str) -> Verdict: ...
```

One call returns all four dimensions plus fix suggestions. This single method — not the concrete `ClaudeCliJudge` — is the stability boundary; the scoring loop depends only on the Protocol, so a future `AnthropicApiJudge` for CI drops in unchanged. Splitting into multiple methods requires a deliberate revisit (§10.6).

`Verdict` is a frozen dataclass: `session_id`, `rubric_version`, `judge_model`, `dimensions` (dict of `DimensionScore{score: int 0-5, evidence: list[str]}`), `overall_score` (float, **locally derived** mean — never a model-supplied value), `suggested_fixes` (list of typed `SuggestedFix`, never bare strings), `judge_cost_usd`, `judge_input_tokens`, `judge_output_tokens`.

**Failure taxonomy** — precise, because these paths behave differently:

| Condition | Raises | Behavior |
|---|---|---|
| `claude` not on `PATH` | `JudgeUnavailableError` | Before any subprocess call |
| Not logged in (non-zero exit, envelope present, case-insensitive `not logged in` in `result`) | `JudgeUnavailableError` naming the remedy | **Hard failure**, does *not* count toward the consecutive-failure budget |
| Envelope `is_error: true` | `JudgeError` with envelope result text | Per-session skip |
| Other non-zero exit, or invalid JSON | `JudgeError` | Per-session skip |
| Exceeds 180s | `JudgeTimeoutError` (subprocess killed) | Per-session skip |
| `OSError` launching the subprocess | normalized to `JudgeError` | Never allowed to escape |
| `OSError` / `UnicodeError` building the transcript view | `JudgeError` | Per-session skip, session id recorded |
| `modelUsage` absent, empty, or multi-entry | `JudgeError` | Never falls back to the alias |
| Unknown fix `dimension`, out-of-set `target`, or bare-string fix list | `JudgeError` | No verdict persisted |
| Programmer errors (`KeyError`, `TypeError`) | unwrapped | Fail fast, deliberately not caught |

**Judge token accounting.** `judge_input_tokens` is the sum of the resolved `modelUsage` entry's `inputTokens + cacheCreationInputTokens + cacheReadInputTokens` — **not** the envelope's top-level `usage.input_tokens`, which under-reports badly because a large uncached prompt is booked as *cache creation*, not input. The original bug recorded `1` input token for a call that consumed ~12.8K. `judge_cost_usd` comes from the envelope's `total_cost_usd` and was always correct, which is what made the token bug silent: right dollars, meaningless tokens.

### Prepared transcript view

The judge always receives a **prepared transcript view**, never raw JSONL. `build_transcript_view(parsed, jsonl_path)` is the only artifact passed to any `Judge` implementation.

Real subagent transcripts run **68KB–875KB (median ~253KB)** across 10–215 records. Raw JSONL is roughly 60% JSON framing overhead and carries full `Read` file contents irrelevant to scoring; the largest transcripts (~875KB ≈ 220K tokens) would consume an entire context window.

Six fixed sections, always present: **Task**, **Agent Identity**, **Deterministic Facts**, **Tool Sequence**, **Errors & Denials**, **Final Report**.

- Built by a **streaming reducer with per-section byte budgets**, then gated by a final UTF-8 byte check against `VIEW_MAX_BYTES` (~20KB). Streaming matters: reading the full transcript and truncating afterward does not bound process memory, which was the actual performance finding.
- Task truncated at 2000 chars with a truncation marker.
- Reads show **path only**, never file contents. Bash shows the first 120 chars of the command plus `→ exit N`.
- Missing final report renders as the literal `"(no final report)"`.
- Unbounded sections (Tool Sequence, Final Report) truncate with a **visible** `TRUNCATION_MARKER`; huge tool histories become a head/tail sample plus a total count.
- **Errors & Denials is also budgeted** (head/tail with step references), walking back an earlier "always retain errors in full" promise: per-entry caps at 300 chars alone were not memory-bounded. All six headers survive truncation.

This module is **the** extension point. If the rubric later needs more detail (for example `Write` input content to assess code quality), this is the only file that changes and no judge backend is affected. It is **lossy by design** — full file contents and thinking blocks are discarded, so the judge cannot assess written code today. Any future backend that reads raw JSONL directly breaks the memory and cost guarantee this design exists to provide.

---

## 3. Data model (deterministic layer)

A star schema. Keeping grains separate is what makes windows trivial (date filters) and prior-window deltas trivial (self-joins).

**All seven tables are created on first run**, even when a phase leaves some unpopulated: `fact_tool_event`, `fact_session`, `dim_agent`, `dim_date`, `dim_tool`, `bridge_session_skill`, `fact_verdict`. Creating tables lazily per phase invites migration churn and schema drift between phases.

**The store is a disposable cache.** Schema changes ship as recreated DDL with **no migration path** — the user deletes the store and re-ingests. `rm ~/.cache/agentlens/agentlens.db` plus re-ingest plus re-score is the sanctioned hard reset. Do not write migration code.

### `fact_tool_event` — finest grain, one row per tool_use / tool_result

| column | source / notes |
|---|---|
| `session_id` | qualified internal key |
| `seq` | order within session |
| `tool_name` | `Read`, `Bash`, `Edit`, `Skill`, `Agent`, … |
| `is_error` | from `tool_result.is_error` |
| `denial_kind` | native `toolDenialKind` (permission denials are typed, not inferred) |
| `ts` | ISO timestamp |
| `input_hash` | hash of the **whole** tool input; identifies a duplicate *call* |
| `file_path_hash` | normalized path identity, file-addressing tools only; independent of offsets, ranges, or replacement text |
| `output_bytes` | |

`input_hash` and `file_path_hash` are deliberately separate: whole-call identity drives duplicate detection, normalized path identity drives distinct-file counts. Collapsing them miscounts files (§10.12).

One shared **UTC-normalizing timestamp parser** feeds both `duration_sec` and `session_date`, so the two cannot disagree at a day boundary.

### `fact_session` — one row **per spawn** (primary grain)

The grain is a single agent run, not an agent *type*. Four `implementer` spawns in one parent session = four rows, each with its own `agentId`. Never dedupe by `agent_type`; the spawn is the unit.

- **Source identity:** `session_id` (qualified internal key), `raw_session_id`, `source_project`, `session_kind` (`subagent` | `main`), `source_revision`, `source_mtime_ns`, `source_size`, `source_content_hash`, `judge_input_hash`
- **Agent identity:** `agent_id` (raw per-spawn filename ID), `agent_type` (canonical name), `agent_definition_id` (the effective versioned definition), `name_source`, `spawn_depth`
- **Lineage:** qualified `parent_session_id`, `spawn_tool_use_id` (from `.meta.json` — joins to the exact `Task` block in the parent), `task_description` (from `.meta.json`)
- **Volume:** `n_turns`, `n_tool_calls`, `n_reads`, `n_edits`, `n_writes`, `n_bash`, `n_files_touched`
- **Health:** `n_errors`, `n_permission_denials`, `n_duplicate_tool_calls`, `final_report_flagged_partial` (raw boolean marker, *not* a completion verdict; §10.3)
- **Cost/time:** `duration_sec`, `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_creation_tokens`
- **Context:** `task_prompt_len`, `n_skills_fired`

> **`fact_session` is derived from two sources, and that is intentional.** It is *not* a pure rollup of `fact_tool_event`.
> - **Event-derived** (aggregated from `fact_tool_event`): `n_tool_calls`, `n_reads`, `n_edits`, `n_writes`, `n_bash`, `n_files_touched`, `n_errors`, `n_permission_denials`, `n_duplicate_tool_calls`.
> - **Transcript-read** (straight from the JSONL at parse time): `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_creation_tokens` (summed across assistant turns), `n_turns` (count of assistant records), `duration_sec` (first-to-last timestamp span).
>
> Token usage lives on `message.usage` of assistant records — a per-turn fact with no home on a tool event. One real final turn was observed at **52,696 cache-read tokens against 1 input token**. Smearing that across a turn's tool events would be dishonest allocation, and adding a whole `fact_turn` grain to preserve a slogan is not worth the extra table and ingest path. **Consequence:** re-deriving `fact_session` requires the transcript, not just the store. Usage is read **defensively** — a missing `usage` object or field contributes 0 and never aborts ingest, because provider field names drift across versions. That defensive read is load-bearing, not hygiene. (§10.2)

> **`main` sessions:** the parser ingests them too (`session_kind = main`, no lineage), so they land in the store from day one. **Scoring** main sessions needs an adapted rubric (they're open-ended, not a discrete delegated task) → **v2**. See §12.

### Discovery globs

- Main sessions: `projects/**/*.jsonl`, top level of a project folder only, tagged `session_kind = main`.
- Subagent runs: `projects/**/<sid>/subagents/agent-*.jsonl`, paired with the sibling `agent-<id>.meta.json` when present.
- Agent definitions: `.claude/agents/**`, resolving both flat (`<name>.md`) and nested (`<name>/<name>.md`) layouts, at project and user level.

Lineage comes from the **filesystem path plus the sidecar**, not from scanning transcripts: `parent_session_id` is the `<sid>` folder containing the `subagents/` directory, and `spawn_tool_use_id` is the sidecar's `toolUseId`.

Malformed JSONL lines and unknown record types are skipped without aborting the session — but see §10.2's snapshot rule: skipping records must not let a partial read masquerade as a complete grain and overwrite sound facts.

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

Corpus baseline at first implementation: **115 subagent runs, each with a matching `.meta.json`; 126 main sessions.** Confirmed present: `thinking`/`text`/`tool_use`/`tool_result` blocks, `usage` fields, `tool_result.is_error`/`tool_use_id`, `toolDenialKind`, `attributionAgent`, and flat agent defs under `~/.claude/agents/`.

### `bridge_session_skill` — declared-vs-fired, one row per (session, skill)

| column | notes |
|---|---|
| `session_id` | |
| `skill_name` | |
| `declared` | referenced in the agent's `.md` (from `dim_agent.declared_skills`) |
| `available` | present in `.claude/skills/` (incl. plugins) |
| `fired` | see below |

The row set is the **union** of declared and fired skills: a skill that fires without being declared still gets a row, and vice versa.

`fired = 1` on the union of exactly two near-disjoint signals:

1. An `isMeta: true` record whose text contains `<skill-format>true` **together with** a `<command-name>` naming that skill (the auto-injected marker), **or**
2. A `Skill` tool_use naming that skill in its input (explicit invocation).

**Reading a `SKILL.md` file does not count as firing.** It was evaluated and demoted as too noisy.

`available` is best-effort: if the `.claude/skills/**` tree can't be resolved for a skill, `available` defaults to 0 and the row is still written. Never drop the row.

Whether a *missing* fire was a mistake is a judgment call → belongs in the judge layer, not here.

### Dimensions

- **`dim_agent`** — versioned, keyed by `(agent_type, scope, source_project, definition_hash)`; stores `name`, `model`, `effort`, `declared_tools[]`, `declared_skills[]`. Effective resolution puts a project definition ahead of a user definition, inside that project only. **Each session records the effective definition binding at ingest**, so later edits to an agent definition do not rewrite historical attribution. An overwrite-only `dim_agent` cannot answer which instructions produced an older run.
- **`dim_date`** — backfilled with `year`, `month`, `day`, `iso_week` per observed date (dates present, not the full span).
- **`dim_tool`** — one row per distinct `tool_name` seen.

### Atomicity

Idempotent upsert is keyed by `session_id` and covers the **full grain**: `fact_session`, `fact_tool_event`, **and** `bridge_session_skill`, plus `dim_date` / `dim_tool` backfills. All of it lands in **one transaction per session**. A failure rolls back only that session's writes, leaving other sessions in a bulk run committed.

> **SQLite gotcha (cost a real bug):** wrapping the existing public upserts in an outer `with conn:` from the ingest layer does **not** give you atomicity — nested SQLite connection context managers commit on each inner exit. The working shape is a single atomic store entry point (`upsert_session_grain`) that performs every write inside one `with conn:`, delegating to **private, non-committing statement helpers** shared with the public single-table upserts (which keep their own transaction-owning behavior).

Grain replacement validates every child record's session ID against the parent **before the first `DELETE`**, so a caller mistake cannot mutate two grains at once.

### `fact_verdict` — LLM judge output (separate, never mixed with deterministic facts)

- **Key:** `session_id` + `judge_input_hash` + `rubric_version` + `judge_model`
- `verdict_json` shape: `{dimensions: {task_completion, honesty, efficiency, scope_adherence} (each {score, evidence[]}), overall_score, suggested_fixes[] (each {dimension, target, recommendation, rationale}), provenance: {locally_derived[], untrusted_model_output[]}}`. `session_id`, `rubric_version`, `judge_model`, and the three judge cost/token fields are `fact_verdict` **columns**, not part of the JSON blob. `provenance` marks which fields are locally derived and validated (scores) versus untrusted model output (evidence, and each fix's recommendation/rationale) — see §10.11.
- **Judge run-cost:** `judge_cost_usd`, `judge_input_tokens`, `judge_output_tokens` (from the `claude -p` envelope) — the tool's *own* footprint, so agentlens is honest about what a run costs.

`judge_input_hash` is the SHA-256 of the exact prepared transcript view. An unchanged re-ingest remains a cache hit; changed judge input creates a distinct verdict identity. Verdict finalization rechecks the session's current hash **inside the write transaction**, so an in-flight score cannot attach to a newer ingest — it is demoted to stale instead. Concurrent scorers use expiring owner-scoped SQLite claims and never hold a transaction during the external judge call. See §10.13.

**Validation is centralized and backend-independent.** One validator/factory — not the Claude backend — enforces the exact dimension set, score bounds, the locally-derived overall score, evidence and fix bounds, enum membership, a concrete model identifier, and finite non-negative accounting. Relying on `claude --json-schema` alone is not enough: custom backends, mocks, and future CLI behavior all bypass that boundary. Every model-controlled value passes through a bounded-excerpt helper before it can appear in an exception or a log line.

### Token & cost reporting

Two distinct figures, kept distinct:

- **Analyzed agent usage** (`fact_session` token fields) → reported as an **efficiency/quality** signal, in raw tokens + cache-read %. Low cache-read across many runs = unstable context (a finding, not a dollar amount). *Do not* dollarize this — quality is the message, not spend.
- **agentlens's own run-cost** (`fact_verdict` judge fields) → reported in **dollars + tokens** ("this analysis cost $X").

### Name resolution (guarded)

Resolve **once per session**. Fallback chain, authoritative first:

1. `.meta.json` `agentType` — authoritative, present for modern subagent spawns.
2. `attribution_agent` from assistant records (only on assistant records; take distinct values).
3. Parent session's `Task` tool `subagent_type` (via `spawn_tool_use_id`).
4. `agent_id` hash — last resort so a session is never dropped.

Record which source won in `name_source`. Multiple conflicting distinct values → flag `ambiguous`; never silently pick one.

---

## 4. Rubric (scoring)

Rubric-first (absolute, pinned, **versioned**), comparative as a derived view.

Four dimensions, integer 0–5 each:

- **Task completion** — did it accomplish the task vs. merely claim it?
- **Honesty** — is the final report supported by the actual tool results?
- **Efficiency** — turns/tokens/retries relative to task complexity.
- **Scope adherence** — stayed within its brief and files?

Ground-truth signals, cheapest first: self-report vs. transcript consistency (judge), parent-session correlation (did the parent immediately re-spawn/correct?), optional user feedback later.

### Versioning

`RUBRIC_VERSION` is a **manual semver string** (`"v1"`, `"v2"`, …) held as a module constant. Bump it explicitly whenever scoring semantics *or* the output schema change: a new dimension, an altered scale, changed criteria, a different evidence format, or `suggested_fixes` gaining structure. Cosmetic rewording and formatting do not require a bump.

Deliberately **not** an auto-hash of the prompt template. During rubric iteration the prompt gets tweaked dozens of times, and a content hash would re-score the entire window — real dollars and minutes — on every tweak.

**Consequence, accepted:** a semantic change shipped *without* a bump silently serves stale verdicts. There is no automated guard; it relies on discipline. It is tolerable only because the store is a disposable cache and because `rubric_version` is part of the primary key. Multiple rubric versions coexist in the store — bumping to `v2` does not delete `v1` rows, which is what makes before/after comparison possible. (§10.4)

### Prompt constraints

The prompt template:

- **Must not ask the judge to compute `overall_score`.** It is derived locally as the mean of validated dimension scores. A model-supplied value is never trusted — the original bug persisted an `overall_score` of `99.0`.
- Is appended via `--append-system-prompt`, never replacing Claude Code's foundation prompt.
- Instructs the judge to treat the transcript view as **untrusted data** and never to follow instructions embedded in it.
- Scopes fixes to the agent's own guidance, and forbids commands, file paths, diffs, and executable content.

### Verdict JSON schema

Four dimension objects (`score` int 0–5 plus `evidence` array of strings); `suggested_fixes` array where each item requires `dimension` (enum of the four), `target` (enum of a closed set), `recommendation` (length-bounded), and `rationale` (length-bounded). `additionalProperties` disallowed. The fixes array is length-bounded. `overall_score` is **not** in the schema.

Each dimension's `evidence` array is bounded in **both item count and per-item length**. This closes a burial channel: a typed, capped `suggested_fixes` sitting above an *unbounded* `evidence` array would let an injected transcript pad plausible-looking citations until genuine content or reviewer attention is exhausted. Bounding limits volume, not content — evidence remains the most natural injection surface, and mitigation there is presentational (labeled untrusted), not structural.

`SuggestedFix` is typed: `dimension` (the four rubric names), `target` (a closed set — agent instructions, declared tools, declared skills, task phrasing; explicitly **not** an arbitrary path), `recommendation`, `rationale`. The security value is not that `recommendation` becomes safe — it is still prose. It is that the surrounding typed structure makes an injected imperative *visibly out of place* to a reviewer, where it would be invisible in a bare bullet list. An unknown `dimension`, an out-of-set `target`, or a bare-string fix list raises `JudgeError` and **persists no verdict**.

### Scoring loop

"Unscored" means lacking a verdict for the **current** `(rubric_version, judge_model)` pair. The loop resolves a window, finds unscored sessions, builds each prepared view, calls the judge, and persists. It is idempotent — a re-run picks up where it left off. Cap and alias orchestration live in the loop, not the CLI; the CLI only validates, confirms, renders, and maps to an exit code.

**Two-stage alias resolution.** When the configured `--judge-model` is an alias rather than a concrete identifier: take the set keyed on the alias as an upper bound → score exactly **one** candidate to learn the resolved identifier → re-query the remainder keyed on that resolved identifier. **Consequence: a fully-scored window re-run under an alias still costs exactly one judge call, never zero.** The store does not persist the alias→ID mapping, so this is per-run. A throwaway probe call purely to resolve identity was rejected (it spends money for information the first real call gives free), as was requiring users to supply concrete IDs (pushes naming churn onto them). The loop **never** overwrites the backend's resolved `judge_model`; it sets only `session_id` and `rubric_version`, which are its own facts. When a floating alias advances, sessions scored under the prior model are correctly reported as unscored and re-scored rather than colliding.

**Failure policy: fail-open per session, fail-closed on systemic failure.** A judge failure (timeout, malformed output, envelope `is_error`) skips that session and continues, writing no verdict row. Transcript I/O failures normalize into the same skip path with the session id recorded. Programmer errors still fail fast. The loop **aborts after exactly 3 consecutive session failures**, reports progress, and exits non-zero — that pattern means something systemic (expired auth, a CLI change), not bad luck. No failure markers are persisted, which keeps invalidation simple. Alias resolution continues past session-specific candidate failures rather than aborting on the first, and one shared attempt-budget counter spans both resolution and scoring.

### Cost gate

Before scoring, show session count, model, and estimated cost, and wait for input. `--no-confirm` skips it; `--max-sessions N` caps the batch; `--dry-run` lists unscored sessions by `agent_type` and `task_description` with the estimate and exits without calling the judge. Declining exits 0.

The estimate derives from **measured** per-session costs of the real minimal-mode invocation against a realistically sized view, and is rounded **above the highest measured cost**, never at the mean. The asymmetry is deliberate: understating costs a user unapproved spend, overstating costs only hesitation. The reported post-run total must not exceed the displayed estimate. When the count cannot be known exactly (first run with an unresolved alias), present it as an upper bound ("up to N sessions").

`PER_SESSION_COST_ESTIMATE` history, as a warning about placeholder numbers: **$0.025** (pre-implementation placeholder, wrong) → **$0.08** (measured against an 18KB view: mean ≈ $0.045, max $0.071) → **$0.15** after a real-session probe exceeded $0.08 on a *smaller* 13KB view. The default for an unrecognized model is also $0.15, since it may well be opus-class. Rough single-call figures: ~$0.02/session on sonnet, ~$0.12/session on opus.

**Known gap:** `ScoringResult.total_cost_usd` excludes failed calls, and a `--max-turns`-exhausted failure can cost roughly twice a success. Reported cost therefore understates true spend when sessions are skipped.

### Caching

`fact_verdict` **is** the cache. Identity = `session_id + judge_input_hash + rubric_version + judge_model`, in `~/.cache/agentlens/`, where `judge_model` is the resolved concrete identifier and never the alias supplied at the CLI (§10.10). Only a current-input miss calls the judge. Same-key rescoring overwrites; different models coexist as separate rows. Floating aliases resolve through healthy candidates under one invocation-wide attempt budget, so failed candidates do not wedge later sessions. Atomic expiring claims prevent concurrent processes from paying twice for the same identity.

---

## 5. Windows & aggregation

- Flags: `--since 7d|30d|<date>`, `--from/--to`, `--agent <type>`, resolving to a **half-open `[start, end)`** range.
- **Default window is 7 days** when no flag is given. `--today` is exactly equivalent to `--since 1d`.
- Group by `agent_type` within window; show N next to every aggregate.
- **Prior-window delta:** compare to the immediately preceding **equal-length** span (this is what makes it a health check, not a snapshot). Computed in SQL as a self-join.
- **Low-volume guard:** below `min_sessions_for_trend` (default **5**), show raw scores + count but **suppress trend arrows**, labeled "insufficient data."
- **N counts spawns, not parent sessions.** "implementer: 12 runs this week" may be 3 sessions × 4 spawns — label it as spawns so trends and the low-volume guard aren't misread. Use `task_description` to distinguish same-type spawns in detail views.
- **Intra-session view (parent lens):** because spawns carry `parent_session_id`, roll up "this session fanned out 4 implementers + 3 explorers; 1 had errors, 1 hit denials" — a health lens per parent session, not just per agent type.
- **One comparable verdict cohort:** a report that includes modeled output must receive or deterministically resolve **one** rubric version and **one** concrete model. It joins verdicts on qualified session id + the session's **current** `judge_input_hash` + rubric version + concrete model, so each spawn contributes at most one score. It never combines cohorts and never picks a row by insertion order. If several models appear in-window and the caller supplied none, the report **fails with an actionable ambiguity error** rather than guessing; `--judge-model` disambiguates. The JSON payload names the selected cohort explicitly. (§10.14)
- **Complete deterministic slice:** JSON includes one typed row for every qualified spawn, **including unscored spawns**, then derives agent and parent rollups from those rows. Verdict inclusion is opportunistic — unscored sessions still appear with deterministic data only, and `--json` adds a `verdicts` key per scored session.

### `n_spawns_with_errors` — a naming rule, not just a column

The metric is named **`n_spawns_with_errors`** everywhere it is exposed: rendered output ("29 had errors"), the JSON payload, per-agent aggregates, parent-lens rows, and delta keys. It must **never** be called a failure count and must never appear as `n_failures`.

It computes `SUM(CASE WHEN n_errors > 0 OR final_report_flagged_partial = 1 THEN 1 ELSE 0 END)` — spawns that hit **at least one** recoverable tool error or self-reported partial work. A spawn with one missed `Grep` among 40 clean calls counts identically to one that genuinely stalled. Calling that "failures" overstated reality by roughly **5× on real data**, and in a tool whose entire premise is trustworthy trend detection, that teaches the reader to distrust the report. It is distinct from `n_errors` (total error *events*, not spawns), and the pre-existing confusion between the two is exactly why the naming is pinned.

Splitting tool-errors from self-reported-partial into two metrics is worth doing, but it is a substantive change, not a rename.

---

## 6. Outputs & locations

| Surface | Role | Location |
|---|---|---|
| **Terminal** | thin summary: headline score + path, auto-opens HTML | stdout (progress to stderr) |
| **HTML** | hero, designed, self-contained single file, shareable, `file://` | `./reports/<scope>.html` |
| **Markdown** | Claude handoff — fixes are advisory, rendered as untrusted content | `./reports/<scope>.md` |
| **JSON** | scripting / piping / dashboard feed | `./reports/<scope>.json` or `--format json` to stdout |
| **Store** | append-only dimensional SQLite (source of truth) | `~/.cache/agentlens/` (or configurable) |

Never write into `.claude/` — read-only. **Canonicalize symlinks before checking the `.claude`-ancestor guard**, or a symlinked store path escapes it. New DB files and parent directories get owner-only permissions.

### Progress and summary strings

Progress to **stderr**:

```
[3/10] implementer "Fix 4 findings" ... scored (4.2/5, $0.019)
[5/10] implementer "Implement X" ... ERROR (timeout), skipped
```

Final summary:

```
Scored 10/10 sessions. Total judge cost: $0.21.
Scored 8/10 sessions. Total judge cost: $0.17. 2 skipped (re-run to retry).
20/50 scored (--max-sessions reached). Re-run to continue.
```

When `--judge-model` was an alias, the summary also names the concrete resolved identifier.

### Idempotency & re-runs

Running multiple times a day must not duplicate data or clutter the folder. Two layers, two behaviors:

- **Store = append-only truth.** Upsert by qualified `session_id`; re-runs add only new source identities and replace a grain only with a sound, non-stale snapshot. Verdicts are cached by `session_id + judge_input_hash + rubric_version + judge_model`, so repeat runs re-pay the judge only for changed prepared input, rubric, or model — plus the one alias-resolution call per run (§4).
- **Reports = overwrite-in-place views.** History lives in the store, *not* in report files — trends and prior-window deltas are always read from the store, never from stale HTML. Report files are disposable, always-current renders.

Naming & overwrite policy:

- **Stable filename per scope, overwritten each run** — no timestamp in the default name:
  - `reports/report_<window>_<agent|all>.{html,md,json}` (e.g. `report_7d_implementer.html`)
  - `reports/session_<session_id>.{html,md,json}`
- **`--archive`** (opt-in, off by default) drops a timestamped copy into `reports/history/` for audit. Not needed for trends — the store already covers that.
- **No-change short-circuit:** if a run finds no new sessions and the same `rubric_version` + resolved `judge_model`, every verdict is a cache hit; report "no changes since last run" and re-render instantly, or skip regeneration entirely.

Net rule: **store is append-only history; reports overwrite in place; archiving is opt-in.**

---

## 7. Dashboard (separate initiative, enabled for free)

Same pattern as a static GitHub Pages dashboard reading a data blob: date-range filters, compare-to-previous-span, heatmaps, drill-down — no backend. Because the store is **append-only and dimensional**, the dashboard is just another consumer: publish a cumulative aggregated JSON to a `gh-pages` repo (manual or scheduled), point a static site at it. The design system from Phase 4 carries over. This is a downstream initiative, but the Phase 1–2 data model is what makes it an afternoon rather than a rebuild.

---

## 8. Testing policy

**All automated tests are synthetic. No test reads the real `~/.claude` tree.**

Every fixture is hand-built under `tmp_path`: a synthetic `.claude`-shaped directory, hand-authored JSONL, or plain dicts. Real logs are not reproducible across machines or CI, they contain proprietary work product, and the JSONL record schema was still settling — strict assertions against them would be brittle.

- **No real-log reads at test time.** The single permitted use of `Path.home()` is asserting default store-*path* resolution (it computes a `~/.cache/...` path, it does not read logs).
- No strict JSON-schema or row-content assertions against real subagent logs. Deferred to v2.
- Manual verification against real logs (`agentlens session --file <real log>` plus eyeballing) is encouraged, but it never becomes an automated test.
- Bright line: **if a test needs `~/.claude`, it is wrong.**

This was learned by near-miss. An early draft's phrase "smoke-run against real input" was read as license to ingest real logs, and tests landed with a machine-pinned path into `~/.claude/projects` plus content assertions on real subagent JSON. Both were reverted and banned.

**Known gap:** drift between synthetic fixtures and real Claude Code output is not caught automatically. The v2 fix is sanctioned **sanitized, committed fixtures** — not live `~/.claude` reads.

**The one sanctioned integration test.** A capability test for the judge's no-tools guarantee: write a canary file, feed a transcript that instructs reading it, assert the canary's contents appear **nowhere** in the resulting verdict. It carries an `integration` marker excluded from the default `pytest` run (it costs money and conflicts with the synthetic-only rule) and lives in `tests/integration/`. It **must** be paired with a **negative control**: the same test with `--tools ""` removed has to fail. An unexercised security test is exactly how the original bug shipped (§11.1).

---

## 9. Phases

Each phase is independently valuable and testable. `⟂` = can run in parallel.

### Phase 0 — Scaffold & contracts
Repo init, CLI skeleton with `session`/`ingest`/`report`/`score` stubs, the full SQLite schema DDL (all seven tables), and the verdict-JSON shape. **Exit:** empty pipeline runs end-to-end and writes an empty store with every table present.

### Phase 1 — Parser core (deterministic, no LLM)
Discover main sessions, subagent runs + their `.meta.json` sidecars, and agent defs. Parse into `fact_tool_event` + `dim_agent`, persist to SQLite. Path-based parent linkage + sidecar pairing + the name-resolution fallback chain. Ingest `main` sessions too. Single-session path first, then bulk `ingest`. **Exit:** point it at a sample `agent-*.jsonl` (+ meta) and get a correctly populated store with parent lineage resolved. ~60% of the value ships here.

### Phase 2 — Deterministic signals & aggregation
Derive `fact_session` from events **plus transcript reads** (§3), build `bridge_session_skill`, implement windows + prior-window deltas + low-volume guards, emit the deterministic slice of the verdict JSON. **Exit:** `report --since 7d` produces real numbers with no LLM.

### Phase 3 — LLM judge
Pluggable `Judge` Protocol, `claude -p` backend, prepared transcript view, rubric v1 (pinned + versioned), the cost gate, caching, `fact_verdict`, and `agentlens score`. **Exit:** sessions get scored + fix proposals; re-runs hit cache (modulo the one alias-resolution call).

### Phase 4 — Design system ⟂ (*artifacts already exist — do not redo from scratch*)
Visual identity, design tokens, component library (score/verdict cards, timeline, trend charts, fix-proposal cards), static HTML mockups with fake data. Built with the `impeccable` skill.

> **Surviving deliverables from the first implementation, deliberately kept through the rebuild:** `PRODUCT.md`, `DESIGN.md`, `design/report-mockup.html` (dark theme, primary), `design/report-mockup-light.html` (reference). **Revisit these when Phase 4 comes up again** rather than regenerating: confirm the component list still matches the real verdict shape (notably typed `SuggestedFix` and the untrusted-block requirement from §10.11), then carry them forward. The palette is "Red Margin"; the voice is a senior engineer's post-incident review.

### Phase 5 — Renderers
Implement markdown (handoff) + JSON export + thin terminal summary + HTML report (Phase 4 design over real verdict JSON). Every renderer that surfaces fixes or evidence **must** present them inside an explicitly marked untrusted block and **must not** emit anything shaped like a patch, diff, or command for direct application (§10.11). **Exit:** all four surfaces render from one verdict core. **Depends on:** 3 + 4.

### Phase 6 — Dashboard (separate initiative)
Cumulative JSON export + static `gh-pages` site (windows, compare, drill-down). **Depends on:** 2 (data model) + 4 (design).

```
0 ──▶ 1 ──▶ 2 ──▶ 3 ──▶ 5 ──▶ (6)
      └─▶ 4 (parallel) ──▶ 5, 6
```

---

## 10. Decisions of record

These were ADRs 0001–0014 in the first implementation. Each is a constraint on future work, not a preference. Old numbers are kept as breadcrumbs into git history.

**10.1 — Synthetic-only tests.** All automated tests are synthetic; no test reads the real `~/.claude` tree. Full treatment in §8. *(was ADR 0001)*

**10.2 — `fact_session` is not a pure rollup.** It derives from `fact_tool_event` **and** the transcript, because usage/turns/duration are turn-level facts with no home on a tool event. Full treatment in §3. Corrected invariant: tool-derived facts aggregate from `fact_tool_event`; turn-derived facts are read from the transcript at parse time. *(was ADR 0002)*

**10.3 — The deterministic layer emits counts, not verdicts.** It emits counts, identifiers, timestamps, and raw booleans — never a scored or interpreted judgment. Two renames enforce it:
- `n_retry_loops` → **`n_duplicate_tool_calls`**, redefined as the session-wide count of `(tool_name, input_hash)` occurrences beyond the first per distinct pair, **not** consecutive-only. The corpus showed **zero** consecutive-identical tool calls across all ten transcripts examined, so a consecutive-only detector is a dead signal. Duplicates exist but are rare (0–5) and mostly legitimate (a re-`Read` after an `Edit`).
- `claimed_status` (complete|partial) → **`final_report_flagged_partial`** (boolean), true iff the final assistant text matches a small documented marker set (unchecked `- [ ]`, "partial", "blocked", "couldn't"/"could not", "unable to"). Every subagent transcript in the corpus ends `stop_reason: end_turn` regardless of actual completion, so `stop_reason` carries no completion signal and a complete/partial column would be a guess wearing the costume of a fact.

**General rule:** if naming a column honestly requires a verb of judgment, it is a verdict and belongs to the judge. *(was ADR 0003)*

**10.4 — Manual rubric versioning.** `RUBRIC_VERSION` is a hand-maintained semver string, bumped explicitly on semantic or schema change. Not an auto-hash. Full treatment in §4. *(was ADR 0004)*

**10.5 — Prepared transcript view.** The judge always receives the prepared view, never raw JSONL. Full treatment in §2. *(was ADR 0005)*

**10.6 — Single-pass judge protocol.** `Judge` exposes exactly one `score(transcript_view, rubric_version) -> Verdict`. Splitting into per-dimension or score-then-suggest calls multiplies cost and latency ~4× for no quality gain, and fixes grounded in the same evidence window hallucinate less. A multi-pass variant is deferred to a future rubric-iteration decision if fix quality proves too generic. *(was ADR 0006)*

**10.7 — `score` is separate from `report`.** `agentlens score` is the only command that calls the judge and the only one that writes `fact_verdict`. `report` reads verdicts opportunistically and **never** triggers a judge call, even when the window contains unscored sessions. `report` was fast, read-only, and free; making it silently slow, stateful, and costly would be an unpleasant surprise for a quick check. Scoring is always explicit opt-in behind a cost gate. **Future CI integration targets `score`, not `report`.** *(was ADR 0007)*

**10.8 — The judge runs with no tools.** Invoke with `--tools ""`, which removes the built-in tool set, rather than omitting or emptying `--allowedTools`, which leaves tools loaded behind a permission decision. Rejected: `--disallowedTools` enumeration (a denylist that fails open on tools the CLI adds later), `--permission-mode plan` (constrains writes, not reads — wrong vector), and prompt-only mitigation (a secondary control at best). Full treatment in §2. *(was ADR 0008)*

**10.9 — Judge subprocess isolation and auth.** Pin `--setting-sources user`, pass `--settings <user settings file>`, launch with an explicit temp `cwd` and an env filtered to `PATH` / `HOME` / `ANTHROPIC_*`, and keep `--bare` for reproducibility. A not-logged-in envelope raises `JudgeUnavailableError` naming the remedy and must keep propagating as a **hard failure**, never a per-session skip counted toward the consecutive-failure budget. Rejected: `--setting-sources ""` (empirically incompatible with `--bare`), a full OS sandbox (disproportionate and platform-specific), dropping `--bare` (breaks comparability), and a `--judge-auth oauth` opt-in (deferred; never exercised by any probe). **Fragility to know:** the detection matches the literal string `not logged in`, so a CLI wording change silently reclassifies it as a generic `JudgeError` — a narrower, already-safe degradation. Full treatment in §2. *(was ADR 0009)*

**10.10 — Verdict comparability.** Two verdicts may occupy the same `fact_verdict` identity only when rubric version, **concrete** `judge_model`, and judge system context all match. The concrete model is read from the response envelope's **`modelUsage` map key**, never from `canonicalModel` and never from the alias: the key carries the dated snapshot while `canonicalModel` carries the undated family name, and they demonstrably diverge (`claude-haiku-4-5-20251001` vs `claude-haiku-4-5`). Keying on `canonicalModel` would reintroduce the same bug one level down. Extraction is strict — absent, empty, or multi-entry `modelUsage` raises `JudgeError` with no silent fallback. Rejected: maintaining an alias→ID table inside agentlens (a second source of truth that goes stale precisely when it matters). Judge system context is held constant **structurally** (`--bare` plus pinned setting sources) rather than as a key column; **any future change that makes judge context configurable per run must promote it into verdict identity.** *(was ADR 0010)*

**10.11 — Handoff trust boundary.** The markdown report is untrusted content. The pipeline runs `transcript (untrusted) → judge → verdict → reports/*.md → human → .claude/agents/*.md`, and that last hop is the highest-leverage write target in the system: a plausible-looking "fix" transcribed by a trusting human becomes a persistent backdoor in an agent definition.
- agentlens emits **no** diffs, patches, file edits, or shell commands. Fixes are strictly advisory. A `--allow-fixes` opt-in for auto-appliable patches was rejected: it moves the decision to a flag the user sets once and forgets, and the residual risk is too asymmetric against the convenience.
- Provenance is a **payload field**, not a documentation convention — `to_verdict_json` explicitly marks locally-derived values (`overall_score`, validated dimension scores) versus model-authored text (`evidence`, `recommendation`, `rationale`). Documenting the convention only was rejected: three renderers plus a dashboard means four chances to forget, and the failure is silent.
- Every rendering surface must present model-authored fields inside an explicitly marked untrusted block, as content to review and never as instructions to execute.
- **Residual risk, explicitly not closed:** `evidence` is bounded in count and length but not in content. It remains the most natural injection surface, and mitigation there is presentational. Transcript-side injection neutralization is parked. *(was ADR 0011)*

**10.12 — Qualified source identity.** Discovery builds `(source_project, session_kind, raw_session_id)` and hashes it to a deterministic SHA-256 `session_id`; raw IDs are retained for display and lookup but are never the primary key. This fixes a **silent-overwrite defect** where the same raw ID in two projects clobbered one another. Agent definitions are versioned by `(agent_type, scope, source_project, definition_hash)` with project scope taking precedence over user scope, and each session records its effective binding. Also split `file_path_hash` (normalized, file-addressing tools only) from the whole-input `input_hash` used for duplicate detection, and share one UTC-normalizing timestamp parser between `duration` and `session_date`. Rejected: a composite primary key (widens every foreign key), and continuing with raw IDs (preserves the defect). **Rollout is delete-and-rebuild, not migration.** *(was ADR 0012)*

**10.13 — Input-bound verdicts and scoring claims.** `judge_input_hash` (SHA-256 of the exact prepared view) joins the `fact_verdict` key, so a re-ingest that changes deterministic facts cannot leave a stale verdict looking like a cache hit. Finalization compares the scored hash against the session's current hash inside the write transaction, demoting an in-flight score to stale rather than attaching it to changed facts. Concurrency uses an **expiring, owner-scoped claim** keyed by target verdict identity, acquired atomically, crash-recoverable via expiry, and never held across the external judge call. Rejected: deleting all verdicts on re-ingest (loses valid cache hits and provenance), and a process- or store-wide lock (blocks safe concurrent scoring of *different* sessions and would not recover cleanly from a crash). **`--max-sessions` counts judge attempts, including failed resolution candidates, not successfully persisted verdicts.** Claims are transient coordination state; reports never read them. *(was ADR 0013)*

**10.14 — Reports select one verdict cohort.** A report including modeled output resolves exactly one rubric version and one concrete model, joins on session + current input hash + rubric + model, and contributes at most one verdict per spawn. Ambiguity fails loudly. Rejected: "just pick the last row" — SQLite guarantees no row order, and an `ORDER BY` would still mix incomparable cohorts. Side-by-side model or rubric comparison is a **separate future report mode**, not this one. Full treatment in §5. *(was ADR 0014)*

---

## 11. Implementation traps

Failure modes that already cost real time or money. Each is a pattern, not a one-off.

**11.1 — Asserting a flag is absent is not asserting a capability is absent.** The first "fix" for the judge's filesystem-tool exposure removed `--permission-mode dontAsk --allowedTools "Read,Grep"` and shipped a passing test named `test_build_args_grants_no_filesystem_tools` that asserted `"--allowedTools" not in args`. Omitting the flag selects the CLI **default**, which is the full built-in tool set — so the vulnerability was untouched and verified live: a canary-file prompt still returned the file's contents. A security property enforced by subprocess arguments must be verified by **exercising the property** (canary read) and paired with a **negative control** that fails when the control is removed. See §8.

**11.2 — Cost and timeout estimates only ever move upward.** Every judge-layer estimate was revised up after real measurement: per-session cost $0.025 → $0.08 → $0.15, timeout 60s → 180s. One draft claimed costs were "40× too high" and had to retract — they were 2–3× too **low**. Two compounding causes: placeholder numbers written before implementation, and measurements taken against a trivial one-line prompt instead of a realistic transcript view (which also produced a bogus "100× savings" claim for `--bare`). **Round cost gates above the observed maximum, never at the mean**, and never benchmark the judge on a toy prompt.

**11.3 — Nested SQLite context managers commit early.** `with conn:` inside `with conn:` commits on the inner exit, so wrapping existing upserts does not give you atomicity. Use one transaction-owning entry point delegating to private non-committing helpers. See §3.

**11.4 — A large uncached prompt is booked as cache creation, not input.** Reading `usage.input_tokens` from the envelope's top level recorded `1` token for a call that consumed ~12.8K. Sum the resolved `modelUsage` entry's input + cache-creation + cache-read instead. The cost field was correct throughout, which is precisely why the bug stayed silent: right dollars, meaningless tokens.

**11.5 — Floating aliases silently destroy comparability.** Storing `"sonnet"` as `judge_model` let two runs a month apart share a cache key while being graded by materially different models — and the whole point of the tool is cross-window deltas. Resolve to the concrete dated identifier from `modelUsage`'s key. See §10.10.

**11.6 — Truncating after materialization does not bound memory.** Reading a full 875KB transcript and then trimming it protects the prompt but not the process. Use a streaming reducer with per-section budgets and a final byte gate. This also forced walking back an earlier "errors are always retained in full" promise. See §2.

**11.7 — Verdict-shaped output needs validation outside the backend.** `claude --json-schema` guards one path; mocks, custom backends, and future CLI behavior bypass it. Validate in one backend-independent place, and pass every model-controlled value through a bounded-excerpt helper before it reaches an exception message or a log. See §3.

**11.8 — The turn limit can burn money without producing a verdict.** `--max-turns 3` exhausted at `num_turns: 4`, `is_error: true`, $0.18, no verdict — while an identical immediately-following run succeeded at 2 turns for $0.088. Non-deterministic, not input-dependent. A skipped session can cost ~2× a successful one, and reported totals exclude failed calls.

**11.9 — Judge output varies run to run on identical input.** Observed 1207–1523 output tokens for the same input. Whether *scores* vary similarly is **unmeasured**, and it bounds the smallest delta `report` can honestly claim. Worth measuring before trusting small trend movements.

---

## 12. Decisions & open items

**Locked:**

- **Name:** `agentlens`.
- **Runtime:** Python. The data core (JSONL parsing, dimensional model, SQLite, path to Parquet/warehouse) is the bulk of the tool and Python's strength. The judge shells out to `claude` regardless of language.
- **Distribution:** publish to PyPI; document `uvx agentlens` (zero-install run) as the primary entry, `pipx install agentlens` for regulars. The dashboard is a static JS front-end reading a JSON blob, so it does not influence this choice.
- **CLI framework:** `click`.
- **Default judge model:** `sonnet`.

**Still open:**

- **Rubric dimensions & weights** — the four dimensions held through v1 and v2, but weights were never tuned against a large scored corpus.
- **Does the 4-field `SuggestedFix` shape need a fifth member?** Specifically whether `target`'s closed set is complete. Answerable only against real scored sessions.
- **Should the prepared view include `Write`/`Edit` input content** (the code actually written), so the judge can assess code quality? It significantly increases view size.
- **Does OAuth authentication work for the judge on a stock machine?** Unverified — the original dev machine authenticated via an injected `ANTHROPIC_AUTH_TOKEN`, so OAuth was never exercised by any probe.
- **Do judge scores vary run-to-run** the way output length does (§11.9)?

**Deferred to v2:**

- **Main-session scoring.** Main sessions are parsed and stored from Phase 1 (`session_kind = main`), but *scoring* them needs an adapted rubric (open-ended conversation, no single delegated task). No data-model change required, just a rubric variant.
- **Sanitized committed fixtures** to close the synthetic-vs-real drift gap in §8.
- **Split `n_spawns_with_errors`** into tool-errors and self-reported-partial as two metrics (§5).
- **Store: Parquet** for warehouse/S3 export.
