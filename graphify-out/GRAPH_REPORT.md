# Graph Report - agentlens  (2026-08-18)

## Corpus Check
- 101 files · ~106,257 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 891 nodes · 1936 edges · 50 communities (44 shown, 6 thin omitted)
- Extraction: 94% EXTRACTED · 6% INFERRED · 0% AMBIGUOUS · INFERRED: 114 edges (avg confidence: 0.59)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `52cf2af3`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- parse_transcript
- ingest/session.py
- errors.py
- core/session.py
- Stage-Shaped Layer Map
- Archived Session Parser Specification
- Store
- tool_events.py
- Parser Integrity Contract
- test_ingest_agent_definitions.py
- canonical_json_fingerprint
- AgentDefinition
- ADDED Requirements
- Sound Snapshot Upsert
- Report Component System
- Required Dependency Injection
- Spec-Driven OpenSpec Schema
- Project Operating Rules
- Canonical Test Builders
- conftest.py
- Agentlens Visual Identity: Neon Aperture and Agent Orbit Network
- agentlens
- ADDED Requirements
- ADDED Requirements
- transcript.py
- Decisions
- reading.py
- Requirement: Name resolution records which source won
- ADDED Requirements
- ADDED Requirements
- ADDED Requirements
- ADDED Requirements
- Decisions
- report-subagent-deterministic-signals/tasks.md
- test_store_schema.py
- 2026-08-18-derive-store-column-order/proposal.md
- 2026-08-18-derive-store-column-order/tasks.md
- report-subagent-deterministic-signals/proposal.md
- name_resolution.py
- MalformedSourceError
- build_fact_session

## God Nodes (most connected - your core abstractions)
1. `parse_transcript()` - 49 edges
2. `build_transcript_path()` - 47 edges
3. `Store` - 41 edges
4. `build_transcript_text()` - 37 edges
5. `build_tool_invocation_pair()` - 35 edges
6. `MalformedSourceError` - 32 edges
7. `DefinitionScope` - 29 edges
8. `build_fact_session()` - 28 edges
9. `build_session_facts()` - 27 edges
10. `_write()` - 25 edges

## Surprising Connections (you probably didn't know these)
- `Actionable Fix Proposals` --semantically_similar_to--> `The Fix Is the Product`  [INFERRED] [semantically similar]
  docs/agentlens-design.md → PRODUCT.md
- `test_a_bracketed_tools_string_is_rejected_with_the_path_and_field_name()` --uses--> `MalformedSourceError`  [INFERRED]
  tests/unit/test_ingest_agent_definitions.py → src/agentlens/errors.py
- `test_symlink_loop_is_translated_to_a_malformed_source_error()` --uses--> `MalformedSourceError`  [INFERRED]
  tests/unit/test_ingest_agent_definitions.py → src/agentlens/errors.py
- `test_refuses_a_main_session_path_with_no_subagents_segment()` --uses--> `MalformedSourceError`  [INFERRED]
  tests/unit/test_ingest_identity.py → src/agentlens/errors.py
- `test_refuses_a_path_with_no_owning_projects_directory()` --uses--> `MalformedSourceError`  [INFERRED]
  tests/unit/test_ingest_identity.py → src/agentlens/errors.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Archived Single Transcript Vertical Slice** — openspec_changes_archive_2026_08_18_ingest_single_transcript_specs_session_command_spec_session_command_specification, openspec_changes_archive_2026_08_18_ingest_single_transcript_specs_session_parser_spec_session_parser_specification, openspec_changes_archive_2026_08_18_ingest_single_transcript_specs_store_schema_spec_store_schema_specification, openspec_changes_archive_2026_08_18_ingest_single_transcript_specs_session_report_spec_session_report_specification [EXTRACTED 1.00]
