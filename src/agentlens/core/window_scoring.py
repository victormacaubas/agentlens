"""Scoring every qualifying spawn in a resolved window."""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from agentlens.core.spawn_scoring import SpawnScoringPreview, SpawnScoringRun
from agentlens.errors import JudgeError, JudgeResponseError, JudgeUnavailableError, StoreError
from agentlens.ingest.context import SubagentContextCache
from agentlens.ingest.discovery import discover_subagent_sources
from agentlens.ingest.identity import SubagentSourceBundle
from agentlens.ingest.transcript import parse_transcript
from agentlens.models.facts import FactSession
from agentlens.models.protocols import Clock, JudgeBackend
from agentlens.models.scoring import (
    ScoringOutcome,
    ScoringRequest,
    ScoringStatus,
    SpawnJudgeUsage,
    WindowJudgeUsage,
    WindowScoringOutcome,
    WindowScoringPreview,
    WindowStopReason,
)
from agentlens.models.session_facts import SessionFacts
from agentlens.models.windows import ResolvedWindow
from agentlens.store import Store, open_disposable_clone

logger = logging.getLogger(__name__)

_MAX_ATTEMPTS_PER_SPAWN: Final = 3
_MAX_CONSECUTIVE_FAILURES: Final = 3
DEFAULT_MAX_RUN_COST_USD: Final = 2.00


@dataclass(frozen=True, slots=True, kw_only=True)
class WindowScoringContext:
    """Shared scope and collaborators for one paid or preview window run."""

    projects_root: Path
    claude_root: Path
    store_path: Path
    clock: Clock
    request: ScoringRequest
    agent_type: str | None
    window: ResolvedWindow


@dataclass
class _Tally:
    scored: int = 0
    reused: int = 0
    skipped: int = 0
    failed: int = 0
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    stop_reason: WindowStopReason | None = None
    unattempted: int = 0

    def record(self, outcome: ScoringOutcome) -> None:
        if outcome.status is ScoringStatus.SCORED:
            self.scored += 1
        elif outcome.status is ScoringStatus.REUSED:
            self.reused += 1
        elif outcome.status is ScoringStatus.CLAIMED_ELSEWHERE:
            self.skipped += 1
        else:
            self.failed += 1
        self.cost_usd += outcome.spawn_judge_usage.cost_usd
        self.input_tokens += outcome.spawn_judge_usage.input_tokens
        self.output_tokens += outcome.spawn_judge_usage.output_tokens

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
            stop_reason=self.stop_reason,
            unattempted=self.unattempted,
        )


@dataclass(frozen=True, slots=True)
class _WindowWorklist:
    bundles_by_session_id: dict[str, SubagentSourceBundle]
    rows: tuple[FactSession, ...]

    def bundle_for(self, session_id: str, projects_root: Path) -> SubagentSourceBundle:
        bundle = self.bundles_by_session_id.get(session_id)
        if bundle is None:
            raise StoreError(
                f"windowed spawn {session_id!r} matches no source bundle discovered "
                f"under {projects_root}"
            )
        return bundle


class _WindowWorklistBuilder:
    def __init__(self, context: WindowScoringContext) -> None:
        self._context = context

    def build(self, *, dry_run: bool) -> _WindowWorklist:
        context = self._context
        bundles = discover_subagent_sources(context.projects_root)
        context_cache = SubagentContextCache(context.claude_root)
        facts = tuple(parse_transcript(bundle, context_cache=context_cache) for bundle in bundles)
        bundles_by_session_id = {
            fact.session.identity.session_id: bundle
            for bundle, fact in zip(bundles, facts, strict=True)
        }
        if dry_run:
            with open_disposable_clone(context.store_path, clock=context.clock) as store:
                outcomes = store.upsert_batch(
                    definitions=context_cache.discovered_definitions(),
                    facts=facts,
                )
                rows = store.read_spawns_in_window(
                    context.window.current_start,
                    context.window.current_end,
                    context.agent_type,
                )
        else:
            with Store(context.store_path, clock=context.clock) as store:
                outcomes = store.upsert_batch(
                    definitions=context_cache.discovered_definitions(),
                    facts=facts,
                )
                rows = store.read_spawns_in_window(
                    context.window.current_start,
                    context.window.current_end,
                    context.agent_type,
                )
        logger.info(
            "Window scoring ingest applied %d subagent source(s) for window [%s, %s) agent_type=%s",
            len(outcomes),
            context.window.current_start,
            context.window.current_end,
            context.agent_type,
        )
        return _WindowWorklist(bundles_by_session_id=bundles_by_session_id, rows=rows)


