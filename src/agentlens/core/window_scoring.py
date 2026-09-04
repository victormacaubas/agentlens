"""Scoring every qualifying spawn in a resolved window, one pass.

``ingest``, ``store``, and ``render`` cannot import each other, so this module
is where a resolved window becomes a scored batch: discover and parse every
subagent source, persist the whole batch, read back the window's spawns, pair
each one to its source bundle, and score every spawn oldest first through the
existing per-spawn :class:`~agentlens.core.spawn_scoring.SpawnScoringRun`.
Reuse and claim coordination stay exactly where that class already puts them;
this module only decides which spawns are in scope and in what order.
"""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from agentlens.core.spawn_scoring import SpawnScoringRun
from agentlens.errors import JudgeError, JudgeResponseError, StoreError
from agentlens.ingest.context import SubagentContextCache
from agentlens.ingest.discovery import discover_subagent_sources
from agentlens.ingest.identity import SubagentSourceBundle
from agentlens.ingest.transcript import parse_transcript
from agentlens.models.facts import FactSession
from agentlens.models.protocols import Clock, JudgeBackend
from agentlens.models.scoring import (
    RunJudgeUsage,
    ScoringOutcome,
    ScoringRequest,
    ScoringStatus,
    WindowJudgeUsage,
    WindowScoringOutcome,
)
from agentlens.models.session_facts import SessionFacts
from agentlens.models.windows import ResolvedWindow
from agentlens.store import Store

logger = logging.getLogger(__name__)


@dataclass
class _Tally:
    """Running per-status counts and judge spend for one window run."""

    scored: int = 0
    reused: int = 0
    skipped: int = 0
    failed: int = 0
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0

    def record(self, outcome: ScoringOutcome) -> None:
        if outcome.status is ScoringStatus.SCORED:
            self.scored += 1
        elif outcome.status is ScoringStatus.REUSED:
            self.reused += 1
        elif outcome.status is ScoringStatus.CLAIMED_ELSEWHERE:
            self.skipped += 1
        else:
            self.failed += 1
        self.cost_usd += outcome.run_judge_usage.cost_usd
        self.input_tokens += outcome.run_judge_usage.input_tokens
        self.output_tokens += outcome.run_judge_usage.output_tokens

    def to_outcome(self) -> WindowScoringOutcome:
        return WindowScoringOutcome(
            scored=self.scored,
            reused=self.reused,
            skipped=self.skipped,
            failed=self.failed,
            judge_usage=WindowJudgeUsage(
                cost_usd=self.cost_usd,
                input_tokens=self.input_tokens,
                output_tokens=self.output_tokens,
            ),
            stop_reason=None,
            unattempted=0,
        )


def score_window(
    *,
    projects_root: Path,
    claude_root: Path,
    store_path: Path,
    clock: Clock,
    judge: JudgeBackend | None,
    request: ScoringRequest,
    agent_type: str | None,
    window: ResolvedWindow,
) -> WindowScoringOutcome:
    """Score every subagent spawn in ``window`` that matches ``agent_type``.

    Discovers and parses every subagent source under ``projects_root``,
    persists the batch, then reads back the spawns whose ``started_at``
    falls in ``window``'s half-open bounds, orders them oldest first by
    ``(started_at, session_id)``, and scores each through a freshly
    constructed :class:`SpawnScoringRun`. A spawn whose identity resolves to
    a reusable verdict never reaches the judge. A window covering no spawns,
    or none matching ``agent_type``, succeeds with every count at zero and no
    judge call.

    A judge failure for one spawn is recorded as that spawn's failed outcome
    rather than raised, and the run continues with the spawns that remain.
    A rejected verdict's already-spent cost is carried into the run's
    aggregated ``judge_usage`` rather than dropped along with the exception
    that reported it.

    Raises:
        ~agentlens.errors.SourceError: A discovered transcript or a shaping
            input it depends on could not be read soundly.
        ~agentlens.errors.StoreError: The store could not be opened, written,
            or read back, or a windowed spawn matched no discovered source.
        ~agentlens.errors.ConfigError: Scoring was requested without a judge
            backend.
    """
    session_id_to_bundle, rows = _ingest_and_read_window(
        projects_root=projects_root,
        claude_root=claude_root,
        store_path=store_path,
        clock=clock,
        window=window,
        agent_type=agent_type,
    )
    ordered_rows = sorted(rows, key=lambda row: (row.started_at, row.identity.session_id))
    logger.info(
        "Scoring window [%s, %s) agent_type=%s covers %d spawn(s)",
        window.current_start,
        window.current_end,
        agent_type,
        len(ordered_rows),
    )

    tally = _Tally()
    for row in ordered_rows:
        session_id = row.identity.session_id
        bundle = session_id_to_bundle.get(session_id)
        if bundle is None:
            raise StoreError(
                f"windowed spawn {session_id!r} matches no source bundle discovered "
                f"under {projects_root}"
            )
        stored = _read_stored_session(store_path, clock=clock, session_id=session_id)
        try:
            outcome = _score_spawn(
                bundle, stored, store_path=store_path, clock=clock, judge=judge, request=request
            )
        except JudgeError as error:
            logger.exception(
                "Scoring failed for session_id=%s agent_type=%s; continuing with "
                "the rest of the window",
                session_id,
                stored.session.agent_type,
            )
            outcome = _failed_outcome(error)
        tally.record(outcome)

    logger.info(
        "Window scoring complete: scored=%d reused=%d skipped=%d failed=%d cost_usd=%s",
        tally.scored,
        tally.reused,
        tally.skipped,
        tally.failed,
        tally.cost_usd,
    )
    return tally.to_outcome()


