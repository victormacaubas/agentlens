## Purpose

Provide a bounded, isolated transport from a prepared judge prompt to a normalized Claude CLI response without trusting or interpreting the verdict content.

## ADDED Requirements

### Requirement: Prepared prompts are submitted as one bounded judge call
The backend SHALL submit exactly one prepared prompt for the requested model in non-interactive mode, SHALL bound the call to three turns and a finite process timeout, and SHALL support an optional caller-supplied JSON Schema for structured output.

#### Scenario: Raw-output request
- **WHEN** a caller supplies a prepared prompt and requested model without an output schema
- **THEN** the backend makes one bounded call and requests a single JSON result envelope

#### Scenario: Structured-output request
- **WHEN** a caller supplies a prepared prompt, requested model, and JSON Schema
- **THEN** the backend makes one bounded call with that schema and returns the structured output from the result envelope without validating its domain meaning

#### Scenario: Process timeout
- **WHEN** the Claude process does not finish within the backend's configured timeout
- **THEN** the backend terminates the call and reports a judge-unavailable error

### Requirement: Judge execution is isolated from user and project content
The backend SHALL run with built-in tools disabled, automatic project customization disabled, session persistence disabled, an empty temporary working directory, and an environment restricted to `PATH`, `HOME`, and `ANTHROPIC_*` variables. The prepared prompt SHALL be supplied through standard input rather than exposed in process arguments.

#### Scenario: Hardened invocation
- **WHEN** the backend constructs a judge call
- **THEN** the call uses bare mode, disables all built-in tools, loads only the user setting source needed for authentication, limits turns, disables session persistence, and requests JSON output

#### Scenario: Host process has unrelated environment variables
- **WHEN** the parent process contains project, credential, plugin, hook, or other unrelated environment variables
- **THEN** none of those variables are inherited by the judge unless their names are `PATH`, `HOME`, or begin with `ANTHROPIC_`

#### Scenario: Judge runs from a project checkout
- **WHEN** agentlens is launched from a directory containing project settings, instructions, plugins, hooks, skills, or MCP configuration
- **THEN** the judge process runs from a separate empty temporary directory and does not discover that project context

#### Scenario: Prepared prompt contains sensitive transcript text
- **WHEN** the backend submits the prepared prompt
- **THEN** the transcript text is absent from the process argument list and is not persisted as a Claude session

### Requirement: Successful envelopes are normalized without trusting verdict content
The backend SHALL return the untrusted raw result or structured output together with the concrete model identity reported by the response, error status, judge cost, token usage, and duration when those metadata are present. It SHALL NOT echo the requested model alias as the resolved model.

#### Scenario: Raw result succeeds
- **WHEN** Claude returns a successful JSON envelope with a raw result and exactly one concrete model in its model-usage metadata
- **THEN** the backend returns that raw result and concrete model with the available usage, cost, and duration metadata

#### Scenario: Structured result succeeds
- **WHEN** Claude returns a successful JSON envelope containing structured output and exactly one concrete model in its model-usage metadata
- **THEN** the backend returns that structured output as untrusted data and preserves the reported metadata

#### Scenario: Alias resolves to a concrete model
- **WHEN** the requested model is a floating alias
- **THEN** the normalized response identifies the concrete model reported by Claude rather than the alias supplied by the caller

### Requirement: Unusable responses fail closed
The backend SHALL reject output that is not a JSON object, reports an error result, omits the requested result form, lacks a concrete model identity, reports more than one possible concrete judge model, or contains metadata with incompatible types.

#### Scenario: Output is not valid JSON
- **WHEN** the process output cannot be decoded as one JSON object
- **THEN** the backend raises a judge-response error without returning partial verdict content

#### Scenario: Claude reports an error result
- **WHEN** the response envelope identifies the run as an error or a non-success result subtype
- **THEN** the backend raises a judge-response error without returning a successful response

#### Scenario: Concrete model is ambiguous
- **WHEN** the response does not identify exactly one concrete model
- **THEN** the backend raises a judge-response error rather than guessing from the requested alias or insertion order

#### Scenario: Requested result form is absent
- **WHEN** a raw-output call has no raw result or a structured-output call has no structured output
- **THEN** the backend raises a judge-response error

### Requirement: Transport and availability failures use the judge error taxonomy
The backend SHALL translate process-boundary failures so callers do not receive operating-system, subprocess, or JSON decoder exceptions.

#### Scenario: Claude executable is missing
- **WHEN** the configured Claude executable cannot be started
- **THEN** the backend raises a judge-unavailable error

#### Scenario: Claude is not authenticated
- **WHEN** the CLI reports that the selected bare-mode authentication path is unavailable
- **THEN** the backend raises a judge-unavailable error

#### Scenario: Process exits unsuccessfully for another reason
- **WHEN** the process exits unsuccessfully and the failure is not an availability or authentication failure
- **THEN** the backend raises a judge-response error carrying safe diagnostic context but no prepared prompt content