- **Sound Single-Transcript Ingest Flow** — openspec_changes_archive_2026_08_18_ingest_single_transcript_design_parsedsession_handoff, openspec_changes_archive_2026_08_18_ingest_single_transcript_design_streaming_pairing_buffer, openspec_changes_archive_2026_08_18_ingest_single_transcript_design_stat_read_stat_soundness, openspec_changes_archive_2026_08_18_ingest_single_transcript_design_transactional_session_replacement, docs_adr_0008_session_row_derivation_python_session_derivation [EXTRACTED 1.00]
- **Stage-Shaped Analysis Architecture** — docs_agentlens_design_agentlens_analysis_pipeline, docs_adr_0001_layer_map_and_dependency_direction_stage_shaped_layer_map, docs_adr_0001_layer_map_and_dependency_direction_independent_middle_layers, docs_adr_0001_layer_map_and_dependency_direction_core_orchestration [EXTRACTED 1.00]
- **Current Single Transcript Contract** — openspec_specs_session_command_spec_session_command_specification, openspec_specs_session_parser_spec_session_parser_specification, openspec_specs_session_report_spec_session_report_specification, openspec_specs_store_schema_spec_store_schema_specification [INFERRED 0.85]
- **Editorial Report System** — product_data_has_narrative, product_self_contained_html_report, design_red_margin_note, design_report_component_system, design_report_mockup_dark_report_mockup, design_report_mockup_light_light_report_mockup [INFERRED 0.85]

## Communities (50 total, 6 thin omitted)

### Community 0 - "parse_transcript"
Cohesion: 0.08
Nodes (88): parse_transcript(), Path, Parse the subagent transcript at ``path`` into one session and its rows. An…, build_assistant_record(), build_denied_invocation(), build_fragmented_turn(), build_root_fields(), build_sidecar() (+80 more)

### Community 1 - "ingest/session.py"
Cohesion: 0.12
Nodes (27): agent_definition_derivation_input(), DerivationInput, derive_session_derivation(), Building the derivation fingerprint and newest observed shaping-input time. A…, One file or fact that shaped a derived row. ``fingerprint_value`` must be JSON-…, Return the transcript's own revision as a derivation input., Return the sidecar's revision and parsed fields as a derivation input., Return the spawn's bound agent definition, or its absence, as a derivation… (+19 more)

### Community 2 - "errors.py"
Cohesion: 0.07
Nodes (49): CaptureFixture, default_store_path(), _exit_code_for(), main(), parse_session_args(), Path, Parse arguments, resolve the composition root, and run the ``session`` command.…, The parsed arguments for the ``session`` subcommand. (+41 more)

### Community 3 - "core/session.py"
Cohesion: 0.06
Nodes (52): Protocol, analyze_session(), _persist_or_log(), Path, Orchestrates one session analysis run across the sibling packages. ``ingest``,…, Ingest, persist, and render one subagent transcript. Parses…, _write_artifact_or_log(), JudgeResponse (+44 more)

### Community 4 - "Stage-Shaped Layer Map"
Cohesion: 0.05
Nodes (39): Measured and Modeled Separation, One-Way Dependency Flow, Project Operating Rules, Vertical Slice Workflow, Read-Only Source Boundary, Untrusted Model Output Boundary, Advisory Output Presentation, Report Trust Boundary (+31 more)

### Community 5 - "Archived Session Parser Specification"
Cohesion: 0.07
Nodes (37): Dry Run With No Writes, Explicit Transcript Path Analysis, Failure Family Exit Codes, Idempotent Session Rerun, Project-Qualified Session Identity, Read-Only Claude Source Tree, Archived Session Command Specification, Ordered Agent Name Resolution (+29 more)

### Community 6 - "Store"
Cohesion: 0.08
Nodes (67): build_session_summary(), Path, Build the readable summary of one analyzed spawn. Names the agent type and…, Path, A SQLite-backed store at a caller-supplied path. Opening the connection,…, Store, build_agent_definition(), build_agent_definition_config() (+59 more)

