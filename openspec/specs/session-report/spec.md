# Session Report Specification

## Purpose

What a caller gets back after one agent run is analyzed: a machine-readable
artifact that downstream tooling can depend on, and a human summary, kept on
separate streams so both are usable at once.

## Requirements

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

### Requirement: The schema version reflects the scored shape

The artifact's schema version SHALL change when the scored fields are introduced, so
a consumer can tell which shape it is holding.

#### Scenario: Version distinguishes the shapes

- **WHEN** a consumer reads artifacts produced before and after scoring existed
- **THEN** their schema versions differ

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

### Requirement: A scored row carries its verdict and its provenance

When a spawn has been scored, its row in the artifact SHALL carry the verdict's
scores, evidence, and suggested fixes, together with the rubric version and the
concrete judge model, and SHALL mark which fields are locally derived and which are
untrusted model output.

#### Scenario: Scored row contents

- **WHEN** a row for a scored spawn is read
- **THEN** it carries the overall score, a score and its evidence for each of the
  four rubric dimensions, the suggested fixes, the rubric version, the concrete
  judge model identifier, and the judge call's dollar cost and token counts

#### Scenario: Provenance is machine-readable

- **WHEN** a consumer reads a scored row
- **THEN** it can tell from the document itself which fields are locally derived and
  which are untrusted model output, without knowing the field names in advance

Rationale: a consumer that renders this content needs to know what to escape. Making
that a naming convention it has to learn puts the burden in the wrong place.

### Requirement: Absence of a score stays absent rather than becoming empty

A document produced by a run that did not score SHALL carry no score, verdict, or fix
field at all, even though the document's shape now admits them.

#### Scenario: Unscored run under the scored-capable shape

- **WHEN** a run that scored nothing produces a document under the schema version that
  admits verdict fields
- **THEN** no verdict, score, or fix key is present anywhere in the document, rather
  than being present and set to null, zero, or empty

#### Scenario: Scored and unscored documents differ only by presence

- **WHEN** the same spawn is analyzed once without scoring and once with it
- **THEN** the two documents are identical except that the scored one adds its verdict
  keys, and the unscored one carries no placeholder where they would go

Rationale: the unscored contract already promises absence, and a consumer testing field
presence must keep working once scoring exists. Turning absence into a null would break
every such consumer silently. A document holding both a scored and an unscored spawn is
only reachable through the windowed report, so that case belongs to the change that
adds it rather than to this one.

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

### Requirement: The summary shows scores and does not show untrusted text

When a spawn has been scored, the human summary SHALL present the overall score and
the four dimension scores, and SHALL NOT print evidence or fix text.

#### Scenario: Scored summary

- **WHEN** a summary is printed for a scored spawn
- **THEN** it names the overall score and each dimension score, and it names where
  the suggested fixes were recorded

#### Scenario: Untrusted text stays off the terminal

- **WHEN** a verdict's evidence or fix text contains control characters, line
  breaks, or text shaped like a shell command
- **THEN** none of it reaches the summary, and the summary is unchanged in shape from
  a verdict whose text contains none of those

Rationale: a terminal cannot be relied upon to render hostile text inertly, and the
scores are enough to tell a reader whether to open the artifact. Presenting fix text
readably is worth doing and is not worth doing here.

#### Scenario: Cost is reported in the summary

- **WHEN** a summary is printed for a run that scored a spawn
- **THEN** it names what the scoring cost in dollars and tokens, and reports the
  analyzed spawn's own token usage without any currency figure

### Requirement: Report files overwrite in place

Report artifacts SHALL be regenerated at the same path on each run rather than
accumulating timestamped copies.

#### Scenario: Repeated runs do not accumulate files

- **WHEN** the same session is analyzed several times
- **THEN** one artifact file exists for that session, holding the current content

Rationale: history belongs in the store, which is queryable. Stale report files
would be a second, divergent source of truth.
