"""Orchestrating one bulk ingest run across the sibling packages.

``ingest``, ``store``, and ``render`` cannot import each other, so this module
is where a projects tree becomes stored rows: discover every subagent source,
parse each one and its project context, then persist the whole batch as one
transaction. Every source is parsed before the store is opened at all, so one
unsound source aborts before a single row is written anywhere.
"""

import logging
from collections.abc import Sequence
from pathlib import Path

from agentlens.ingest.context import SubagentContextCache
from agentlens.ingest.discovery import discover_subagent_sources
from agentlens.ingest.transcript import parse_transcript
from agentlens.models.session_facts import SessionFacts
from agentlens.store import Store, UpsertOutcome

logger = logging.getLogger(__name__)


def batch_ingest_subagents(
    *, projects_root: Path, claude_root: Path, store_path: Path
) -> tuple[UpsertOutcome, ...]:
    """Discover, parse, and persist every subagent transcript under ``projects_root``.

    Every discovered transcript, together with the agent-definition and
    skill-inventory context its own ``cwd`` resolves, is parsed before the
    store at ``store_path`` is opened. A hard source failure anywhere in that
    parse — an unsound transcript, sidecar, definition, or skill file —
    therefore aborts before a single row is written. Persisting then happens
    as one all-or-nothing transaction: a database error partway through the
    batch leaves the store exactly as it was before this call.

    Raises:
        ~agentlens.errors.SourceError: A discovered transcript or a shaping
            input it depends on could not be read soundly, including one that
            changed while being read.
        ~agentlens.errors.StoreError: The store could not be opened or
            written.
    """
    paths = discover_subagent_sources(projects_root)
    logger.info("Discovered %d subagent source(s) under %s", len(paths), projects_root)

    context_cache = SubagentContextCache(claude_root)
    facts = _parse_all(paths, context_cache=context_cache)

    with Store(store_path) as store:
        outcomes = store.upsert_batch(
            definitions=context_cache.discovered_definitions(), facts=facts
        )
    logger.info("Batch-upserted %d session(s) at %s", len(outcomes), store_path)
    return outcomes


def _parse_all(
    paths: Sequence[Path], *, context_cache: SubagentContextCache
) -> tuple[SessionFacts, ...]:
    return tuple(parse_transcript(path, context_cache=context_cache) for path in paths)
