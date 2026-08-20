## Purpose

Records which skills each subagent could use, was instructed to use, and
actually fired without turning a missing fire into a modeled judgment.

## ADDED Requirements

### Requirement: Session-skill grain is unique

The system SHALL store at most one row for each qualified subagent session and
skill name.

#### Scenario: One skill has several evidence sources
- **WHEN** a skill is declared, available, and fired more than once in one
  session
- **THEN** one session-skill row represents that skill with the three states
  resolved independently

### Requirement: Bridge membership is declaration or firing

The system SHALL create a session-skill row only for a skill the effective agent
definition declared or the transcript proves fired. Availability SHALL NOT make
a skill a member of the bridge, and the skills that shape a session's derivation
identity SHALL be limited to that same membership.

#### Scenario: Installed skill is neither declared nor fired
- **WHEN** a skill is present in the observed inventory but the spawn's
  effective definition did not declare it and no firing evidence appears
- **THEN** no session-skill row exists for that skill

#### Scenario: Unrelated skill is edited
- **WHEN** a skill that a spawn neither declared nor fired changes on disk
- **THEN** that spawn's derivation identity is unchanged

### Requirement: Declared, available, and fired are independent states

Each session-skill row SHALL expose independent states for whether the effective
agent definition declared the skill, whether the skill was available to the
run, and whether the transcript proves it fired. Declared and available SHALL
support `unknown`; fired SHALL be a boolean because it comes from transcript
evidence. `false` SHALL remain a storable availability state, but the system
SHALL NOT derive it from a skill's absence from the present-day inventory,
because current absence does not prove historical unavailability.

#### Scenario: Declared skill has unprovable availability and does not fire
- **WHEN** an effective agent definition declares a skill whose availability at
  the spawn start the observed inventory cannot prove, and no firing evidence
  appears
- **THEN** the row records `declared=true`, `available=unknown`, and
  `fired=false`

#### Scenario: Available undeclared skill fires
- **WHEN** a skill is available and firing evidence appears but the effective
  definition did not declare it
- **THEN** the row records `declared=false`, `available=true`, and `fired=true`

#### Scenario: Historical definition cannot be proven
- **WHEN** the currently observed matching agent definition is newer than the
  spawn
- **THEN** declaration state is `unknown` for skills whose historical
  declaration cannot be established

#### Scenario: Historical availability cannot be proven
- **WHEN** current skill files and transcript evidence cannot establish whether
  a skill was available when the spawn started
- **THEN** availability state is `unknown` rather than `false`

### Requirement: Fired state requires execution evidence

The system SHALL mark a skill as fired only from a `Skill` tool invocation or a
recognized injected-skill marker in the transcript.

#### Scenario: Skill tool invocation appears
- **WHEN** a transcript contains a `Skill` tool invocation naming a skill
- **THEN** that session-skill row records `fired=true`

#### Scenario: Skill file is only read
- **WHEN** a transcript reads a `SKILL.md` file but contains no `Skill`
  invocation or injected-skill marker
- **THEN** the read alone does not set `fired=true`

### Requirement: Fired-skill count is reproducible

Each subagent session SHALL report the number of distinct session-skill rows
whose fired state is true.

#### Scenario: One skill fires more than once
- **WHEN** the same skill fires several times in one subagent session
- **THEN** the session's fired-skill count increases by one for that skill

### Requirement: Missing fires remain deterministic facts

The deterministic layer SHALL NOT label an available or declared skill's
failure to fire as a mistake, score, or verdict.

#### Scenario: Declared skill does not fire
- **WHEN** a declared skill has `fired=false`
- **THEN** reports expose the three observed states without claiming the agent
  should have fired the skill

### Requirement: Current files do not rewrite unknown history as fact

The system SHALL use observed file modification times and transcript evidence
to distinguish known historical declaration or availability from current-only
state.

#### Scenario: Skill is installed after the spawn
- **WHEN** a currently available skill was last modified after the spawn start
  and the transcript has no evidence that it was available
- **THEN** the session-skill availability state is `unknown`, not `true`
