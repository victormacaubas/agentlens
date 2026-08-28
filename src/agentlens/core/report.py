"""Orchestrates one report run across the sibling packages.

``ingest``, ``store``, and ``render`` cannot import each other, so this
module is where a resolved window becomes a rendered report: discover and
parse every subagent source, persist the whole batch, read back the
window's spawns and aggregates, and hand that to the renderer. Discovery,
parsing, and persistence are the same all-or-nothing batch
:func:`agentlens.core.ingest_run.batch_ingest_subagents` uses for the
``session`` path; this module adds the window read and the report-shaped
document on top.
"""

import logging
from pathlib import Path

from agentlens.core.ingest_run import PreparedIngestBatch, prepare_ingest_batch
from agentlens.models.protocols import Clock
from agentlens.models.report_document import REPORT_SCHEMA_VERSION, ReportDocument, ReportSpawn
from agentlens.models.windows import ResolvedWindow
from agentlens.render.artifact import report_artifact_path, write_report_artifact
from agentlens.render.document import build_report_document_json, render_document_json
from agentlens.render.summary import build_report_summary
from agentlens.store import Store, open_disposable_clone

logger = logging.getLogger(__name__)

FORMAT_JSON = "json"


def generate_report(
    *,
    window: ResolvedWindow,
    agent_filter: str | None,
    store_path: Path,
    claude_root: Path,
    output_format: str | None,
    clock: Clock,
    dry_run: bool,
) -> str:
    """Discover, persist, query, and render one report window.

    Discovers and parses every subagent source under ``claude_root /
    "projects"`` before opening any store target, then applies the
    resulting batch, queries the current window's qualifying spawns, their
    skill-bridge rows, and both windows' agent rollups, and builds one
    ``ReportDocument`` from what that target actually holds. Never
    constructs or calls a judge backend.

    A normal run applies the batch to the persistent store at
    ``store_path``. ``dry_run`` instead applies it to a disposable clone —
    a copy of ``store_path`` when it exists, otherwise an empty store — so
    the same query and render path runs without writing ``store_path`` or a
    report artifact; diagnostics name what would have been written.

    Returns the JSON document verbatim when ``output_format`` is
    ``FORMAT_JSON``; otherwise returns the human-readable summary naming
    the report artifact's path, writing that artifact unless ``dry_run``
    is set.

    Raises:
        ~agentlens.errors.SourceError: A discovered source could not be read
            soundly.
        ~agentlens.errors.StoreError: The store could not be opened, written,
            or read back.
    """
    prepared = prepare_ingest_batch(projects_root=claude_root / "projects", claude_root=claude_root)

    if dry_run:
        with open_disposable_clone(store_path, clock=clock) as store:
            document = _upsert_and_build_document(
                store, prepared=prepared, window=window, agent_filter=agent_filter, clock=clock
            )
    else:
        with Store(store_path, clock=clock) as store:
            document = _upsert_and_build_document(
                store, prepared=prepared, window=window, agent_filter=agent_filter, clock=clock
            )

    document_json = build_report_document_json(document)
    if output_format == FORMAT_JSON:
        return render_document_json(document_json)

    artifact_path = report_artifact_path(selector=window.selector, agent_filter=agent_filter)
    if dry_run:
        logger.info("Dry run: would write report artifact to %s", artifact_path)
    else:
        write_report_artifact(document_json, selector=window.selector, agent_filter=agent_filter)
    return build_report_summary(document, artifact_path=artifact_path)


def _upsert_and_build_document(
    store: Store,
    *,
    prepared: PreparedIngestBatch,
    window: ResolvedWindow,
    agent_filter: str | None,
    clock: Clock,
) -> ReportDocument:
    outcomes = store.upsert_batch(definitions=prepared.definitions, facts=prepared.facts)
    logger.info("Report ingest applied %d subagent source(s)", len(outcomes))

    spawns = store.read_spawns_in_window(window.current_start, window.current_end, agent_filter)
    skill_signals_by_session = store.read_skill_signals_for_sessions(
        [spawn.identity.session_id for spawn in spawns]
    )
    agent_rollups = store.read_agent_rollups(
        window.current_start,
        window.current_end,
        window.prior_start,
        window.prior_end,
        agent_filter,
        min_sessions_for_trend=window.min_sessions_for_trend,
    )
    logger.info(
        "Report window [%s, %s) covers %d spawn(s) across %d agent type(s)",
        window.current_start,
        window.current_end,
        len(spawns),
        len(agent_rollups),
    )

    return ReportDocument(
        schema_version=REPORT_SCHEMA_VERSION,
        generated_at=clock.now(),
        window=window,
        agent_filter=agent_filter,
        spawns=tuple(
            ReportSpawn(
                session=spawn,
                skill_signals=skill_signals_by_session.get(spawn.identity.session_id, ()),
            )
            for spawn in spawns
        ),
        agent_rollups=agent_rollups,
    )
