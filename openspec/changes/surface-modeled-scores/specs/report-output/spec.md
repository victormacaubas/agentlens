## MODIFIED Requirements

### Requirement: Every qualifying subagent spawn has a typed row

The report SHALL contain one row for every qualifying current-window subagent
spawn, including spawns with no effective definition, no skill evidence, or no
verdict. Each row's measured fields SHALL remain populated independently of
whether that spawn carries modeled fields.

#### Scenario: Spawn has incomplete optional context
- **WHEN** a qualifying subagent has no effective definition and no
  session-skill rows
- **THEN** its spawn row remains present with required measured fields and
  explicit empty optional context

#### Scenario: Spawn has no verdict in the named cohort
- **WHEN** a qualifying subagent holds no verdict the report can present
- **THEN** its spawn row remains present with its measured fields intact and its
  modeled fields explicitly absent

## ADDED Requirements

### Requirement: Measured and modeled fields occupy separate structures

The document SHALL keep a spawn's measured fields and its modeled fields in
separate structures rather than flattening them together, and SHALL keep the
deterministic agent rollup separate from the modeled agent rollup. A consumer
SHALL be able to read every measured value without interpreting any modeled one.

#### Scenario: Consumer reads only deterministic figures
- **WHEN** a consumer reads a report document that contains modeled scores
- **THEN** it can extract every measured spawn field and deterministic rollup
  without traversing modeled structures, and no measured figure has been
  recomputed from a verdict

#### Scenario: Report presents both rollup families for one agent type
- **WHEN** an agent type has both a deterministic rollup and a modeled rollup
- **THEN** the two appear as distinct structures with their own populations and
  their own trend statuses

### Requirement: The document version advances with the modeled surface

The document's schema version SHALL advance when the modeled surface is
introduced, so a consumer pinned to the deterministic-only shape detects the
change rather than silently reading a document it does not understand. Fields
present before the advance SHALL keep their meaning.

#### Scenario: Consumer compares document versions
- **WHEN** a consumer reads a report document produced after the modeled surface
  is introduced
- **THEN** its declared schema version differs from the deterministic-only
  version, and every field the earlier version defined carries the same meaning

## REMOVED Requirements

### Requirement: Output is deterministic-only

**Reason**: Superseded by this change. The requirement was correct while no
scoring path existed; now that a window's spawns can be scored, a report that
omits every modeled field hides the product. The invariant worth keeping from it
is not omission but separation, which is carried by "Measured and modeled fields
occupy separate structures" and by the `report-modeled-scores` capability's rule
that a report speaks for exactly one cohort.

**Migration**: A consumer that relied on modeled fields being absent should read
the document's schema version, which advances with this change, and read measured
fields from the structures that hold them rather than assuming the document
contains nothing else. No measured field is removed, renamed, or recomputed.
