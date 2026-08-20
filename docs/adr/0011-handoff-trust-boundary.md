# ADR 0011: The handoff report is untrusted content

## Status

Accepted

## Context

The judge reads an attacker-influenceable transcript (task text, tool args, error strings, the agent's own final report) and writes free text. That free text is destined, by design, for a human who pastes it into a Claude Code session to patch an agent definition. The path is:

```
transcript (untrusted) → judge → verdict → reports/*.md → human → .claude/agents/*.md
```

agentlens's own guardrail is "read-only against `.claude/`", and its code never violates it: nothing in this repository writes into that directory. But the intended workflow routes around the guardrail by one hop. A `suggested_fix` that reads like an instruction rather than a recommendation is a plausible-looking string that can pass every check in the pipeline, get pasted into a report, and get transcribed into an agent definition by a human who trusted the tool that produced it. Agent definitions are the highest-leverage write target in the system: a fix that becomes part of one runs on every future spawn of that agent, not once.

ADR 0008 closed the read side of this problem: the judge has no tools, so a manipulated transcript cannot make it exfiltrate data or take an action during scoring. It said nothing about what happens to the judge's *output* once scoring finishes. Before this decision, `overall_score` and each dimension's `score` were locally derived and range-validated (`harden-judge-security-and-scoring`), because a model-supplied number could be impossible to trust otherwise. `suggested_fixes` and `evidence` received no equivalent treatment: both were `list[str]`, indistinguishable from any other string in a rendered report. Typing the fix shape (four fields, a closed set for `target`) makes an injected imperative structurally awkward, but a `recommendation` and a `rationale` are still natural-language prose. No schema makes prose safe to execute; a schema only makes it visible when it's out of place.

## Decision

The markdown report is untrusted content, and agentlens never produces something designed to be applied without a human reading it. This is the decision that has to outlive the specific schema of any one field.

Concretely:

- agentlens emits no diffs, patches, file edits, or shell commands. No output field is designed to be executed, copied verbatim into a file, or run.
- The serialized verdict carries provenance as a payload field, not a rendering convention. Scores are marked locally derived and validated; `evidence`, and each fix's `recommendation` and `rationale`, are marked as untrusted model output. Every consumer, present or future, reads the same signal instead of reimplementing the judgment in its own renderer.
- Any surface that renders a verdict for a human or another tool presents model-authored fields as content to review, inside an explicitly marked untrusted block, never as an instruction to follow.
- `evidence` stays a verbatim channel from the transcript into the report. It is quotation, not a claim, and constraining its shape would either break its purpose or produce the same free string with extra ceremony. It is bounded in item count and length so it cannot become an unbounded burial channel, but bounding volume is not the same as neutralizing content; that residual is stated plainly in Consequences.
- Considered and rejected: an `--allow-fixes` opt-in that emits appliable patches directly. It moves the trust decision to a flag a user sets once and forgets, and the risk on the other side of that flag, a persistent backdoor written into an agent definition, is too asymmetric against the convenience of skipping a copy-paste.

The human is the enforcement point, but only because the boundary is made visible to them. A human skimming an unmarked bullet list of plausible-sounding fixes is not meaningfully a security control; a human reading the same list inside a block labeled "unvalidated model output, derived from an untrusted transcript" has something to actually decide against.

## Consequences

- Phase 5's renderers (markdown, JSON, HTML) and Phase 6's dashboard must present fixes and evidence inside an explicitly marked untrusted block, and must not emit anything shaped like a patch, diff, or command for direct application. Any future automation reading `fact_verdict` inherits the same constraint: it consumes provenance-labeled data, not instructions.
- Typed fixes make an injected imperative anomalous rather than invisible, but `recommendation` and `rationale` remain prose. Provenance labelling is a presentational control, not a structural one; nothing here claims injected text cannot occupy those fields, only that it will be legible as untrusted when it does.
- `evidence` is the strongest remaining argument for eventually taking up transcript-side injection defense: neutralizing content inside the prepared view before the judge ever sees it. That work is out of scope here and remains a known residual risk, not a gap this ADR closes.
- A rubric or renderer change that stops labelling provenance, or that starts emitting an artifact meant to be applied without review, is not a routine schema tweak. It reopens this decision and needs the same scrutiny this ADR received.
