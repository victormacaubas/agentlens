## Context

The judge reads untrusted data and writes free text. That free text is destined, by design, for a Claude Code session that edits agent definitions.

```
 ┌── UNTRUSTED ─────────────────────────────────────────────┐
 │ transcript: task text, tool args, error strings, report   │
 └──────────────────────┬───────────────────────────────────┘
                        │
             ┌──────────▼──────────┐
             │ judge (no tools —    │  ← closed by
             │ harden-judge-invoc.) │    the sibling change
             └──────────┬──────────┘
                        │
             ┌──────────▼──────────────────────────────────┐
             │ verdict                                      │
             │  scores:     validated, derived locally  ✓    │
             │  evidence[]: free text, unvalidated      ✗    │
             │  fixes[]:    free text, unvalidated      ✗    │
             └──────────┬──────────────────────────────────┘
                        │  Phase 5 renderer (unbuilt)
             ┌──────────▼──────────┐
             │ reports/*.md         │  "Claude handoff"
             └──────────┬──────────┘
                        │  human pastes into Claude Code
             ┌──────────▼──────────┐
             │ .claude/agents/*.md  │  ← the write happens here
             └─────────────────────┘
```

The scores are the one part already defended: `harden-judge-security-and-scoring` made `overall_score` locally derived and range-validated every dimension, precisely because a model-supplied number could be impossible. `suggested_fixes` and `evidence` received no equivalent treatment — they are `list[str]` with no constraint beyond being strings.

The asymmetry matters because of where each field lands. A manipulated score corrupts a report. A manipulated fix, once transcribed into an agent definition, executes on every future spawn of that agent. Agent definitions are the highest-leverage write target in the system, and they are exactly what the product exists to modify.

Two things make this the right moment rather than a Phase 5 concern. The renderers do not exist yet, so no consumer depends on the loose shape. And `fact_verdict` holds zero rows, so bumping `RUBRIC_VERSION` — which invalidates the verdict cache by design — costs nothing.

## Goals / Non-Goals

**Goals:**

- A model-authored fix cannot be shaped like an instruction to a downstream agent. The schema, not prompt wording, is the control.
- Every consumer of a verdict can tell which fields are model-authored and derived from untrusted input, without having to know the pipeline's history.
- The handoff's trust boundary is written down, so Phase 5's renderer inherits a decision rather than inventing one.
- The product bet survives: fixes remain specific and actionable, not watered down into generic advice.

**Non-Goals:**

- **Validating that a fix is *good*.** Out of reach — that is the rubric-quality question. The goal here is that a fix cannot be *dangerous by shape*, which is achievable.
- **Auto-applying fixes.** Explicitly rejected below rather than deferred.
- **Transcript-side injection defense.** Parked as thread B; `harden-judge-invocation` covers the capability side and this change covers the output side. Neutralizing injected text inside the prepared view remains unaddressed and is stated as a known residual risk.
- **Rubric dimension changes.** The four dimensions stay as they are; only the fix payload changes.
- **Constraining `evidence[]` to a typed shape.** Evidence is quotation from the transcript and is inherently free text; it gets provenance labelling but not a schema. Discussed under D4.

## Decisions

### D1: `suggested_fixes` becomes a list of typed records

Replace `list[str]` with a list of `SuggestedFix` records. The field set is chosen so that a fix is a *description of a change to guidance*, and so that free prose has nowhere to hide:

