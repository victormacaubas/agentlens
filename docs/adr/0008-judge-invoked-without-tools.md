# ADR 0008: Judge invoked without tools

## Status

Accepted

## Context

The Claude CLI judge backend (`ClaudeCliJudge`) scores subagent transcripts by invoking `claude -p` as a subprocess. The prepared transcript view — which contains attacker-influenceable content (task text, tool metadata, error excerpts, final report) — is passed as the user prompt via stdin.

Prior to this decision, the judge was invoked with `--permission-mode dontAsk --allowedTools "Read,Grep"`, granting it filesystem access. A code audit (SEC-01) demonstrated that a malicious transcript could instruct the judge to read arbitrary files visible to the user's Claude process, exfiltrating credentials or sensitive data through the model's evidence/fix fields.

The judge is a pure grading call: it scores a prepared view and returns structured JSON. It has no legitimate need to inspect files, run shell commands, or perform any action beyond text generation.

## Decision

The Claude CLI judge is invoked with no tools granted — the `--permission-mode` and `--allowedTools` flags are omitted entirely from the argument list. As defense-in-depth, the rubric prompt explicitly marks the transcript as untrusted data and instructs the model to never follow embedded directives.

The primary security control is the absence of capabilities (no tools to aim at); the prompt-level instruction is secondary and cannot be relied upon alone.

## Consequences

- Prompt injection in transcripts can no longer cause file reads, shell execution, or any side effect beyond the model's text output.
- The judge's output quality is unchanged: it was never supposed to browse files (the prepared view already contains all the signal it needs), so removing tools aligns behavior with intent.
- Any future judge feature that needs filesystem access (e.g., reading agent definitions for context) must provide the data in the prepared view rather than granting tools at invocation time.
- The `judge-interface` OpenSpec spec no longer mandates `Read,Grep`; any implementation of the `Judge` Protocol must operate without filesystem access.
