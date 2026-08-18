# Graph Report - agentlens  (2026-08-18)

## Corpus Check
- 89 files · ~90,806 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 547 nodes · 1190 edges · 31 communities (26 shown, 5 thin omitted)
- Extraction: 95% EXTRACTED · 5% INFERRED · 0% AMBIGUOUS · INFERRED: 55 edges (avg confidence: 0.63)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- Transcript Parsing Tests
- Ingest Identity Pipeline
- CLI Error Handling
- Core Orchestration Protocols
- Architectural Rules
- Session Specifications
- SQLite Store Operations
- Session Facts and Summaries
- Column Order Refactor
- Store Domain Rows
- Hashing Utilities
- Store Schema Definition
- CLI Session Integration Tests
- Session Ingest Integrity
- Editorial Report Design
- Dependency Injection Seams
- OpenSpec Change Metadata
- Runtime Dependency Rules
- Testing Builder Strategy
- Pytest Configuration
- Agentlens Visual Identity
- Package Root

## God Nodes (most connected - your core abstractions)
1. `parse_transcript()` - 36 edges
2. `build_transcript_path()` - 34 edges
3. `build_tool_invocation_pair()` - 28 edges
4. `build_transcript_text()` - 28 edges
5. `Store` - 24 edges
6. `SessionFacts` - 22 edges
7. `build_session_facts()` - 22 edges
8. `build_fact_session()` - 20 edges
9. `_write()` - 18 edges
10. `main()` - 16 edges

## Surprising Connections (you probably didn't know these)
- `Actionable Fix Proposals` --semantically_similar_to--> `The Fix Is the Product`  [INFERRED] [semantically similar]
  docs/agentlens-design.md → PRODUCT.md
- `test_refuses_a_main_session_path_with_no_subagents_segment()` --uses--> `MalformedSourceError`  [INFERRED]
  tests/unit/test_ingest_identity.py → src/agentlens/errors.py
- `test_refuses_a_path_with_no_owning_projects_directory()` --uses--> `MalformedSourceError`  [INFERRED]
  tests/unit/test_ingest_identity.py → src/agentlens/errors.py
- `test_empty_file_is_rejected()` --uses--> `MalformedSourceError`  [INFERRED]
  tests/unit/test_ingest_parsing.py → src/agentlens/errors.py
- `test_transcript_with_nothing_usable_is_rejected()` --uses--> `MalformedSourceError`  [INFERRED]
  tests/unit/test_ingest_parsing.py → src/agentlens/errors.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Stage-Shaped Analysis Architecture** — docs_agentlens_design_agentlens_analysis_pipeline, docs_adr_0001_layer_map_and_dependency_direction_stage_shaped_layer_map, docs_adr_0001_layer_map_and_dependency_direction_independent_middle_layers, docs_adr_0001_layer_map_and_dependency_direction_core_orchestration [EXTRACTED 1.00]
- **Sound Single-Transcript Ingest Flow** — openspec_changes_archive_2026_08_18_ingest_single_transcript_design_parsedsession_handoff, openspec_changes_archive_2026_08_18_ingest_single_transcript_design_streaming_pairing_buffer, openspec_changes_archive_2026_08_18_ingest_single_transcript_design_stat_read_stat_soundness, openspec_changes_archive_2026_08_18_ingest_single_transcript_design_transactional_session_replacement, docs_adr_0008_session_row_derivation_python_session_derivation [EXTRACTED 1.00]
- **Editorial Report System** — product_data_has_narrative, product_self_contained_html_report, design_red_margin_note, design_report_component_system, design_report_mockup_dark_report_mockup, design_report_mockup_light_light_report_mockup [INFERRED 0.85]
- **Archived Single Transcript Vertical Slice** — openspec_changes_archive_2026_08_18_ingest_single_transcript_specs_session_command_spec_session_command_specification, openspec_changes_archive_2026_08_18_ingest_single_transcript_specs_session_parser_spec_session_parser_specification, openspec_changes_archive_2026_08_18_ingest_single_transcript_specs_store_schema_spec_store_schema_specification, openspec_changes_archive_2026_08_18_ingest_single_transcript_specs_session_report_spec_session_report_specification [EXTRACTED 1.00]
- **Current Single Transcript Contract** — openspec_specs_session_command_spec_session_command_specification, openspec_specs_session_parser_spec_session_parser_specification, openspec_specs_session_report_spec_session_report_specification, openspec_specs_store_schema_spec_store_schema_specification [INFERRED 0.85]
- **Column Order Single-Source Flow** — openspec_changes_derive_store_column_order_design_single_column_order_declaration, openspec_changes_derive_store_column_order_design_generated_sql_enumerations, openspec_changes_derive_store_column_order_design_name_keyed_extractor_map, openspec_changes_derive_store_column_order_design_sqlite_row_named_reads [EXTRACTED 1.00]