### Community 7 - "tool_events.py"
Cohesion: 0.13
Nodes (21): assistant_message_groups(), content_blocks(), parse_timestamp(), datetime, JsonRecord, Small helpers for reading fields out of a transcript's already-parsed records.…, Return the raw subagent identifier carried by ``records``. Uses the first…, Parse a record's root-level ``timestamp`` as a timezone-aware instant. Raises:… (+13 more)

### Community 8 - "Parser Integrity Contract"
Cohesion: 0.25
Nodes (8): Session Command Contract, Session Command Specification, Parser Integrity Contract, Session Parser Specification, Report Output Contract, Session Report Specification, Store Integrity Contract, Store Schema Specification

### Community 9 - "test_ingest_agent_definitions.py"
Cohesion: 0.12
Nodes (43): The file changed while being read, so the snapshot cannot be trusted., SourceChangedError, content_addressed_definition_id(), discover_agent_definitions(), datetime, Path, Scan ``directory`` non-recursively for ``*.md`` agent definitions. Returns an…, Return the effective definition per agent name for one project. A project-… (+35 more)

### Community 10 - "canonical_json_fingerprint"
Cohesion: 0.16
Nodes (20): canonical_json_fingerprint(), file_identity(), hash_text(), normalize_path(), Path, Return the SHA-256 hex digest of ``text``, encoded as UTF-8., Hash ``value`` as canonical JSON: sorted keys, no insignificant whitespace. Two…, Return the normalized absolute form of ``path``, without touching disk. (+12 more)

