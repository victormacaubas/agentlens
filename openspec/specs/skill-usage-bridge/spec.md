# Skill Usage Bridge

## Purpose

Populates `bridge_session_skill` — one row per `(session_id, skill_name)` — recording whether a skill was declared in the agent definition, available on disk, and actually fired during the session. Bridges the declared-skills dimension with observed skill-fire signals from the parser.

## Requirements

### Requirement: Populate bridge_session_skill

The system SHALL populate `bridge_session_skill` with one row per `(session_id, skill_name)`, recording `declared`, `available`, and `fired`. The row set SHALL be the union of a session's declared skills and its fired skills, so a skill that fires without being declared still produces a row.

#### Scenario: Declared and fired skills both produce rows

- **WHEN** a session's agent declares skill A and the session fires skill B (not declared)
- **THEN** `bridge_session_skill` has a row for A (declared, not fired) and a row for B (fired, not declared)

#### Scenario: Declared flag from agent definition

- **WHEN** a skill name appears in the session's agent's `dim_agent.declared_skills`
- **THEN** that skill's row has `declared = 1`

#### Scenario: Available flag is best-effort

- **WHEN** the `.claude/skills/**` tree cannot be resolved for a skill
- **THEN** `available` defaults to 0 and the row is still written

### Requirement: fired is the union of injection marker and Skill tool_use

The system SHALL set `fired = 1` for a skill when EITHER an `isMeta:true` record's text contains `<skill-format>true` together with a `<command-name>` naming that skill, OR a `Skill` tool_use names that skill in its input. `SKILL.md` reads SHALL NOT set `fired`.

#### Scenario: Auto-injected skill detected via marker

- **WHEN** an `isMeta:true` record carries `<skill-format>true` and `<command-name>python-engineering-standards</command-name>`
- **THEN** the `python-engineering-standards` row has `fired = 1`

#### Scenario: Explicitly invoked skill detected via tool_use

- **WHEN** a `Skill` tool_use names `openspec-sync-specs` in its input
- **THEN** the `openspec-sync-specs` row has `fired = 1`

#### Scenario: SKILL.md read does not set fired

- **WHEN** a session only reads a `SKILL.md` file for a skill and never injects or invokes it
- **THEN** that skill's `fired` remains 0