## Communities (31 total, 5 thin omitted)

### Community 0 - "Transcript Parsing Tests"
Cohesion: 0.09
Nodes (72): parse_transcript(), Path, Parse the subagent transcript at ``path`` into one session and its rows. An…, build_assistant_record(), build_denied_invocation(), build_fragmented_turn(), build_root_fields(), build_sidecar() (+64 more)

### Community 1 - "Ingest Identity Pipeline"
Cohesion: 0.05
Nodes (66): MalformedSourceError, A transcript or its ``.meta.json`` sidecar could not be parsed., build_session_identity(), derive_transcript_location(), Path, Deriving a subagent session's qualified identity from its transcript path.…, The identity components read off a subagent transcript's file path., Derive the owning project, raw session id, and session kind from ``path``.… (+58 more)

### Community 2 - "CLI Error Handling"
Cohesion: 0.07
Nodes (36): BaseException, default_store_path(), _exit_code_for(), parse_session_args(), Path, The parsed arguments for the ``session`` subcommand., Parse the ``session`` subcommand's arguments. Testable directly against a plain…, Return the store's default location, under the user's cache directory. (+28 more)

### Community 3 - "Core Orchestration Protocols"
Cohesion: 0.08
Nodes (35): Protocol, analyze_session(), _persist_or_log(), Path, Orchestrates one session analysis run across the sibling packages. ``ingest``,…, Ingest, persist, and render one subagent transcript. Parses…, _write_artifact_or_log(), JudgeResponse (+27 more)

### Community 4 - "Architectural Rules"
Cohesion: 0.05
Nodes (39): Measured and Modeled Separation, One-Way Dependency Flow, Project Operating Rules, Vertical Slice Workflow, Read-Only Source Boundary, Untrusted Model Output Boundary, Advisory Output Presentation, Report Trust Boundary (+31 more)

### Community 5 - "Session Specifications"
Cohesion: 0.07
Nodes (37): Dry Run With No Writes, Explicit Transcript Path Analysis, Failure Family Exit Codes, Idempotent Session Rerun, Project-Qualified Session Identity, Read-Only Claude Source Tree, Archived Session Command Specification, Ordered Agent Name Resolution (+29 more)

### Community 6 - "SQLite Store Operations"
Cohesion: 0.12
Nodes (29): Connection, Path, A SQLite-backed store at a caller-supplied path. Opening the connection,…, Replace the stored rows for ``facts``'s session, subject to staleness. Raises:…, Return the stored session identified by ``session_id``, or ``None``., Store, StrEnum, What happened when a session snapshot was presented for writing. ``REPLACED``… (+21 more)

### Community 7 - "Session Facts and Summaries"
Cohesion: 0.12
Nodes (29): build_session_summary(), _cache_read_proportion(), Path, Cache reads as a share of every input-side token the run consumed. The three…, Build the readable summary of one analyzed spawn. Names the agent type and…, build_fact_session(), build_session_facts(), FakeClock (+21 more)

### Community 8 - "Column Order Refactor"
Cohesion: 0.08
Nodes (32): ADR 0001 and ADR 0002 Storage Boundary, ADR 0008 Python Session Derivation, Behavior-Preserving Refactor With No Migration, Generated SQL Column Enumerations, Name-Keyed Row Extractor Map, Nullability and Primary-Key Style Fidelity, Single Column Order Declaration, SQL Data Analysis Standard (+24 more)

