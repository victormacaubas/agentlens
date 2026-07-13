## MODIFIED Requirements

### Requirement: Emit deterministic verdict-JSON slice

The system SHALL emit the deterministic slice of the verdict JSON — per-session counts and window rollups — and SHALL include verdict scores (from `fact_verdict`) when they exist for sessions in the window. Verdict inclusion SHALL be opportunistic: sessions without verdicts still appear with deterministic data only.

#### Scenario: Deterministic numbers without scores

- **WHEN** `report --since 7d` runs against a populated store with no verdicts
- **THEN** it emits per-session counts and window rollups with no LLM score fields

#### Scenario: Verdicts included when present

- **WHEN** `report --since 7d` runs and some sessions have verdicts in the store
- **THEN** those sessions include their verdict scores and suggested_fixes in the output

#### Scenario: Mixed scored and unscored

- **WHEN** a window contains 10 sessions, 6 scored and 4 unscored
- **THEN** all 10 appear in the report; the 6 scored ones include verdict data, the 4 unscored show deterministic data only

#### Scenario: JSON output includes verdicts

- **WHEN** `report --since 7d --json` runs with scored sessions
- **THEN** the JSON output includes a `verdicts` key per session containing dimension scores and suggested_fixes
