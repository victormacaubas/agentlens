# Score CLI

## Purpose

Provides the `agentlens score` subcommand that finds unscored sessions in a window, calls the LLM judge, persists verdicts, and reports progress and cost to the user.

## Requirements

### Requirement: Score subcommand

The system SHALL provide an `agentlens score` subcommand that finds unscored sessions in a window, scores them via the judge, and persists verdicts. It SHALL accept `--since`, `--from/--to`, `--agent`, `--judge-model` (default: `sonnet`), `--max-sessions`, `--no-confirm`, and `--dry-run` flags.

#### Scenario: Score a window

- **WHEN** a user runs `agentlens score --since 7d`
- **THEN** unscored sessions in the last 7 days are scored and verdicts are persisted

#### Scenario: Agent filter

- **WHEN** a user runs `agentlens score --since 30d --agent implementer`
- **THEN** only unscored `implementer` sessions in the window are scored

#### Scenario: Model override

- **WHEN** a user runs `agentlens score --since 7d --judge-model opus`
- **THEN** the judge uses `opus` and verdicts are keyed with `judge_model = "opus"`

### Requirement: Cost confirmation gate

The system SHALL display an estimated cost and session count before scoring and wait for user confirmation. The confirmation SHALL be skippable with `--no-confirm`.

#### Scenario: User confirms

- **WHEN** the loop finds 20 unscored sessions with model `sonnet`
- **THEN** the CLI prints "Will score 20 sessions with sonnet (est. ~$0.40). Proceed? [Y/n]" and waits for input

#### Scenario: User declines

- **WHEN** the user responds "n" to the confirmation prompt
- **THEN** the CLI exits with status 0 and scores nothing

#### Scenario: No-confirm skips prompt

- **WHEN** `--no-confirm` is passed
- **THEN** the CLI proceeds without asking for confirmation

### Requirement: Max sessions cap

The system SHALL accept `--max-sessions N` to cap the number of sessions scored in one invocation. When the cap is reached, it SHALL report the cap and suggest re-running.

#### Scenario: Cap reached

- **WHEN** 50 sessions are unscored and `--max-sessions 20` is passed
- **THEN** exactly 20 sessions are scored and the CLI reports "20/50 scored (--max-sessions reached). Re-run to continue."

### Requirement: Dry-run mode

The system SHALL accept `--dry-run` to show what would be scored without calling the judge.

#### Scenario: Dry-run output

- **WHEN** `--dry-run` is passed with 12 unscored sessions
- **THEN** the CLI lists the 12 sessions (agent_type, task_description) and estimated cost, then exits without calling the judge

### Requirement: Progress output

The system SHALL print per-session progress to stderr during scoring, including the session index, agent type, task description excerpt, score, and per-session cost.

#### Scenario: Progress during scoring

- **WHEN** 10 sessions are being scored
- **THEN** stderr shows lines like `[3/10] implementer "Fix 4 findings" ... scored (4.2/5, $0.019)`

#### Scenario: Skipped session in progress

- **WHEN** a session fails and is skipped
- **THEN** stderr shows `[5/10] implementer "Implement X" ... ERROR (timeout), skipped`

### Requirement: Final summary

The system SHALL print a final summary after the loop completes, reporting sessions scored, total judge cost, and sessions skipped.

#### Scenario: All scored

- **WHEN** 10 of 10 sessions score successfully
- **THEN** the CLI prints "Scored 10/10 sessions. Total judge cost: $0.21."

#### Scenario: Partial with skips

- **WHEN** 8 of 10 succeed and 2 are skipped
- **THEN** the CLI prints "Scored 8/10 sessions. Total judge cost: $0.17. 2 skipped (re-run to retry)."
