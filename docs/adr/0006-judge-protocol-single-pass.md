# 6. Judge Protocol uses a single score() method producing all dimensions in one call

Status: Accepted

## Context

The judge produces scores across four rubric dimensions plus fix suggestions. Two interface designs were considered:

- **One method per concern** — separate `score()` and `suggest_fixes()` methods, or one method per dimension.
- **Single `score()` method** — all four dimension scores and fix suggestions are produced in one judge call, returning a `Verdict`.

The transcript view is ~3K tokens; the rubric prompt ~1-2K. Structured JSON output with four dimensions is well within the `--json-schema` capability of current models. Splitting into four calls would multiply cost and latency by 4x for no quality benefit in the common case. Fix suggestions are grounded in scoring evidence — producing both in the same context window reduces hallucination risk.

## Decision

**The `Judge` Protocol exposes a single `score(transcript_view, rubric_version) -> Verdict` method.** All four rubric dimensions and fix suggestions are produced in one call. The `Verdict` dataclass holds `DimensionScore` entries for each dimension plus a `suggested_fixes` list.

Future judge backends must implement this single-method Protocol. Splitting into multiple methods requires a new ADR.

## Consequences

- **One call per session** — cost and latency are predictable. At ~3K input + ~1K output: ~$0.02/session with sonnet, ~$0.12/session with opus.
- **Fix quality is bounded by single-pass context.** If fix suggestions are too generic, a multi-pass approach (score first, suggest fixes against scored evidence) could improve quality. This is deferred to a future rubric iteration ADR.
- **The Protocol is the stability boundary.** `ClaudeCliJudge` (subprocess backend) implements it today. A future `AnthropicApiJudge` (CI backend) must also implement it. Callers in `ScoringLoop` depend only on the Protocol, not the concrete class.
