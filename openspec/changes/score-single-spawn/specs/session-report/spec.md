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
