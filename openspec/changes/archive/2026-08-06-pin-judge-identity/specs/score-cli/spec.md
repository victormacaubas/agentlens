## MODIFIED Requirements

### Requirement: Score subcommand

The system SHALL provide an `agentlens score` subcommand that finds unscored sessions in a window, scores them via the judge, and persists verdicts. It SHALL accept `--since`, `--from/--to`, `--agent`, `--judge-model` (default: `sonnet`), `--max-sessions`, `--no-confirm`, and `--dry-run` flags.

`--judge-model` SHALL accept either a model alias or a fully pinned model identifier. An alias is an input convenience only: verdicts SHALL be keyed by the concrete model identifier the judge backend resolves, never by the alias as typed.

#### Scenario: Score a window

- **WHEN** a user runs `agentlens score --since 7d`
- **THEN** unscored sessions in the last 7 days are scored and verdicts are persisted

#### Scenario: Agent filter

- **WHEN** a user runs `agentlens score --since 30d --agent implementer`
- **THEN** only unscored `implementer` sessions in the window are scored

#### Scenario: Model override

- **WHEN** a user runs `agentlens score --since 7d --judge-model opus`
- **THEN** the judge uses the `opus` alias and verdicts are keyed with the concrete model identifier it resolved to (e.g. `claude-opus-5`), not with the string `opus`

### Requirement: Cost confirmation gate

The system SHALL display an estimated cost and session count before scoring and wait for user confirmation. The confirmation SHALL be skippable with `--no-confirm`.

Per-session cost estimates SHALL be derived from measured costs of the judge's actual minimal-mode invocation against a realistically-sized transcript view, rather than from pre-implementation placeholder values. Estimates SHALL be rounded **above** the highest measured per-session cost, so that the displayed figure is an upper bound rather than a central guess — a gate that understates spends money the user did not approve, while one that overstates only under-promises. The authoritative figure is the post-run total taken from the judge envelope.

Where the number of sessions to be scored cannot yet be known exactly — on a first run with a given model alias, before any call has resolved it to a concrete identifier — the confirmation SHALL present the session count as an upper bound rather than as an exact figure.

#### Scenario: User confirms

- **WHEN** the loop finds 20 unscored sessions with model `sonnet`
- **THEN** the CLI prints a confirmation naming the session count, the model, and an estimated cost derived from measured per-session costs, and waits for input

#### Scenario: Estimate is not exceeded by the actual cost

- **WHEN** a window of sessions is scored after the user accepted the estimate
- **THEN** the reported total cost does not exceed the estimate that was displayed

#### Scenario: First run with an unresolved alias shows an upper bound

- **WHEN** a user runs `agentlens score` with a model alias that no existing verdict was keyed under, so the unscored set cannot yet be determined exactly
- **THEN** the confirmation presents the session count as an upper bound rather than an exact figure

#### Scenario: User declines

- **WHEN** the user responds "n" to the confirmation prompt
- **THEN** the CLI exits with status 0 and scores nothing

#### Scenario: No-confirm skips prompt

- **WHEN** `--no-confirm` is passed
- **THEN** the CLI proceeds without asking for confirmation

### Requirement: Final summary

The system SHALL print a final summary after the loop completes, reporting sessions scored, total judge cost, and sessions skipped. When the configured `--judge-model` was an alias, the summary SHALL also name the concrete model identifier the verdicts were keyed under, so that a re-score triggered by alias movement is self-explanatory.

#### Scenario: All scored

- **WHEN** 10 of 10 sessions score successfully
- **THEN** the CLI prints a summary naming 10 of 10 scored and the total judge cost

#### Scenario: Partial with skips

- **WHEN** 8 of 10 succeed and 2 are skipped
- **THEN** the CLI prints a summary naming 8 of 10 scored, the total judge cost, and that 2 were skipped with a suggestion to re-run

#### Scenario: Resolved model named when an alias was used

- **WHEN** the user passed `--judge-model sonnet` and the judge resolved it to a concrete identifier
- **THEN** the summary names that concrete identifier alongside the alias
