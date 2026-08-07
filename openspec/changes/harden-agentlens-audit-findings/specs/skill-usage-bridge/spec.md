## MODIFIED Requirements

### Requirement: Populate bridge_session_skill

The system SHALL populate `bridge_session_skill` with one row per qualified
`(session_id, skill_name)`, recording `declared`, `available`, and `fired`. The row set
SHALL be the union of the effective definition version's declared skills and the
session's fired skills. The effective definition SHALL be the project-scoped definition
for the session's source project when present, otherwise the user-scoped definition.
Historical bridge rows SHALL remain attributable to the definition version selected when
the session was ingested.

#### Scenario: Declared and fired skills both produce rows

- **WHEN** a session's effective definition declares skill A and the session fires undeclared skill B
- **THEN** the bridge contains A as declared and B as fired

#### Scenario: Declared flag from agent definition

- **WHEN** a skill appears in the effective definition version's declared skills
- **THEN** that session's bridge row records `declared = 1`

#### Scenario: Project declaration wins only in its project

- **WHEN** two projects define the same agent type with different declared skills
- **THEN** each project's session receives bridge rows from its own effective definition

#### Scenario: User definition supplies the fallback

- **WHEN** a source project has no project-scoped definition for the session's agent type
- **THEN** declared skills come from the matching user-scoped definition

#### Scenario: Definition update preserves historical attribution

- **WHEN** an agent definition changes after an earlier session was ingested
- **THEN** the earlier session remains linked to its original definition identity and a new session links to the new version

#### Scenario: Available flag is best-effort

- **WHEN** the skills tree cannot be resolved
- **THEN** `available` defaults to 0 and bridge rows are still written