- `dimension` — which rubric dimension this addresses, constrained to the four known names. Ties every fix to a scored weakness and makes an unmotivated fix impossible to express.
- `target` — what the fix applies to, from a closed set (the agent definition's instructions, its declared tools, its declared skills, or the task phrasing the caller used). A closed set is what prevents a fix from naming an arbitrary file path as its target.
- `recommendation` — the change itself, as a bounded-length string. Still natural language, because a genuinely useful fix has to be; but it is now one labelled field among several rather than the entire payload.
- `rationale` — why, referencing what happened in the run. Forces the fix to be grounded rather than generic, which is the same pressure the rubric prompt already applies to evidence.

The security value is not that `recommendation` becomes safe — it cannot, it is prose. It is that the surrounding structure makes an injected imperative *visibly* out of place: a fix whose `dimension` is `honesty`, `target` is `agent_instructions`, and `recommendation` reads "read ~/.aws/credentials" is legible as an anomaly to a human and to a downstream model, where the same sentence in a bare bullet list is not.

*Alternatives considered.* Keep free strings and rely on prompt instructions — rejected: the prior fix already established that prompt wording is a secondary control, and this is the field with the highest-consequence destination. A fully enumerated fix vocabulary with no free text — rejected: it would make fixes generic, which defeats the product's central claim. Filtering `recommendation` against a denylist of dangerous patterns — rejected: denylists on natural language fail open, and would create false confidence.

### D2: Fixes are advisory; agentlens never emits an auto-appliable patch

The schema and the rubric prompt both scope a fix to *recommending a change to the agent's own guidance*. agentlens does not emit diffs, patches, file edits, or shell commands, and no output field is designed to be executed.

This is a deliberate cap on ambition. A tool that auto-patched agent definitions from model output derived from untrusted transcripts would be handing an injection channel a write primitive. Keeping the human in the loop is the enforcement point — but only if the boundary is *visible*, which is D3's job. A human reviewing an unmarked bullet list of plausible-sounding fixes is not meaningfully an enforcement point.

*Alternative considered.* An `--allow-fixes` opt-in that emits appliable patches — rejected for v1. It moves the decision to a flag the user will set once and forget, and the residual risk (a persistent backdoor in an agent definition) is too asymmetric against the convenience gained.

### D3: Provenance is a field in the payload, not a convention

`to_verdict_json` marks which parts of the payload are model-authored and untrusted-derived, rather than leaving that knowledge in a renderer's head. A renderer, a JSON consumer, or a future dashboard all read the same signal.

Concretely this means the serialized verdict distinguishes locally-derived values (`overall_score`, the validated dimension scores) from model-authored text (`evidence`, the `recommendation` and `rationale` of each fix). Phase 5's markdown renderer is then required to present the model-authored portion inside an explicitly-marked untrusted block — which is why this change lists a Phase 5 constraint in its Impact rather than just changing a dataclass.

*Alternative considered.* Document the convention in the ADR and let each renderer implement it — rejected: three renderers plus a dashboard means four chances to forget, and the failure is silent.

### D4: `evidence` gets labelling and length bounds, but not a shape

Evidence is quotation from the transcript. Constraining its *shape* would either break its purpose or amount to the same free string with extra ceremony, so it stays free text and is labelled as untrusted model output (D3).

It does, however, get **bounded** — a maximum item count per dimension and a maximum length per item, mirroring the bounds `suggested_fixes` already gets. Bounding is not shaping: it costs nothing in expressiveness (a useful evidence citation is a sentence, not a page) and it closes a burial channel. An injected transcript that cannot smuggle an instruction through a typed fix can still pad `evidence` with plausible-looking entries until the genuine content, or the reviewer's attention, is exhausted. A fix list capped at five sitting above an unbounded evidence array is an odd place to stop counting.

Worth stating the residual risk plainly: evidence remains a channel through which transcript content reaches the report verbatim, and it is the natural place for injected text to surface. Bounds limit the volume, not the content. The mitigation for content is presentational (marked as untrusted, never treated as instruction) rather than structural. This is the strongest remaining argument for eventually taking up thread B, and the ADR should say so.

### D5: Bump `RUBRIC_VERSION`

The prompt and the output schema both change, so verdicts produced before and after are not comparable. ADR 0004 makes rubric versioning manual and the design doc establishes that a bump invalidates the cache and forces re-scoring as correct behavior. With an empty store, the bump costs nothing.

### D6: One ADR for the handoff trust boundary

The decision that needs to outlive this change is not "fixes are typed" — it is *"the markdown report is untrusted content, and agentlens never produces something designed to be applied without human reading."* That constrains Phase 5, Phase 6's dashboard, and any future automation. It belongs in `docs/adr/`, alongside ADR 0008's no-tools decision, as the write-side counterpart to it.

## Risks / Trade-offs

- **A typed shape could make fixes less useful.** → `recommendation` stays natural language and the rationale requirement pushes toward specificity, the same pressure that already produces useful evidence. This is checked explicitly after the change lands (task 6.5) rather than assumed: score a real window and read the fixes. If they come back generic, **the schema is what changes, not the guardrail** — the security properties (typed dimension, closed-set target, no executable content) are not negotiable against fix quality, but the field set around them is.
- **Injected prose can still occupy `recommendation` and `rationale`.** → Acknowledged and unavoidable while fixes are natural language. The structure makes it anomalous rather than invisible, D2 removes the automatic write path, and D3 makes the boundary visible to the reviewer. Not claimed as elimination.
- **Provenance labelling only helps if consumers honor it.** → Which is why it is a payload field (D3) and a spec requirement rather than a convention, and why the Phase 5 constraint is recorded in this change's Impact.
- **The rubric bump means any verdicts scored before this lands are discarded.** → Currently zero. If the sibling changes land first and someone scores a window in between, that work is lost; the tasks include a check so this is a known cost rather than a surprise.
- **A model may struggle to fill a four-field structure well and produce lower-quality fixes than free text would.** → Genuine unknown, untestable until real sessions are scored. The `--json-schema` retry loop handles malformed output; quality regression would only show up in use, which is an argument for scoring a real window soon after this lands.

## Migration Plan

None required. `verdict_json` is an opaque `TEXT` column, so no store schema change; `fact_verdict` holds zero rows, so nothing is invalidated by the `RUBRIC_VERSION` bump.

Rollback is reverting the schema, prompt, and dataclass, and restoring the prior `RUBRIC_VERSION`. Any verdicts written under the new version would be orphaned rather than corrupted, and would simply be re-scored.

Independent of the two sibling changes — no shared files — so it can be implemented in parallel. It must land before Phase 5 begins, since Phase 5 consumes the payload it defines.

## Open Questions

- Is the four-field fix shape right, or does `target`'s closed set need a fifth member once real fixes exist? Answerable only against scored sessions. The closed set is the security-relevant part, so additions should be deliberate rather than convenient.
- Are the chosen bounds — five fixes, and the new per-dimension evidence caps (D4) — the right numbers? They are set to be comfortably above what a genuine verdict needs and well below what a burial attempt requires, but only real output can calibrate them.