### Community 11 - "AgentDefinition"
Cohesion: 0.05
Nodes (56): BaseException, Row, SqliteRow, The store could not be read or written. Translates ``sqlite3.Error``., StoreError, AgentDefinition, AgentDefinitionConfig, Domain types for one cataloged agent definition. Both ``ingest`` (which… (+48 more)

### Community 12 - "ADDED Requirements"
Cohesion: 0.08
Nodes (23): ADDED Requirements, Purpose, Requirement: Aggregates group subagent spawns by agent type, Requirement: Calendar windows use the machine local timezone, Requirement: Comparable metrics carry prior-window deltas, Requirement: Low-volume trends are suppressed, Requirement: Prior window has equal duration, Requirement: Resolved ranges are half-open (+15 more)

### Community 13 - "Sound Snapshot Upsert"
Cohesion: 0.20
Nodes (10): Parent Lens, Parent Lens, Qualified Session Identity, Sound Snapshot Upsert, Spawn Grain, Tool Invocation Grain, Stat-Read-Stat Soundness, Streaming Pairing Buffer (+2 more)

### Community 14 - "Report Component System"
Cohesion: 0.50
Nodes (5): Editorial Report Hierarchy, The Red Margin Note, Report Component System, Dark Report Mockup, Light Report Mockup

### Community 15 - "Required Dependency Injection"
Cohesion: 0.50
Nodes (4): Required Dependency Injection, Seam Membership Rule, Single Composition Root, Injection Over Patching

### Community 31 - "ADDED Requirements"
Cohesion: 0.09
Nodes (21): ADDED Requirements, Purpose, Requirement: Default output overwrites a stable artifact, Requirement: Deterministic report never invokes the judge, Requirement: Dry run performs no writes, Requirement: Exactly one window selector is accepted, Requirement: Machine-readable output stays isolated, Requirement: Report command discovers before aggregating (+13 more)

### Community 32 - "ADDED Requirements"
Cohesion: 0.11
Nodes (18): ADDED Requirements, Purpose, Requirement: Current files do not rewrite unknown history as fact, Requirement: Declared, available, and fired are independent states, Requirement: Fired-skill count is reproducible, Requirement: Fired state requires execution evidence, Requirement: Missing fires remain deterministic facts, Requirement: Session-skill grain is unique (+10 more)

### Community 33 - "transcript.py"
Cohesion: 0.23
Nodes (11): build_session_identity(), derive_parent_session_id(), derive_transcript_location(), Path, Deriving a subagent session's qualified identity from its transcript path.…, The identity components read off a subagent transcript's file path.…, Derive the owning project, raw session id, and session kind from ``path``.…, Return the qualified identity for ``location``. ``session_id`` is the SHA-256… (+3 more)

### Community 34 - "Decisions"
Cohesion: 0.11
Nodes (17): Agent frontmatter uses a bounded parser, Analytical SQL owns windows and rollups, Bulk ingest validates first and commits once, Context, Decisions, Discovery returns subagent source bundles, not paths alone, Dry run uses a disposable clone of the store, Every derivation input participates in snapshot identity (+9 more)

### Community 35 - "reading.py"
Cohesion: 0.33
Nodes (6): Path, Streaming a transcript file once: soundness and raw records together. Soundness…, The result of one streaming pass over a transcript file., Stream ``path`` line by line, hashing its content as it is read. Lines that are…, read_transcript(), TranscriptContents

### Community 36 - "Requirement: Name resolution records which source won"
Cohesion: 0.11
Nodes (17): ADDED Requirements, MODIFIED Requirements, Requirement: Derivation identity covers every shaping input, Requirement: Name resolution records which source won, Requirement: Parent metadata does not require main-session ingestion, Requirement: Session records carry deterministic reporting context, Scenario: Attribution supplies the name, Scenario: Context inputs are unchanged (+9 more)

### Community 37 - "ADDED Requirements"
Cohesion: 0.12
Nodes (15): ADDED Requirements, Purpose, Requirement: Agent definitions are versioned by content and scope, Requirement: An unknown definition does not drop a spawn, Requirement: Binding requires historical applicability, Requirement: Cataloged definitions expose deterministic configuration, Requirement: Project scope overrides user scope inside that project, Scenario: Both scopes define the same agent (+7 more)

### Community 38 - "ADDED Requirements"
Cohesion: 0.12
Nodes (15): ADDED Requirements, Purpose, Requirement: Agent rollups carry population and trend status, Requirement: Every qualifying subagent spawn has a typed row, Requirement: Output is deterministic-only, Requirement: Report artifact has a stable scope-derived path, Requirement: Spawn rows expose deterministic facts, Requirement: Window report is self-describing (+7 more)

### Community 39 - "ADDED Requirements"
Cohesion: 0.12
Nodes (15): ADDED Requirements, Requirement: Added store data remains reproducible, Requirement: Agent-definition versions are queryable, Requirement: Context changes refresh derived session facts, Requirement: Report windows are queryable without model output, Requirement: Session rows persist deterministic reporting context, Requirement: Session-skill bridge has session-skill grain, Scenario: Current and prior ranges contain spawns (+7 more)

### Community 40 - "ADDED Requirements"
Cohesion: 0.15
Nodes (12): ADDED Requirements, Purpose, Requirement: Discovery finds subagent transcripts across project trees, Requirement: Discovery ingests every sound subagent snapshot, Requirement: Qualified parent identity is retained as metadata, Requirement: Source trees remain read-only, Scenario: Bulk report completes, Scenario: Main-session transcript is encountered (+4 more)

### Community 41 - "Decisions"
Cohesion: 0.17
Nodes (11): Column order is declared once, as an ordered tuple of column definitions in `store/schema.py`, Context, Decisions, `fact_tool_event` gets the same treatment despite lower risk, Goals / Non-Goals, Migration Plan, Reads use `sqlite3.Row` and access by name, Risks / Trade-offs (+3 more)

### Community 42 - "report-subagent-deterministic-signals/tasks.md"
Cohesion: 0.18
Nodes (10): 10. Verify the Phase 2 boundary and finish, 1. Enrich one subagent session end to end, 2. Catalog agent definitions and bind only provable history, 3. Derive session-skill signals vertically, 4. Complete deterministic name and parent resolution, 5. Discover and batch-ingest all subagent sources, 6. Resolve report windows, 7. Query current and prior deterministic aggregates (+2 more)

### Community 43 - "test_store_schema.py"
Cohesion: 0.36
Nodes (9): NamedTuple, _emitted_columns(), PinnedColumn, Path, The emitted schema, pinned column by column as SQLite reports it. These…, One column as ``PRAGMA table_info`` reports it. ``primary_key_position`` is…, test_dim_agent_schema_has_the_declared_columns_in_order(), test_fact_session_schema_has_the_declared_columns_in_order() (+1 more)

### Community 44 - "2026-08-18-derive-store-column-order/proposal.md"
Cohesion: 0.29
Nodes (6): Capabilities, Impact, Modified Capabilities, New Capabilities, What Changes, Why

### Community 45 - "2026-08-18-derive-store-column-order/tasks.md"
Cohesion: 0.29
Nodes (6): 1. Pin the current schema before touching it, 2. Declare each table's column order once, 3. Generate the column lists in the SQL statements, 4. Make writes name-keyed, 5. Make reads name-keyed, 6. Verify the refactor preserved behavior

### Community 46 - "report-subagent-deterministic-signals/proposal.md"
Cohesion: 0.29
Nodes (6): Capabilities, Impact, Modified Capabilities, New Capabilities, What Changes, Why

### Community 47 - "name_resolution.py"
Cohesion: 0.40
Nodes (5): NameResolution, Resolving an agent type through the ordered name-resolution chain. Only the…, The agent type resolved for a session, and which link supplied it., Resolve the agent type, sidecar first and the raw-id hash second., resolve_agent_type()

### Community 48 - "MalformedSourceError"
Cohesion: 0.15
Nodes (29): FrontmatterValue, MalformedSourceError, A transcript or its ``.meta.json`` sidecar could not be parsed., _extract_config(), Reading, cataloging, and binding agent definitions. An agent definition is a…, Frontmatter, _is_unsupported_shape(), list_field() (+21 more)

### Community 49 - "build_fact_session"
Cohesion: 0.16
Nodes (15): build_fact_session(), _count_by_category(), _count_repeated_invocations(), _earliest_timestamp_and_duration(), datetime, JsonRecord, Sum each turn's token figures from its trailing fragment, not every fragment.…, Return the earliest usable timestamp and the record-timestamp span in ms.… (+7 more)

## Knowledge Gaps
- **171 isolated node(s):** `agentlens`, `Context`, `Goals / Non-Goals`, `Column order is declared once, as an ordered tuple of column definitions in `store/schema.py``, `The declaration carries nullability, and reproduces each table's key style faithfully` (+166 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **6 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `parse_transcript()` connect `parse_transcript` to `transcript.py`, `ingest/session.py`, `core/session.py`, `reading.py`, `tool_events.py`, `name_resolution.py`, `MalformedSourceError`, `build_fact_session`?**
  _High betweenness centrality (0.042) - this node is a cross-community bridge._
- **Why does `Store` connect `Store` to `AgentDefinition`, `core/session.py`?**
  _High betweenness centrality (0.029) - this node is a cross-community bridge._
- **Why does `MalformedSourceError` connect `MalformedSourceError` to `parse_transcript`, `ingest/session.py`, `errors.py`, `transcript.py`, `tool_events.py`, `test_ingest_agent_definitions.py`, `build_fact_session`?**
  _High betweenness centrality (0.026) - this node is a cross-community bridge._
- **Are the 28 inferred relationships involving `Store` (e.g. with `StoreError` and `AgentDefinition`) actually correct?**
  _`Store` has 28 INFERRED edges - model-reasoned connections that need verification._
- **What connects `agentlens`, `Context`, `Goals / Non-Goals` to the rest of the system?**
  _171 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `parse_transcript` be split into smaller, more focused modules?**
  _Cohesion score 0.0789293067947838 - nodes in this community are weakly interconnected._
- **Should `ingest/session.py` be split into smaller, more focused modules?**
  _Cohesion score 0.125 - nodes in this community are weakly interconnected._