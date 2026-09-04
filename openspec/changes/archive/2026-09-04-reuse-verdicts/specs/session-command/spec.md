## ADDED Requirements

### Requirement: The scorer's owner identity is logged

The diagnostic stream SHALL record the owner value this run uses to claim verdict
identities, once, in the same startup line that records the resolved arguments.

#### Scenario: Owner appears in the resolved-argument line

- **WHEN** the command starts with scoring requested
- **THEN** the resolved-argument line on the diagnostic stream carries the owner value
  this run will claim with, alongside whether scoring was requested and which model

#### Scenario: Two runs log different owners

- **WHEN** two invocations start
- **THEN** the owner values they log differ

Rationale: when a run reports a spawn as claimed elsewhere, the only way to find out
which process is holding it is to match the owner in the store against the owner a
process logged at startup. Without the line, a stuck claim is anonymous and the
operator's only recourse is to wait out the lease.

#### Scenario: The owner does not identify the machine or the user

- **WHEN** the owner value is logged
- **THEN** it carries no hostname, username, or path

Rationale: the diagnostic stream is pasted into issues and CI logs. An owner needs to
be unique and matchable, which does not require it to describe the host.
