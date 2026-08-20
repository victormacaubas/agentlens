## Purpose

Versions the agent definitions that shape subagent behavior and binds each
spawn to the effective definition that applied in its project.

## ADDED Requirements

### Requirement: Agent definitions are versioned by content and scope

The system SHALL catalog observed user-scoped and project-scoped agent
definitions with a stable identity that changes when the definition content
changes.

#### Scenario: Definition is unchanged across report runs
- **WHEN** the same scoped agent definition is scanned twice without a content
  change
- **THEN** both scans resolve to the same agent-definition identity and do not
  create duplicate versions

#### Scenario: Definition content changes
- **WHEN** a scanned agent definition differs from the version already stored
  for the same name and scope
- **THEN** a new content identity is cataloged and spawn bindings are
  re-evaluated against its observed modification time

### Requirement: Binding requires historical applicability

The system SHALL bind a spawn to a currently observed definition only when the
definition's observed modification time is no later than the spawn start time.

#### Scenario: Current definition predates the spawn
- **WHEN** the effective scoped definition was last modified before the spawn
  started
- **THEN** the spawn binds to that definition identity

#### Scenario: Current definition is newer than the spawn
- **WHEN** the only currently observed matching definition was modified after
  the spawn started
- **THEN** the spawn records its historical definition identity as unknown
  rather than binding to the newer content

### Requirement: Project scope overrides user scope inside that project

The system SHALL resolve the effective definition for a spawn by preferring a
historically applicable project-scoped definition over a historically
applicable user-scoped definition of the same agent type.

#### Scenario: Both scopes define the same agent
- **WHEN** a project and the user scope both define the spawned agent type
- **THEN** the spawn binds to the project-scoped definition

#### Scenario: Only user scope defines the agent
- **WHEN** no matching project-scoped definition exists but a user-scoped one
  does
- **THEN** the spawn binds to the user-scoped definition

### Requirement: Cataloged definitions expose deterministic configuration

Each cataloged definition SHALL retain its agent name, scope, source project
where applicable, model, effort, declared tools, and declared skills.

#### Scenario: Definition metadata is read
- **WHEN** a cataloged definition is queried
- **THEN** its deterministic configuration and declared tool and skill sets are
  available without re-reading the source file

### Requirement: An unknown definition does not drop a spawn

A subagent spawn SHALL remain ingestible when no matching agent definition can
be resolved with historical confidence.

#### Scenario: Spawn has no historically applicable definition
- **WHEN** a discovered spawn has no matching definition or all matching
  observed definitions are newer than the spawn
- **THEN** the spawn is persisted with no effective agent-definition identity
  and remains visible in deterministic reports
