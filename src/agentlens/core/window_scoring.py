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
from typing import Final, cast

from agentlens.core.spawn_scoring import SpawnScoringRun
from agentlens.errors import JudgeError, JudgeResponseError, JudgeUnavailableError, StoreError
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
    WindowStopReason,
)
from agentlens.models.session_facts import SessionFacts
from agentlens.models.windows import ResolvedWindow
from agentlens.store import Store

logger = logging.getLogger(__name__)

_MAX_ATTEMPTS_PER_SPAWN: Final = 3
_MAX_CONSECUTIVE_FAILURES: Final = 3
DEFAULT_MAX_RUN_COST_USD: Final = 2.00


@dataclass
class _Tally:
    """Running per-status counts, judge spend, and stop state for one window run."""

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
            stop_reason=self.stop_reason,
            unattempted=self.unattempted,
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
    max_run_cost_usd: float = DEFAULT_MAX_RUN_COST_USD,
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

    A judge that could not be reached is retried up to a bounded number of
    attempts before that spawn is reported failed; a rejected verdict is
    never retried. A rejected verdict's already-spent cost is carried into
    the run's aggregated ``judge_usage`` rather than dropped along with the
    exception that reported it.

    The run stops early in two cases, both reported through ``stop_reason``
    and ``unattempted`` rather than by raising: after a bounded number of
    *consecutive* spawn failures, on the theory that a judge unusable for one
    spawn is unusable for the next; and before starting a spawn whose
    accrued cost so far has already reached ``max_run_cost_usd``, which can
    let the run's real spend exceed the ceiling by at most one call's own
    spend bound. A spawn reused or skipped as claimed elsewhere never calls
    the judge, so it resets neither counter and never trips the ceiling
    check.

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
    consecutive_failures = 0
    for index, row in enumerate(ordered_rows):
        if tally.cost_usd >= max_run_cost_usd:
            tally.stop_reason = WindowStopReason.COST_CEILING_REACHED
            tally.unattempted = len(ordered_rows) - index
            logger.warning(
                "Window scoring stopped at cost ceiling $%.2f with %d spawn(s) unattempted",
                max_run_cost_usd,
                tally.unattempted,
            )
            break

        session_id = row.identity.session_id
        bundle = session_id_to_bundle.get(session_id)
        if bundle is None:
            raise StoreError(
                f"windowed spawn {session_id!r} matches no source bundle discovered "
                f"under {projects_root}"
            )
        stored = _read_stored_session(store_path, clock=clock, session_id=session_id)
        outcome = _score_spawn_within_budget(
            bundle, stored, store_path=store_path, clock=clock, judge=judge, request=request
        )
        tally.record(outcome)

        if outcome.status is ScoringStatus.FAILED:
            consecutive_failures += 1
            if consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
                tally.stop_reason = WindowStopReason.JUDGE_UNUSABLE
                tally.unattempted = len(ordered_rows) - index - 1
                logger.error(
                    "Window scoring stopped after %d consecutive spawn failures; the "
                    "judge could not be found or reached; %d spawn(s) unattempted",
                    _MAX_CONSECUTIVE_FAILURES,
                    tally.unattempted,
                )
                break
        elif outcome.status is ScoringStatus.SCORED:
            consecutive_failures = 0

    logger.info(
        "Window scoring complete: scored=%d reused=%d skipped=%d failed=%d cost_usd=%s "
        "stop_reason=%s unattempted=%d",
        tally.scored,
        tally.reused,
        tally.skipped,
        tally.failed,
        tally.cost_usd,
        tally.stop_reason,
        tally.unattempted,
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


def _score_spawn_within_budget(
    bundle: SubagentSourceBundle,
    stored: SessionFacts,
    *,
    store_path: Path,
    clock: Clock,
    judge: JudgeBackend | None,
    request: ScoringRequest,
) -> ScoringOutcome:
    """Score one spawn, retrying an unreachable judge within its attempt budget.

    ``JudgeResponseError`` (the judge answered but the verdict was rejected)
    is never retried: the verdict is unusable regardless of how many more
    times the judge is asked, so one attempt reports the spawn failed with
    whatever it had already cost. ``JudgeUnavailableError`` (the judge could
    not be reached) is retried back-to-back, with no backoff, until
    ``_MAX_ATTEMPTS_PER_SPAWN`` attempts are spent; exhausting the budget
    also reports the spawn failed. Each retry calls
    :class:`SpawnScoringRun` again, so it shows up in the injected judge's
    own invocation count.
    """
    session_id = stored.session.identity.session_id
    agent_type = stored.session.agent_type
    attempt = 1
    while True:
        try:
            return _score_spawn(
                bundle, stored, store_path=store_path, clock=clock, judge=judge, request=request
            )
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
