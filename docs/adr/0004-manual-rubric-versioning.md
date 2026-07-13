# 4. Rubric version is a manual semver string, not an auto-hash

Status: Accepted

## Context

The LLM judge scores sessions against a rubric — a prompt template that defines dimensions, criteria, and output format. Verdicts are cached in `fact_verdict` keyed by `(session_id, rubric_version, judge_model)`. When the rubric changes, old verdicts may no longer be valid and sessions should be re-scored.

Two versioning strategies were considered:

- **Auto-hash of the prompt template.** `rubric_version = sha256(RUBRIC_PROMPT_TEMPLATE)[:12]`. Any change to the prompt — including typos, whitespace, wording tweaks — automatically invalidates all cached verdicts and forces a full re-score on the next run.
- **Manual semver string.** `RUBRIC_VERSION = "v1"`. Bumped explicitly by the developer when the rubric's scoring semantics change intentionally.

During rubric development (Phase 3b), the prompt is iterated frequently — dozens of tweaks to wording, evidence format, and criteria phrasing while calibrating against real sessions. Auto-hashing would re-score the entire window on every tweak, costing dollars and minutes per iteration cycle.

## Decision

**Rubric version is a manual semver string (`"v1"`, `"v2"`, ...) stored as `RUBRIC_VERSION` in `src/agentlens/judge/rubric.py`. It must be bumped explicitly when scoring semantics change.**

A cosmetic prompt edit (reworded instruction, formatting fix) that does not change what scores a session receives does not require a version bump. A semantic change (new dimension, altered scale, changed criteria, different evidence format) does.

## Consequences

- **Cached verdicts remain valid across cosmetic edits.** Re-running after a wording cleanup costs nothing — the cache key hasn't changed, so all verdicts are hits.
- **A semantic change without a version bump silently serves stale verdicts.** This is the failure mode. There is no automated guard; it relies on the developer remembering to bump. The risk is acceptable because (a) the rubric changes rarely after stabilization, (b) the store is a disposable cache — `rm ~/.cache/agentlens/agentlens.db` + re-ingest + re-score is always available as a hard reset, and (c) different rubric versions coexist in the store (the PK includes `rubric_version`), so you can compare scoring before and after a bump.
- **`rubric_version` is human-readable in the store and reports.** `"v1"` is immediately meaningful; `"a3f8c2b1e9d4"` is not.
- **Multiple rubric versions coexist.** Scoring with `v1` and then bumping to `v2` does not delete `v1` rows — both are queryable, enabling before/after comparison during calibration.