class WindowScoringRun:
    """Coordinates paid scoring for a fixed window with a required judge backend."""

    def __init__(
        self,
        *,
        context: WindowScoringContext,
        judge: JudgeBackend,
        max_run_cost_usd: float = DEFAULT_MAX_RUN_COST_USD,
    ) -> None:
        self._context = context
        self._judge = judge
        self._max_run_cost_usd = max_run_cost_usd

    def score(self) -> WindowScoringOutcome:
        """Score every qualifying spawn in the configured window."""
        worklist = _WindowWorklistBuilder(self._context).build(dry_run=False)
        ordered_rows = sorted(
            worklist.rows,
            key=lambda row: (row.started_at, row.identity.session_id),
        )
        context = self._context
        logger.info(
            "Scoring window [%s, %s) agent_type=%s covers %d spawn(s)",
            context.window.current_start,
            context.window.current_end,
            context.agent_type,
            len(ordered_rows),
        )

        tally = _Tally()
        consecutive_failures = 0
        for index, row in enumerate(ordered_rows):
            if tally.cost_usd >= self._max_run_cost_usd:
                tally.stop_reason = WindowStopReason.COST_CEILING_REACHED
                tally.unattempted = len(ordered_rows) - index
                logger.warning(
                    "Window scoring stopped at cost ceiling $%.2f for window [%s, %s) "
                    "agent_type=%s with %d spawn(s) unattempted",
                    self._max_run_cost_usd,
                    context.window.current_start,
                    context.window.current_end,
                    context.agent_type,
                    tally.unattempted,
                )
                break

            session_id = row.identity.session_id
            bundle = worklist.bundle_for(session_id, context.projects_root)
            stored = self._read_stored_session(session_id)
            outcome = self._score_spawn_within_budget(bundle, stored)
            tally.record(outcome)
            logger.info(
                "Scored spawn session_id=%s agent_type=%s status=%s",
                session_id,
                stored.session.agent_type,
                outcome.status.value,
            )

            if outcome.status is ScoringStatus.FAILED:
                consecutive_failures += 1
                if consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
                    tally.stop_reason = WindowStopReason.JUDGE_UNUSABLE
                    tally.unattempted = len(ordered_rows) - index - 1
                    logger.error(
                        "Window scoring stopped after %d consecutive spawn failures for window "
                        "[%s, %s) agent_type=%s; the judge could not be found or reached; "
                        "%d spawn(s) unattempted",
                        _MAX_CONSECUTIVE_FAILURES,
                        context.window.current_start,
                        context.window.current_end,
                        context.agent_type,
                        tally.unattempted,
                    )
                    break
            elif outcome.status is ScoringStatus.SCORED:
                consecutive_failures = 0

        logger.info(
            "Window scoring complete for window [%s, %s) agent_type=%s: "
            "scored=%d reused=%d skipped=%d failed=%d cost_usd=%s stop_reason=%s "
            "unattempted=%d",
            context.window.current_start,
            context.window.current_end,
            context.agent_type,
            tally.scored,
            tally.reused,
            tally.skipped,
            tally.failed,
            tally.cost_usd,
            tally.stop_reason,
            tally.unattempted,
        )
        return tally.to_outcome()

    def _read_stored_session(self, session_id: str) -> SessionFacts:
        with Store(self._context.store_path, clock=self._context.clock) as store:
            stored = store.read_session(session_id)
        if stored is None:
            raise StoreError(
                f"windowed spawn {session_id!r} was ingested but could not be read back"
            )
        return stored

    def _score_spawn_within_budget(
        self,
        bundle: SubagentSourceBundle,
        stored: SessionFacts,
    ) -> ScoringOutcome:
        session_id = stored.session.identity.session_id
        agent_type = stored.session.agent_type
        attempt = 1
        while True:
            try:
                return SpawnScoringRun(
                    store_path=self._context.store_path,
                    clock=self._context.clock,
                    judge=self._judge,
                    request=self._context.request,
                ).score(bundle, stored)
            except JudgeResponseError as error:
                logger.exception(
                    "Judge rejected the verdict for session_id=%s agent_type=%s; not retried",
                    session_id,
                    agent_type,
                )
                return _failed_outcome(error)
            except JudgeUnavailableError as error:
                if attempt >= _MAX_ATTEMPTS_PER_SPAWN:
                    logger.error(
                        "Exhausted %d attempt(s) reaching the judge for session_id=%s "
                        "agent_type=%s; last error: %s",
                        _MAX_ATTEMPTS_PER_SPAWN,
                        session_id,
                        agent_type,
                        error,
                    )
                    return _failed_outcome(error)
                logger.warning(
                    "Judge unreachable for session_id=%s agent_type=%s (attempt %d/%d): %s",
                    session_id,
                    agent_type,
                    attempt,
                    _MAX_ATTEMPTS_PER_SPAWN,
                    error,
                )
                attempt += 1


