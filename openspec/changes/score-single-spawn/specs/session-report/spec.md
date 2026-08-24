## ADDED Requirements

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

A row for a spawn that was not scored SHALL continue to carry no score, verdict, or
fix field at all, in the same document as rows that were scored.

#### Scenario: Mixed document

- **WHEN** an artifact covers both a scored and an unscored spawn
- **THEN** the scored row carries its verdict fields and the unscored row carries
  none of them, rather than carrying them set to null, zero, or empty

Rationale: the unscored contract already promises absence, and a consumer testing
field presence must keep working once scoring exists. Turning absence into a null
would break every such consumer silently.

### Requirement: The schema version reflects the scored shape

The artifact's schema version SHALL change when the scored fields are introduced, so
a consumer can tell which shape it is holding.

#### Scenario: Version distinguishes the shapes

- **WHEN** a consumer reads artifacts produced before and after scoring existed
- **THEN** their schema versions differ

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
