# Graph Report - agentlens  (2026-08-18)

## Corpus Check
- 83 files · ~91,660 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 557 nodes · 1227 edges · 33 communities (28 shown, 5 thin omitted)
- Extraction: 95% EXTRACTED · 5% INFERRED · 0% AMBIGUOUS · INFERRED: 58 edges (avg confidence: 0.63)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `5d653bb8`
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
- MalformedSourceError
- Single Column Order Declaration
- sidecar.py
- tool_events.py
- SessionFacts
- Sound Snapshot Upsert
- Report Component System
- Required Dependency Injection
- Spec-Driven Change Without Delta Specs
- Project Operating Rules
- Canonical Test Builders
- conftest.py
- Agentlens Visual Identity: Neon Aperture and Agent Orbit Network
- agentlens
- rows.py
- transcript.py
- reading.py

## God Nodes (most connected - your core abstractions)
1. `parse_transcript()` - 36 edges
2. `build_transcript_path()` - 34 edges
3. `build_tool_invocation_pair()` - 28 edges
4. `build_transcript_text()` - 28 edges
5. `Store` - 27 edges
6. `build_session_facts()` - 26 edges
7. `build_fact_session()` - 23 edges
8. `SessionFacts` - 22 edges
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
- **Archived Single Transcript Vertical Slice** — openspec_changes_archive_2026_08_18_ingest_single_transcript_specs_session_command_spec_session_command_specification, openspec_changes_archive_2026_08_18_ingest_single_transcript_specs_session_parser_spec_session_parser_specification, openspec_changes_archive_2026_08_18_ingest_single_transcript_specs_store_schema_spec_store_schema_specification, openspec_changes_archive_2026_08_18_ingest_single_transcript_specs_session_report_spec_session_report_specification [EXTRACTED 1.00]
- **Column Order Single-Source Flow** — openspec_changes_derive_store_column_order_design_single_column_order_declaration, openspec_changes_derive_store_column_order_design_generated_sql_enumerations, openspec_changes_derive_store_column_order_design_name_keyed_extractor_map, openspec_changes_derive_store_column_order_design_sqlite_row_named_reads [EXTRACTED 1.00]
- **Sound Single-Transcript Ingest Flow** — openspec_changes_archive_2026_08_18_ingest_single_transcript_design_parsedsession_handoff, openspec_changes_archive_2026_08_18_ingest_single_transcript_design_streaming_pairing_buffer, openspec_changes_archive_2026_08_18_ingest_single_transcript_design_stat_read_stat_soundness, openspec_changes_archive_2026_08_18_ingest_single_transcript_design_transactional_session_replacement, docs_adr_0008_session_row_derivation_python_session_derivation [EXTRACTED 1.00]
- **Stage-Shaped Analysis Architecture** — docs_agentlens_design_agentlens_analysis_pipeline, docs_adr_0001_layer_map_and_dependency_direction_stage_shaped_layer_map, docs_adr_0001_layer_map_and_dependency_direction_independent_middle_layers, docs_adr_0001_layer_map_and_dependency_direction_core_orchestration [EXTRACTED 1.00]
- **Current Single Transcript Contract** — openspec_specs_session_command_spec_session_command_specification, openspec_specs_session_parser_spec_session_parser_specification, openspec_specs_session_report_spec_session_report_specification, openspec_specs_store_schema_spec_store_schema_specification [INFERRED 0.85]
- **Editorial Report System** — product_data_has_narrative, product_self_contained_html_report, design_red_margin_note, design_report_component_system, design_report_mockup_dark_report_mockup, design_report_mockup_light_light_report_mockup [INFERRED 0.85]

## Communities (33 total, 5 thin omitted)

### Community 0 - "parse_transcript"
Cohesion: 0.09
Nodes (72): parse_transcript(), Path, Parse the subagent transcript at ``path`` into one session and its rows. An…, build_assistant_record(), build_denied_invocation(), build_fragmented_turn(), build_root_fields(), build_sidecar() (+64 more)

