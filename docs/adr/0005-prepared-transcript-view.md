# 5. Judge receives a prepared transcript view, not raw JSONL

Status: Accepted

## Context

The LLM judge scores agent sessions from the transcript. Two approaches were considered:

- **Pass raw JSONL** — give the judge the `.jsonl` file path and let it read records directly.
- **Build a prepared view** — extract a structured ~10-12KB text document from the parsed transcript before calling the judge.

Real subagent transcripts in the corpus range from 68KB to 875KB (median ~253KB) and contain 10-215 records. Raw JSONL is roughly 60% JSON framing: field names, nesting, and encoding for every record. It also includes full file contents from `Read` tool results, which are irrelevant to scoring task focus or efficiency. The largest transcripts (~875KB ≈ 220K tokens) would consume the entire context window of any current model.

## Decision

**The judge always receives a prepared transcript view, not raw JSONL.** `build_transcript_view(parsed, jsonl_path)` in `src/agentlens/judge/transcript_view.py` produces the view. It is the only artifact passed to any `Judge` implementation.

The view structure is fixed: task description, agent identity, deterministic facts,
condensed tool sequence (with tool-specific summarization rules), error/denial excerpts,
and final report. Future judge backends (API, CI) must consume this same view format.

The builder streams transcript records and retains bounded pending tool pairs and
deterministic head/tail samples. Every section has an explicit byte budget. When errors or
denials exceed their budget, the view preserves their total count and stable sampled step
references rather than every full excerpt. A final UTF-8 byte gate enforces the documented
20KB hard limit while retaining all six section headers.

## Consequences

- **Context window and process memory stay bounded.** The completed view never exceeds
  20KB, and view construction does not retain decoded copies of the complete transcript or
  large tool-result payloads.
- **The view module is the extension point.** If future rubric iteration needs more detail (e.g., Write input content for code quality assessment), `transcript_view.py` is the only file to change — judge backends are unaffected.
- **Lossy by design.** The view discards full file contents and thinking blocks. A judge cannot assess code written by the agent unless Write inputs are added to the view. This is a known trade-off accepted in v1.
- **High-volume sections are sampled.** Tool history and errors retain counts and stable
  references, but not every detail when the hard limit would be exceeded. Preserving all
  error text and enforcing a fixed total size are mathematically incompatible.
- **New judge backends must not bypass the view.** Any `Judge` implementation that reads raw JSONL directly would break the memory/cost guarantees that motivated this decision.
