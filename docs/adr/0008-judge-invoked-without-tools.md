# ADR 0008: Judge invoked without tools

## Status

Accepted

## Context

The Claude CLI judge backend (`ClaudeCliJudge`) scores subagent transcripts by invoking `claude -p` as a subprocess. The prepared transcript view — which contains attacker-influenceable content (task text, tool metadata, error excerpts, final report) — is passed as the user prompt via stdin.

Prior to this decision, the judge was invoked with `--permission-mode dontAsk --allowedTools "Read,Grep"`, granting it filesystem access. A code audit (SEC-01) demonstrated that a malicious transcript could instruct the judge to read arbitrary files visible to the user's Claude process, exfiltrating credentials or sensitive data through the model's evidence/fix fields.

The judge is a pure grading call: it scores a prepared view and returns structured JSON. It has no legitimate need to inspect files, run shell commands, or perform any action beyond text generation.

**Revision (harden-judge-invocation).** The first fix removed `--permission-mode dontAsk` and `--allowedTools "Read,Grep"` from the argument list and added a unit test asserting those strings were absent from `_build_args()`. That test passed, but the fix was not equivalent to removing the judge's tools: omitting `--allowedTools` does not deny tools, it selects the CLI's default, which grants the full built-in tool set. A follow-up probe against the installed CLI, using exactly the flags `_build_args()` produced, confirmed the judge could still read an arbitrary canary file and return its contents with `permission_denials: []`. The decision recorded below (no tools for the judge) was correct throughout; the mechanism believed to enforce it did not.

## Decision

The Claude CLI judge is invoked with `--tools ""`, the installed CLI's documented switch to disable the entire built-in tool set (`claude --help`: `--tools <tools...>` "Specify the list of available tools from the built-in set. Use \"\" to disable all tools, \"default\" to use all tools"). This removes the tools themselves, rather than merely declining to grant a subset of them: `--allowedTools ""` (or omitting `--allowedTools` altogether) still leaves the full built-in set loaded and expresses only a permission decision over it, which is what let the prior fix silently fail open. `--tools ""` was verified empirically against a canary file read that the omission-based argument list did not block.

As defense-in-depth, the rubric prompt explicitly marks the transcript as untrusted data and instructs the model to never follow embedded directives.

The primary security control is the absence of capabilities (no tools to aim at); the prompt-level instruction is secondary and cannot be relied upon alone.

## Consequences

- Prompt injection in transcripts can no longer cause file reads, shell execution, or any side effect beyond the model's text output.
- The judge's output quality is unchanged: it was never supposed to browse files (the prepared view already contains all the signal it needs), so removing tools aligns behavior with intent.
- Any future judge feature that needs filesystem access (e.g., reading agent definitions for context) must provide the data in the prepared view rather than granting tools at invocation time.
- The `judge-interface` OpenSpec spec no longer mandates `Read,Grep`; any implementation of the `Judge` Protocol must operate without filesystem access.
- **Asserting that a flag string is absent from an argument list does not assert that a capability is absent.** `test_build_args_grants_no_filesystem_tools` asserted `"--allowedTools" not in args` and equivalent negatives, and that assertion passed against an argument list that still granted the judge the full built-in tool set: the omission selected the CLI's default rather than denying anything. The lesson generalizes past this bug: a security property enforced by a subprocess's argument list must be verified by exercising the property itself (here, a canary file whose contents must never reach the verdict), not by pattern-matching the arguments that produce it. String-level assertions on the argument list remain useful as a fast, cheap complement, but they are not the guarantee.