### Community 9 - "Store Domain Rows"
Cohesion: 0.13
Nodes (24): SqliteRow, FactSession, One agent run: one spawn, never one agent type. ``identity`` and ``revision``…, NameSource, StrEnum, Which link in the name-resolution chain supplied ``agent_type``. Ordered…, Which kind of transcript a session came from. Part of the qualified key,…, SessionKind (+16 more)

### Community 10 - "Hashing Utilities"
Cohesion: 0.16
Nodes (20): canonical_json_fingerprint(), file_identity(), hash_text(), normalize_path(), Path, Return the SHA-256 hex digest of ``text``, encoded as UTF-8., Hash ``value`` as canonical JSON: sorted keys, no insignificant whitespace. Two…, Return the normalized absolute form of ``path``, without touching disk. (+12 more)

### Community 11 - "Store Schema Definition"
Cohesion: 0.16
Nodes (16): NamedTuple, _Column, _column_ddl(), _create_table_sql(), ensure_schema(), Connection, DDL for the two fact tables the store persists. ``fact_session`` holds one row…, Create both fact tables if they do not already exist. (+8 more)

### Community 12 - "CLI Session Integration Tests"
Cohesion: 0.33
Nodes (17): CaptureFixture, main(), Parse arguments, resolve the composition root, and run the ``session`` command.…, _fact_session_count(), MonkeyPatch, Path, The ``agentlens session`` command: end to end, exit codes, and stream…, _snapshot() (+9 more)

### Community 13 - "Session Ingest Integrity"
Cohesion: 0.20
Nodes (10): Parent Lens, Parent Lens, Qualified Session Identity, Sound Snapshot Upsert, Spawn Grain, Tool Invocation Grain, Stat-Read-Stat Soundness, Streaming Pairing Buffer (+2 more)

### Community 14 - "Editorial Report Design"
Cohesion: 0.50
Nodes (5): Editorial Report Hierarchy, The Red Margin Note, Report Component System, Dark Report Mockup, Light Report Mockup

### Community 15 - "Dependency Injection Seams"
Cohesion: 0.50
Nodes (4): Required Dependency Injection, Seam Membership Rule, Single Composition Root, Injection Over Patching

### Community 16 - "OpenSpec Change Metadata"
Cohesion: 0.67
Nodes (3): Derive Store Column Order Change Metadata, Spec-Driven Change Without Delta Specs, Spec-Driven OpenSpec Schema

## Knowledge Gaps
- **41 isolated node(s):** `agentlens`, `Quality Gate CI`, `Project Operating Rules`, `Agentlens CLI`, `Report Trust Boundary` (+36 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **5 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `parse_transcript()` connect `Transcript Parsing Tests` to `Ingest Identity Pipeline`, `Core Orchestration Protocols`?**
  _High betweenness centrality (0.073) - this node is a cross-community bridge._
- **Why does `SessionFacts` connect `Core Orchestration Protocols` to `Transcript Parsing Tests`, `Ingest Identity Pipeline`, `SQLite Store Operations`, `Session Facts and Summaries`, `Store Domain Rows`?**
  _High betweenness centrality (0.058) - this node is a cross-community bridge._
- **Why does `build_session_facts()` connect `Session Facts and Summaries` to `Transcript Parsing Tests`, `Ingest Identity Pipeline`, `Core Orchestration Protocols`, `SQLite Store Operations`, `Store Domain Rows`?**
  _High betweenness centrality (0.030) - this node is a cross-community bridge._
- **Are the 13 inferred relationships involving `Store` (e.g. with `StoreError` and `SessionFacts`) actually correct?**
  _`Store` has 13 INFERRED edges - model-reasoned connections that need verification._
- **What connects `agentlens`, `Quality Gate CI`, `Project Operating Rules` to the rest of the system?**
  _41 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Transcript Parsing Tests` be split into smaller, more focused modules?**
  _Cohesion score 0.08817498291182502 - nodes in this community are weakly interconnected._
- **Should `Ingest Identity Pipeline` be split into smaller, more focused modules?**
  _Cohesion score 0.05017543859649123 - nodes in this community are weakly interconnected._