def _ingest_and_read_window(
    *,
    projects_root: Path,
    claude_root: Path,
    store_path: Path,
    clock: Clock,
    window: ResolvedWindow,
    agent_type: str | None,
) -> tuple[dict[str, SubagentSourceBundle], tuple[FactSession, ...]]:
    bundles = discover_subagent_sources(projects_root)
    context_cache = SubagentContextCache(claude_root)
    facts = tuple(parse_transcript(bundle, context_cache=context_cache) for bundle in bundles)
    session_id_to_bundle = {
        fact.session.identity.session_id: bundle
        for bundle, fact in zip(bundles, facts, strict=True)
    }

    with Store(store_path, clock=clock) as store:
        outcomes = store.upsert_batch(
            definitions=context_cache.discovered_definitions(), facts=facts
        )
        rows = store.read_spawns_in_window(window.current_start, window.current_end, agent_type)
    logger.info("Window scoring ingest applied %d subagent source(s)", len(outcomes))
    return session_id_to_bundle, rows


def _read_stored_session(store_path: Path, *, clock: Clock, session_id: str) -> SessionFacts:
    with Store(store_path, clock=clock) as store:
        stored = store.read_session(session_id)
    if stored is None:
        raise StoreError(f"windowed spawn {session_id!r} was ingested but could not be read back")
    return stored


def _score_spawn(
    bundle: SubagentSourceBundle,
    stored: SessionFacts,
    *,
    store_path: Path,
    clock: Clock,
    judge: JudgeBackend | None,
    request: ScoringRequest,
) -> ScoringOutcome:
    """Score one spawn, narrowing the ``None``-only-on-dry-run return.

    ``dry_run=False`` here always yields a ``ScoringOutcome``; ``score()``'s
    ``None`` return is reserved for its dry-run paths, which this run never
    takes, so the cast reflects a contract ``SpawnScoringRun`` already
    guarantees rather than a condition this function checks.
    """
    outcome = SpawnScoringRun(
        store_path=store_path, clock=clock, judge=judge, request=request
    ).score(bundle, stored, dry_run=False)
    return cast(ScoringOutcome, outcome)


def _failed_outcome(error: JudgeError) -> ScoringOutcome:
    """Build the ``FAILED`` outcome for a spawn whose judge call could not be used.

    Carries whatever cost a rejected verdict's call had already spent, read
    off ``error`` when it is a :class:`JudgeResponseError`, so a validation
    failure after a completed call is not reported as free. Any other
    ``JudgeError`` carries no cost, since no call completed.
    """
    if isinstance(error, JudgeResponseError):
        run_judge_usage = RunJudgeUsage(
            cost_usd=error.cost_usd,
            input_tokens=error.input_tokens,
            output_tokens=error.output_tokens,
        )
    else:
        run_judge_usage = RunJudgeUsage(cost_usd=0.0, input_tokens=0, output_tokens=0)
    return ScoringOutcome(
        status=ScoringStatus.FAILED,
        verdict=None,
        run_judge_usage=run_judge_usage,
        is_behind_current_input=False,
    )
