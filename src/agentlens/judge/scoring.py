"""Scoring loop: finds subagent sessions lacking a verdict for the current
rubric and judge model, scores them via a `Judge` backend, and persists
verdicts into `fact_verdict`.

`judge_model` identity is the backend's resolved concrete model, not
necessarily the alias a caller configures the loop with — see
`ScoringLoop.score_window` for how the loop resolves one against the other.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

from agentlens.errors import JudgeError, JudgeUnavailableError, StaleVerdictError
from agentlens.judge.protocol import Judge, Verdict, bounded_diagnostic, validate_verdict
from agentlens.judge.rubric import MODEL_ALIASES
from agentlens.judge.transcript_view import build_transcript_view
from agentlens.parser.session import SESSION_KIND_SUBAGENT, ParsedSession
from agentlens.reporting.date_window import WindowRange
from agentlens.store.models import (
    ScoringClaimRecord,
    SessionRecord,
    ToolEventRecord,
    VerdictRecord,
)
from agentlens.store.operations import (
    acquire_scoring_claim,
    finalize_scoring_claim,
    release_scoring_claim,
    set_session_judge_input_hash,
    verdict_exists,
)

logger = logging.getLogger(__name__)

DEFAULT_CONSECUTIVE_FAILURE_LIMIT: Final[int] = 3
DEFAULT_CLAIM_TTL_SECONDS: Final[int] = 15 * 60
KNOWN_MODEL_ALIASES: Final[frozenset[str]] = MODEL_ALIASES


def is_concrete_model_id(model: str) -> bool:
    """Return whether `model` is not one of the known floating aliases.

    Used by the loop to decide whether resolution is needed, and by the
    CLI to decide whether a pre-scoring session count is exact or an upper
    bound.
    """
    return model not in KNOWN_MODEL_ALIASES


@dataclass(frozen=True)
class ProgressEvent:
    """Emitted after each session in the scoring loop."""

    index: int
    total: int
    session: SessionRecord
    verdict: Verdict | None
    error: str | None


@dataclass(frozen=True)
class ScoringResult:
    """Summary of one scoring invocation."""

    scored: int
    skipped: int
    total_cost_usd: float
    aborted: bool
    attempts: int = 0
    remaining: int = 0
    resolved_model: str | None = None


@dataclass
class _RunState:
    scored: int = 0
    skipped: int = 0
    total_cost_usd: float = 0.0
    aborted: bool = False
    attempts: int = 0
    consecutive_failures: int = 0
    progress_index: int = 0
    processed_session_ids: set[str] = field(default_factory=set)

    def result(
        self,
        *,
        remaining: int = 0,
        resolved_model: str | None = None,
    ) -> ScoringResult:
        return ScoringResult(
            scored=self.scored,
            skipped=self.skipped,
            total_cost_usd=self.total_cost_usd,
            aborted=self.aborted,
            attempts=self.attempts,
            remaining=remaining,
            resolved_model=resolved_model,
        )


@dataclass(frozen=True)
class _PreparedSession:
    transcript_view: str
    judge_input_hash: str


class ScoringLoop:
    """Owns the judge, store connection, and scoring config for one run.

    Constructed once per invocation of the `score` command; `find_unscored_sessions`,
    `run`, and `score_window` share the connection and config held here
    rather than threading them through every call.
    """

    def __init__(
        self,
        *,
        judge: Judge,
        conn: sqlite3.Connection,
        rubric_version: str,
        judge_model: str,
        max_sessions: int | None = None,
        consecutive_failure_limit: int = DEFAULT_CONSECUTIVE_FAILURE_LIMIT,
        claim_ttl_seconds: int = DEFAULT_CLAIM_TTL_SECONDS,
        owner_id: str | None = None,
    ) -> None:
        self.judge = judge
        self.conn = conn
        self.rubric_version = rubric_version
        self.judge_model = judge_model
        self.max_sessions = max_sessions
        self.consecutive_failure_limit = consecutive_failure_limit
        self.claim_ttl_seconds = claim_ttl_seconds
        self.owner_id = owner_id or uuid.uuid4().hex
        self._resolved_model = judge.resolved_model

    def find_unscored_sessions(
        self, *, window: WindowRange, agent_type: str | None = None
    ) -> list[SessionRecord]:
        """Return subagent sessions in `window` with no verdict for the
        current rubric and judge model.

        Keys on the judge's resolved model once a call this run has produced
        one; before that, keys on the configured value (alias or concrete
        id). For a floating alias, no stored verdict was ever written under
        the alias itself, so that pre-resolution query over-counts — every
        session in the window matches. That over-count is the upper bound
        `score_window` resolves before scoring the remainder.
        """
        resolved_model = self._resolved_model
        query_model = resolved_model if resolved_model is not None else self.judge_model
        query = """
            SELECT fs.session_id, fs.agent_id, fs.agent_type, fs.name_source, fs.session_kind,
                   fs.spawn_depth, fs.parent_session_id, fs.spawn_tool_use_id,
                   fs.task_description, fs.session_date, fs.n_turns, fs.n_tool_calls,
                   fs.n_reads, fs.n_edits, fs.n_writes, fs.n_bash, fs.n_files_touched,
                   fs.n_errors, fs.n_permission_denials, fs.n_duplicate_tool_calls,
                   fs.final_report_flagged_partial, fs.duration_sec, fs.input_tokens,
                   fs.output_tokens, fs.cache_read_tokens, fs.cache_creation_tokens,
                   fs.task_prompt_len, fs.n_skills_fired, fs.raw_session_id,
                   fs.source_project, fs.source_revision, fs.source_mtime_ns,
                   fs.source_size, fs.source_content_hash, fs.judge_input_hash,
                   fs.agent_definition_id
            FROM fact_session fs
            WHERE fs.session_kind = ?
              AND fs.session_date >= ?
              AND fs.session_date < ?
        """
        params: list[Any] = [
            SESSION_KIND_SUBAGENT,
            window.start.isoformat(),
            window.end.isoformat(),
        ]
        if agent_type is not None:
            query += " AND fs.agent_type = ?"
            params.append(agent_type)

        query += """
              AND NOT EXISTS (
                  SELECT 1 FROM fact_verdict fv
                  WHERE fv.session_id = fs.session_id
                    AND fv.judge_input_hash = fs.judge_input_hash
                    AND fv.rubric_version = ?
                    AND fv.judge_model = ?
              )
        """
        params.extend([self.rubric_version, query_model])
        query += " ORDER BY fs.session_date, fs.session_id"

        rows = self.conn.execute(query, params).fetchall()
        return [_row_to_session_record(row) for row in rows]

    def run(
        self,
        sessions: Sequence[SessionRecord],
        *,
        jsonl_paths: dict[str, Path],
        on_progress: Callable[[ProgressEvent], None] | None = None,
    ) -> ScoringResult:
        """Score each session, persisting a verdict on success and skipping on
        per-session failure. Aborts once `consecutive_failure_limit` failures
        happen back to back.

        Raises:
            JudgeUnavailableError: The judge itself is unusable (e.g. missing
                credentials). Propagates instead of being counted as a skip,
                since no session will succeed until the caller fixes it.
        """
        state = _RunState()
        self._run_sessions(
            sessions,
            state=state,
            jsonl_paths=jsonl_paths,
            on_progress=on_progress,
            progress_total=len(sessions),
        )
        return state.result(
            remaining=max(0, len(sessions) - state.scored),
            resolved_model=self._resolved_model,
        )

    def score_window(
        self,
        *,
        window: WindowRange,
        agent_type: str | None = None,
        jsonl_paths: dict[str, Path],
        on_progress: Callable[[ProgressEvent], None] | None = None,
    ) -> ScoringResult:
        """Find and score the unscored sessions in `window`, resolving a
        floating alias to its concrete model identifier first when needed.

        The resolved identifier is only knowable from a judge call, and
        nothing in the store maps an alias back to it. So when the
        configured model is not already a concrete identifier and no call
        has resolved one yet, this scores one candidate from the alias-keyed
        upper bound to learn the resolved identifier, then re-queries the
        remainder against it before scoring the rest. A window that is
        already fully scored under the resolved identifier therefore costs
        exactly one judge call rather than zero — the price of learning what
        the alias currently points at.
        """
        upper_bound = self.find_unscored_sessions(window=window, agent_type=agent_type)
        state = _RunState()
        if not upper_bound:
            return state.result(resolved_model=self._resolved_model)

        if is_concrete_model_id(self.judge_model) or self._resolved_model is not None:
            self._run_sessions(
                upper_bound,
                state=state,
                jsonl_paths=jsonl_paths,
                on_progress=on_progress,
                progress_total=len(upper_bound),
            )
            return self._window_result(
                state,
                window=window,
                agent_type=agent_type,
            )

        for candidate in upper_bound:
            self._run_sessions(
                [candidate],
                state=state,
                jsonl_paths=jsonl_paths,
                on_progress=on_progress,
                progress_total=len(upper_bound),
            )
            if self._resolved_model is not None or state.aborted or self._budget_exhausted(state):
                break

        if self._resolved_model is None or state.aborted or self._budget_exhausted(state):
            return self._window_result(
                state,
                window=window,
                agent_type=agent_type,
            )

        remainder = [
            session
            for session in self.find_unscored_sessions(window=window, agent_type=agent_type)
            if session.session_id not in state.processed_session_ids
        ]
        self._run_sessions(
            remainder,
            state=state,
            jsonl_paths=jsonl_paths,
            on_progress=on_progress,
            progress_total=state.progress_index + len(remainder),
        )
        return self._window_result(
            state,
            window=window,
            agent_type=agent_type,
        )

    def _window_result(
        self,
        state: _RunState,
        *,
        window: WindowRange,
        agent_type: str | None,
    ) -> ScoringResult:
        remaining = len(
            self.find_unscored_sessions(
                window=window,
                agent_type=agent_type,
            )
        )
        return state.result(
            remaining=remaining,
            resolved_model=self._resolved_model,
        )

    def _run_sessions(
        self,
        sessions: Sequence[SessionRecord],
        *,
        state: _RunState,
        jsonl_paths: dict[str, Path],
        on_progress: Callable[[ProgressEvent], None] | None,
        progress_total: int,
    ) -> None:
        for session in sessions:
            if state.aborted or self._budget_exhausted(state):
                return
            if session.session_id in state.processed_session_ids:
                continue
            state.processed_session_ids.add(session.session_id)
            self._process_session(
                session,
                state=state,
                jsonl_paths=jsonl_paths,
                on_progress=on_progress,
                progress_total=progress_total,
            )

    def _process_session(
        self,
        session: SessionRecord,
        *,
        state: _RunState,
        jsonl_paths: dict[str, Path],
        on_progress: Callable[[ProgressEvent], None] | None,
        progress_total: int,
    ) -> None:
        try:
            prepared = self._prepare_session(session, jsonl_paths=jsonl_paths)
        except JudgeError as exc:
            self._record_failure(
                session,
                exc,
                state=state,
                on_progress=on_progress,
                progress_total=progress_total,
            )
            return

        claim_model = self._resolved_model or self.judge_model
        if is_concrete_model_id(claim_model) and verdict_exists(
            self.conn,
            _empty_verdict_record(
                session_id=session.session_id,
                judge_input_hash=prepared.judge_input_hash,
                rubric_version=self.rubric_version,
                judge_model=claim_model,
            ),
        ):
            return

        now = datetime.now(UTC)
        claim = ScoringClaimRecord(
            session_id=session.session_id,
            judge_input_hash=prepared.judge_input_hash,
            rubric_version=self.rubric_version,
            judge_model=claim_model,
            owner_id=self.owner_id,
            expires_at=(now + timedelta(seconds=self.claim_ttl_seconds)).isoformat(),
        )
        if not acquire_scoring_claim(self.conn, claim, now=now.isoformat()):
            diagnostic = "scoring identity is actively claimed by another run"
            state.skipped += 1
            self._emit_progress(
                session,
                verdict=None,
                error=diagnostic,
                state=state,
                on_progress=on_progress,
                progress_total=progress_total,
            )
            return

        state.attempts += 1
        try:
            verdict = validate_verdict(
                self.judge.score(prepared.transcript_view, self.rubric_version)
            )
            verdict = replace(
                verdict,
                session_id=session.session_id,
                judge_input_hash=prepared.judge_input_hash,
                rubric_version=self.rubric_version,
            )
            finalized_at = datetime.now(UTC).isoformat()
            finalize_scoring_claim(
                self.conn,
                claim=claim,
                verdict=_to_verdict_record(verdict),
                now=finalized_at,
            )
        except JudgeUnavailableError:
            raise
        except JudgeError as exc:
            self._record_failure(
                session,
                exc,
                state=state,
                on_progress=on_progress,
                progress_total=progress_total,
            )
            return
        finally:
            release_scoring_claim(self.conn, claim)

        self._resolved_model = verdict.judge_model
        state.scored += 1
        state.total_cost_usd += verdict.judge_cost_usd
        state.consecutive_failures = 0
        self._emit_progress(
            session,
            verdict=verdict,
            error=None,
            state=state,
            on_progress=on_progress,
            progress_total=progress_total,
        )

    def _prepare_session(
        self,
        session: SessionRecord,
        *,
        jsonl_paths: dict[str, Path],
    ) -> _PreparedSession:
        jsonl_path = jsonl_paths.get(session.session_id)
        if jsonl_path is None:
            raise JudgeError(f"no transcript path provided for session {session.session_id}")

        parsed = _to_parsed_session(session, events=_fetch_events(self.conn, session.session_id))
        try:
            transcript_view = build_transcript_view(parsed, jsonl_path)
        except (OSError, UnicodeError) as exc:
            raise JudgeError(
                f"failed to read transcript for {session.session_id} at {jsonl_path}"
            ) from exc
        judge_input_hash = hashlib.sha256(transcript_view.encode("utf-8")).hexdigest()
        if not set_session_judge_input_hash(
            self.conn,
            session_id=session.session_id,
            source_revision=session.source_revision,
            judge_input_hash=judge_input_hash,
        ):
            raise StaleVerdictError(
                f"session {session.session_id} changed before scoring could begin"
            )
        return _PreparedSession(
            transcript_view=transcript_view,
            judge_input_hash=judge_input_hash,
        )

    def _record_failure(
        self,
        session: SessionRecord,
        exc: JudgeError,
        *,
        state: _RunState,
        on_progress: Callable[[ProgressEvent], None] | None,
        progress_total: int,
    ) -> None:
        diagnostic = bounded_diagnostic(exc)
        logger.warning(
            "Skipping session %s: judge failed: %s",
            session.session_id,
            diagnostic,
        )
        state.skipped += 1
        state.consecutive_failures += 1
        self._emit_progress(
            session,
            verdict=None,
            error=diagnostic,
            state=state,
            on_progress=on_progress,
            progress_total=progress_total,
        )
        if state.consecutive_failures >= self.consecutive_failure_limit:
            logger.error(
                "Aborting scoring loop after %d consecutive failures",
                state.consecutive_failures,
            )
            state.aborted = True

    def _emit_progress(
        self,
        session: SessionRecord,
        *,
        verdict: Verdict | None,
        error: str | None,
        state: _RunState,
        on_progress: Callable[[ProgressEvent], None] | None,
        progress_total: int,
    ) -> None:
        if on_progress is not None:
            on_progress(
                ProgressEvent(
                    index=state.progress_index,
                    total=progress_total,
                    session=session,
                    verdict=verdict,
                    error=error,
                )
            )
        state.progress_index += 1

    def _budget_exhausted(self, state: _RunState) -> bool:
        return self.max_sessions is not None and state.attempts >= self.max_sessions


def _row_to_session_record(row: tuple[Any, ...]) -> SessionRecord:
    return SessionRecord(
        session_id=row[0],
        agent_id=row[1],
        agent_type=row[2],
        name_source=row[3],
        session_kind=row[4],
        spawn_depth=row[5],
        parent_session_id=row[6],
        spawn_tool_use_id=row[7],
        task_description=row[8],
        session_date=row[9],
        n_turns=row[10],
        n_tool_calls=row[11],
        n_reads=row[12],
        n_edits=row[13],
        n_writes=row[14],
        n_bash=row[15],
        n_files_touched=row[16],
        n_errors=row[17],
        n_permission_denials=row[18],
        n_duplicate_tool_calls=row[19],
        final_report_flagged_partial=bool(row[20]),
        duration_sec=row[21],
        input_tokens=row[22],
        output_tokens=row[23],
        cache_read_tokens=row[24],
        cache_creation_tokens=row[25],
        task_prompt_len=row[26],
        n_skills_fired=row[27],
        raw_session_id=row[28],
        source_project=row[29],
        source_revision=row[30],
        source_mtime_ns=row[31],
        source_size=row[32],
        source_content_hash=row[33],
        judge_input_hash=row[34],
        agent_definition_id=row[35],
    )


def _fetch_events(conn: sqlite3.Connection, session_id: str) -> list[ToolEventRecord]:
    rows = conn.execute(
        """
        SELECT session_id, seq, tool_name, is_error, denial_kind, ts, input_hash,
               file_path_hash, output_bytes
        FROM fact_tool_event
        WHERE session_id = ?
        ORDER BY seq
        """,
        (session_id,),
    ).fetchall()
    return [
        ToolEventRecord(
            session_id=row[0],
            seq=row[1],
            tool_name=row[2],
            is_error=bool(row[3]),
            denial_kind=row[4],
            ts=row[5],
            input_hash=row[6],
            file_path_hash=row[7],
            output_bytes=row[8],
        )
        for row in rows
    ]


def _empty_verdict_record(
    *,
    session_id: str,
    judge_input_hash: str,
    rubric_version: str,
    judge_model: str,
) -> VerdictRecord:
    return VerdictRecord(
        session_id=session_id,
        judge_input_hash=judge_input_hash,
        rubric_version=rubric_version,
        judge_model=judge_model,
        verdict_json="",
        judge_cost_usd=0.0,
        judge_input_tokens=0,
        judge_output_tokens=0,
    )


def _to_verdict_record(verdict: Verdict) -> VerdictRecord:
    return VerdictRecord(
        session_id=verdict.session_id,
        judge_input_hash=verdict.judge_input_hash,
        rubric_version=verdict.rubric_version,
        judge_model=verdict.judge_model,
        verdict_json=json.dumps(verdict.to_verdict_json()),
        judge_cost_usd=verdict.judge_cost_usd,
        judge_input_tokens=verdict.judge_input_tokens,
        judge_output_tokens=verdict.judge_output_tokens,
    )


def _to_parsed_session(record: SessionRecord, *, events: list[ToolEventRecord]) -> ParsedSession:
    """Reconstruct the `ParsedSession` fields `build_transcript_view` needs
    from a stored `fact_session` row plus its `fact_tool_event` rows.

    `ambiguous`, `first_ts`, and `fired_skills` aren't read by
    `build_transcript_view` and carry placeholder values here — they're
    name-resolution/skill-bridge concerns settled at ingest time, not
    re-derivable (or needed) from the store alone.
    """
    return ParsedSession(
        session_id=record.session_id,
        session_kind=record.session_kind or SESSION_KIND_SUBAGENT,
        agent_id=record.agent_id,
        name=record.agent_type,
        name_source=record.name_source,
        ambiguous=False,
        parent_session_id=record.parent_session_id,
        spawn_tool_use_id=record.spawn_tool_use_id,
        task_description=record.task_description,
        spawn_depth=record.spawn_depth,
        events=events,
        n_turns=record.n_turns,
        duration_sec=record.duration_sec,
        first_ts=None,
        input_tokens=record.input_tokens,
        output_tokens=record.output_tokens,
        cache_read_tokens=record.cache_read_tokens,
        cache_creation_tokens=record.cache_creation_tokens,
        fired_skills=[],
        final_report_flagged_partial=record.final_report_flagged_partial,
    )