### Community 1 - "ingest/session.py"
Cohesion: 0.23
Nodes (14): NameResolution, The agent type resolved for a session, and which link supplied it., build_fact_session(), _count_by_category(), _count_repeated_invocations(), _duration_ms(), JsonRecord, Deriving one session row from a transcript's invocations and sidecar. Several… (+6 more)

### Community 2 - "errors.py"
Cohesion: 0.07
Nodes (47): CaptureFixture, default_store_path(), _exit_code_for(), main(), parse_session_args(), Path, Parse arguments, resolve the composition root, and run the ``session`` command.…, The parsed arguments for the ``session`` subcommand. (+39 more)

### Community 3 - "core/session.py"
Cohesion: 0.06
Nodes (51): BaseException, Protocol, analyze_session(), _persist_or_log(), Path, Orchestrates one session analysis run across the sibling packages. ``ingest``,…, Ingest, persist, and render one subagent transcript. Parses…, _write_artifact_or_log() (+43 more)

### Community 4 - "Stage-Shaped Layer Map"
Cohesion: 0.05
Nodes (39): Measured and Modeled Separation, One-Way Dependency Flow, Project Operating Rules, Vertical Slice Workflow, Read-Only Source Boundary, Untrusted Model Output Boundary, Advisory Output Presentation, Report Trust Boundary (+31 more)

### Community 5 - "Archived Session Parser Specification"
Cohesion: 0.07
Nodes (37): Dry Run With No Writes, Explicit Transcript Path Analysis, Failure Family Exit Codes, Idempotent Session Rerun, Project-Qualified Session Identity, Read-Only Claude Source Tree, Archived Session Command Specification, Ordered Agent Name Resolution (+29 more)

### Community 6 - "Store"
Cohesion: 0.11
Nodes (45): build_session_summary(), _cache_read_proportion(), Path, Cache reads as a share of every input-side token the run consumed. The three…, Build the readable summary of one analyzed spawn. Names the agent type and…, Path, A SQLite-backed store at a caller-supplied path. Opening the connection,…, Store (+37 more)

### Community 7 - "MalformedSourceError"
Cohesion: 0.24
Nodes (9): MalformedSourceError, A transcript or its ``.meta.json`` sidecar could not be parsed., assistant_message_groups(), parse_timestamp(), datetime, JsonRecord, Small helpers for reading fields out of a transcript's already-parsed records.…, Parse a record's root-level ``timestamp`` as a timezone-aware instant. Raises:… (+1 more)

### Community 8 - "Single Column Order Declaration"
Cohesion: 0.08
Nodes (32): ADR 0001 and ADR 0002 Storage Boundary, ADR 0008 Python Session Derivation, Behavior-Preserving Refactor With No Migration, Generated SQL Column Enumerations, Name-Keyed Row Extractor Map, Nullability and Primary-Key Style Fidelity, Single Column Order Declaration, SQL Data Analysis Standard (+24 more)

### Community 9 - "sidecar.py"
Cohesion: 0.33
Nodes (6): Path, Reading the optional ``.meta.json`` sidecar next to a subagent transcript., The fields a ``.meta.json`` sidecar carries about its spawn.…, Return the sidecar next to ``transcript_path``, or ``None`` if absent. Raises:…, read_sidecar(), Sidecar

### Community 10 - "tool_events.py"
Cohesion: 0.10
Nodes (32): content_blocks(), Return ``message.content`` as a sequence of mappings, or an empty one., pair_tool_events(), _PendingInvocation, JsonRecord, Pairing tool invocations with their results into one row per invocation. Each…, Mutable accumulator for one tool_use, filled in as its result arrives. Mutated…, Return one ``FactToolEvent`` per tool invocation found in ``records``.… (+24 more)

### Community 11 - "SessionFacts"
Cohesion: 0.06
Nodes (38): NamedTuple, Row, SqliteRow, One session row and its ordered tool-invocation rows. An instance is always…, SessionFacts, Connection, Replace the stored rows for ``facts``'s session, subject to staleness. Raises:…, Return the stored session identified by ``session_id``, or ``None``. (+30 more)

