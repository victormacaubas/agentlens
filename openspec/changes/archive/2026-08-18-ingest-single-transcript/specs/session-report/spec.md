## Purpose

What a caller gets back after one agent run is analyzed: a machine-readable
artifact that downstream tooling can depend on, and a human summary, kept on
separate streams so both are usable at once.

## ADDED Requirements

### Requirement: A machine-readable artifact is always produced

Analyzing a session SHALL produce a JSON document describing that session, written
to a stable path derived from the session key.

#### Scenario: Artifact written to a stable path

- **WHEN** a session is analyzed without an explicit output format
- **THEN** a JSON file is written under a `reports/` directory at a path derived
  from the session key, and the printed summary names that path

#### Scenario: Artifact to standard output

- **WHEN** the caller requests JSON output
- **THEN** the JSON document is written to standard output and nothing else is
  written to standard output

### Requirement: The artifact is self-describing and versioned

The JSON document SHALL carry a schema version and the time it was generated, so a
consumer can tell what it is holding without inferring from field presence.

#### Scenario: Version and timestamp present

- **WHEN** the JSON document is read
- **THEN** it contains a schema version and a generation timestamp that is
  timezone-aware and in UTC

### Requirement: One typed row per qualified spawn

The artifact SHALL contain a row for every spawn it covers, including any spawn
that has no score, rather than omitting rows that lack data.

#### Scenario: Unscored spawn still appears

- **WHEN** a spawn has been ingested but not scored
- **THEN** its row is present in the artifact with its deterministic fields
  populated, and the document marks it as unscored

Rationale: a consumer that computes coverage needs to see the spawns that are
missing scores. Omitting them makes an incomplete run look complete.

#### Scenario: No score is claimed

- **WHEN** the artifact is produced by a run that performed no scoring
- **THEN** no score, verdict, or fix field is present or defaulted anywhere in the
  document

### Requirement: The artifact reports the deterministic facts of the run

Each row SHALL carry the identity, task, volume, health, and cost figures observed
for that spawn.

#### Scenario: Row contents

- **WHEN** a row is read
- **THEN** it carries the qualified session key and its raw components, the agent
  type and which source named it, the task description, the counts of turns and
  tool invocations, error and permission-denial counts, the run duration, the token
  counts including cache reads, and the count of records the parser could not read

### Requirement: A human summary goes to standard output, diagnostics do not

The command SHALL print a readable summary of the run, and SHALL keep progress,
warnings, and error detail off the stream carrying machine-readable output.

#### Scenario: JSON output stays parseable

- **WHEN** the caller requests JSON output and the run emits warnings
- **THEN** standard output contains only the JSON document and the warnings appear
  on the diagnostic stream

Rationale: the output is meant to be piped. A warning printed onto the same stream
would break every consumer.

#### Scenario: Summary reports what was observed

- **WHEN** a run completes with a readable summary
- **THEN** the summary names the agent type, the task, the volume and health counts,
  the cache-read proportion, and the artifact path

#### Scenario: Nothing is presented as a score

- **WHEN** a summary is printed by a run that performed no scoring
- **THEN** the summary presents no score and says that the session is unscored

### Requirement: Report files overwrite in place

Report artifacts SHALL be regenerated at the same path on each run rather than
accumulating timestamped copies.

#### Scenario: Repeated runs do not accumulate files

- **WHEN** the same session is analyzed several times
- **THEN** one artifact file exists for that session, holding the current content

Rationale: history belongs in the store, which is queryable. Stale report files
would be a second, divergent source of truth.