class WindowScoringPreviewRun:
    """Coordinates a read-only preview for a fixed window."""

    def __init__(
        self,
        *,
        context: WindowScoringContext,
        per_call_cost_usd_bound: float,
        max_run_cost_usd: float = DEFAULT_MAX_RUN_COST_USD,
    ) -> None:
        self._context = context
        self._per_call_cost_usd_bound = per_call_cost_usd_bound
        self._max_run_cost_usd = max_run_cost_usd

    def preview(self) -> WindowScoringPreview:
        """Return counts and a cost bound without invoking a judge or writing the store."""
        worklist = _WindowWorklistBuilder(self._context).build(dry_run=True)
        ordered_rows = sorted(
            worklist.rows,
            key=lambda row: (row.started_at, row.identity.session_id),
        )
        context = self._context
        checker = SpawnScoringPreview(
            store_path=context.store_path,
            clock=context.clock,
            request=context.request,
        )
        would_score = 0
        would_reuse = 0
        for row in ordered_rows:
            bundle = worklist.bundle_for(row.identity.session_id, context.projects_root)
            stored = SessionFacts(session=row, tool_events=(), skill_signals=())
            if checker.check_reusable(bundle, stored) is not None:
                would_reuse += 1
            else:
                would_score += 1

        cost_bound_usd = min(
            would_score * self._per_call_cost_usd_bound,
            self._max_run_cost_usd + self._per_call_cost_usd_bound,
        )
        logger.info(
            "Dry run over window [%s, %s) agent_type=%s skips writing %d verdict(s) and "
            "%d claim(s); would_score=%d would_reuse=%d cost_upper_bound_usd=%.2f",
            context.window.current_start,
            context.window.current_end,
            context.agent_type,
            would_score,
            would_score,
            would_score,
            would_reuse,
            cost_bound_usd,
        )
        return WindowScoringPreview(
            would_score=would_score,
            would_reuse=would_reuse,
            cost_bound_usd=cost_bound_usd,
        )


def _failed_outcome(error: JudgeError) -> ScoringOutcome:
    if isinstance(error, JudgeResponseError):
        spawn_judge_usage = SpawnJudgeUsage(
            cost_usd=error.cost_usd,
            input_tokens=error.input_tokens,
            output_tokens=error.output_tokens,
        )
    else:
        spawn_judge_usage = SpawnJudgeUsage(cost_usd=0.0, input_tokens=0, output_tokens=0)
    return ScoringOutcome(
        status=ScoringStatus.FAILED,
        verdict=None,
        spawn_judge_usage=spawn_judge_usage,
        is_behind_current_input=False,
    )