### Community 13 - "Sound Snapshot Upsert"
Cohesion: 0.20
Nodes (10): Parent Lens, Parent Lens, Qualified Session Identity, Sound Snapshot Upsert, Spawn Grain, Tool Invocation Grain, Stat-Read-Stat Soundness, Streaming Pairing Buffer (+2 more)

### Community 14 - "Report Component System"
Cohesion: 0.50
Nodes (5): Editorial Report Hierarchy, The Red Margin Note, Report Component System, Dark Report Mockup, Light Report Mockup

### Community 15 - "Required Dependency Injection"
Cohesion: 0.50
Nodes (4): Required Dependency Injection, Seam Membership Rule, Single Composition Root, Injection Over Patching

### Community 16 - "Spec-Driven Change Without Delta Specs"
Cohesion: 0.67
Nodes (3): Derive Store Column Order Change Metadata, Spec-Driven Change Without Delta Specs, Spec-Driven OpenSpec Schema

### Community 32 - "rows.py"
Cohesion: 0.15
Nodes (19): FactSession, Fact row types: what the store persists, one row per tool invocation and per…, One agent run: one spawn, never one agent type. ``identity`` and ``revision``…, NameSource, StrEnum, Which link in the name-resolution chain supplied ``agent_type``. Ordered…, The observable state of a source file, used to judge snapshot soundness.…, One agent run: one spawn, never one agent type. ``session_id`` is the SHA-256… (+11 more)

### Community 33 - "transcript.py"
Cohesion: 0.18
Nodes (12): build_session_identity(), derive_transcript_location(), Path, Deriving a subagent session's qualified identity from its transcript path.…, The identity components read off a subagent transcript's file path., Derive the owning project, raw session id, and session kind from ``path``.…, Return the qualified identity for ``location``. ``session_id`` is the SHA-256…, TranscriptLocation (+4 more)

### Community 35 - "reading.py"
Cohesion: 0.28
Nodes (8): The file changed while being read, so the snapshot cannot be trusted., SourceChangedError, Path, Streaming a transcript file once: soundness and raw records together. Soundness…, The result of one streaming pass over a transcript file., Stream ``path`` line by line, hashing its content as it is read. Lines that are…, read_transcript(), TranscriptContents

## Knowledge Gaps
- **41 isolated node(s):** `agentlens`, `Parent Lens`, `Parent Lens`, `Stat-Read-Stat Soundness`, `Streaming Pairing Buffer` (+36 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **5 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `parse_transcript()` connect `parse_transcript` to `transcript.py`, `ingest/session.py`, `core/session.py`, `reading.py`, `MalformedSourceError`, `sidecar.py`, `tool_events.py`, `SessionFacts`?**
  _High betweenness centrality (0.070) - this node is a cross-community bridge._
- **Why does `SessionFacts` connect `SessionFacts` to `parse_transcript`, `transcript.py`, `rows.py`, `core/session.py`, `ingest/session.py`, `Store`?**
  _High betweenness centrality (0.052) - this node is a cross-community bridge._
- **Why does `build_session_facts()` connect `Store` to `parse_transcript`, `ingest/session.py`, `rows.py`, `core/session.py`, `SessionFacts`?**
  _High betweenness centrality (0.035) - this node is a cross-community bridge._
- **Are the 16 inferred relationships involving `Store` (e.g. with `StoreError` and `SessionFacts`) actually correct?**
  _`Store` has 16 INFERRED edges - model-reasoned connections that need verification._
- **What connects `agentlens`, `Parent Lens`, `Parent Lens` to the rest of the system?**
  _41 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `parse_transcript` be split into smaller, more focused modules?**
  _Cohesion score 0.08817498291182502 - nodes in this community are weakly interconnected._
- **Should `errors.py` be split into smaller, more focused modules?**
  _Cohesion score 0.07337662337662337 - nodes in this community are weakly interconnected._