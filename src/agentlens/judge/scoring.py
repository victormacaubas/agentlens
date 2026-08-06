"""Scoring loop: finds subagent sessions lacking a verdict for the current
rubric and judge model, scores them via a `Judge` backend, and persists
verdicts into `fact_verdict`.

`judge_model` identity is the backend's resolved concrete model, not
necessarily the alias a caller configures the loop with — see
`ScoringLoop.score_window` for how the loop resolves one against the other.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Final

from agentlens.errors import JudgeError, JudgeUnavailableError
from agentlens.judge.protocol import Judge, Verdict
from agentlens.judge.transcript_view import build_transcript_view
from agentlens.parser.session import SESSION_KIND_SUBAGENT, ParsedSession
from agentlens.reporting.date_window import WindowRange
from agentlens.store.models import SessionRecord, ToolEventRecord

logger = logging.getLogger(__name__)

DEFAULT_CONSECUTIVE_FAILURE_LIMIT: Final[int] = 3
KNOWN_MODEL_ALIASES: Final[frozenset[str]] = frozenset({"sonnet", "opus", "haiku", "opusplan"})


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
    """Summary of one `ScoringLoop.run()` invocation."""

    scored: int
    skipped: int
    total_cost_usd: float
    aborted: bool


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
    ) -> None:
        self.judge = judge
        self.conn = conn
        self.rubric_version = rubric_version
        self.judge_model = judge_model
        self.max_sessions = max_sessions
        self.consecutive_failure_limit = consecutive_failure_limit

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
        resolved_model = self.judge.resolved_model
        query_model = resolved_model if resolved_model is not None else self.judge_model
        query = """
            SELECT fs.session_id, fs.agent_id, fs.agent_type, fs.name_source, fs.session_kind,
                   fs.spawn_depth, fs.parent_session_id, fs.spawn_tool_use_id,
                   fs.task_description, fs.session_date, fs.n_turns, fs.n_tool_calls,
                   fs.n_reads, fs.n_edits, fs.n_writes, fs.n_bash, fs.n_files_touched,
                   fs.n_errors, fs.n_permission_denials, fs.n_duplicate_tool_calls,
                   fs.final_report_flagged_partial, fs.duration_sec, fs.input_tokens,
                   fs.output_tokens, fs.cache_read_tokens, fs.cache_creation_tokens,
                   fs.task_prompt_len, fs.n_skills_fired
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
                    AND fv.rubric_version = ?
                    AND fv.judge_model = ?
              )
        """
        params.extend([self.rubric_version, query_model])

        if self.max_sessions is not None:
            query += " LIMIT ?"
            params.append(self.max_sessions)

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
        scored = 0
        skipped = 0
        total_cost_usd = 0.0
        consecutive_failures = 0
        aborted = False
        total = len(sessions)

        for idx, session in enumerate(sessions):
            try:
                verdict = self._score_session(session, jsonl_paths=jsonl_paths)
            except JudgeUnavailableError:
                # Must precede the JudgeError clause below, which it subclasses.
                raise
            except JudgeError as exc:
                logger.warning(
                    "Skipping session %s: judge failed", session.session_id, exc_info=True
                )
                skipped += 1
                consecutive_failures += 1
                if on_progress:
                    on_progress(ProgressEvent(
                        index=idx, total=total, session=session,
                        verdict=None, error=str(exc),
                    ))
                if consecutive_failures >= self.consecutive_failure_limit:
                    logger.error(
                        "Aborting scoring loop after %d consecutive failures",
                        consecutive_failures,
                    )
                    aborted = True
                    break
                continue

            self.persist_verdict(verdict)
            scored += 1
            total_cost_usd += verdict.judge_cost_usd
            consecutive_failures = 0
            if on_progress:
                on_progress(ProgressEvent(
                    index=idx, total=total, session=session,
                    verdict=verdict, error=None,
                ))

        return ScoringResult(
            scored=scored, skipped=skipped, total_cost_usd=total_cost_usd, aborted=aborted
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
        if not upper_bound:
            return ScoringResult(scored=0, skipped=0, total_cost_usd=0.0, aborted=False)

        if is_concrete_model_id(self.judge_model) or self.judge.resolved_model is not None:
            return self.run(upper_bound, jsonl_paths=jsonl_paths, on_progress=on_progress)

        resolution_candidate, *_ = upper_bound
        resolution_result = self.run(
            [resolution_candidate], jsonl_paths=jsonl_paths, on_progress=on_progress
        )
        if self.judge.resolved_model is None:
            # The one resolution attempt failed; nothing further can be
            # resolved this run, so report what happened and stop here.
            return resolution_result

        remainder = self.find_unscored_sessions(window=window, agent_type=agent_type)
        remainder_result = self.run(remainder, jsonl_paths=jsonl_paths, on_progress=on_progress)

        return ScoringResult(
            scored=resolution_result.scored + remainder_result.scored,
            skipped=resolution_result.skipped + remainder_result.skipped,
            total_cost_usd=resolution_result.total_cost_usd + remainder_result.total_cost_usd,
            aborted=resolution_result.aborted or remainder_result.aborted,
        )

    def _score_session(
        self, session: SessionRecord, *, jsonl_paths: dict[str, Path]
    ) -> Verdict:
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
        verdict = self.judge.score(transcript_view, self.rubric_version)
        # judge_model is not overwritten: it is the backend's resolved
        # concrete identifier, not the loop's (possibly-floating) configured
        # value, and only the backend knows which one it actually used.
        return replace(
            verdict,
            session_id=session.session_id,
            rubric_version=self.rubric_version,
        )

    def persist_verdict(self, verdict: Verdict) -> None:
        """Upsert `verdict` into `fact_verdict`, keyed on
        `(session_id, rubric_version, judge_model)`.
        """
        with self.conn:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO fact_verdict
                    (session_id, rubric_version, judge_model, verdict_json,
                     judge_cost_usd, judge_input_tokens, judge_output_tokens)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    verdict.session_id,
                    verdict.rubric_version,
                    verdict.judge_model,
                    json.dumps(verdict.to_verdict_json()),
                    verdict.judge_cost_usd,
                    verdict.judge_input_tokens,
                    verdict.judge_output_tokens,
                ),
            )


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
    )


def _fetch_events(conn: sqlite3.Connection, session_id: str) -> list[ToolEventRecord]:
    rows = conn.execute(
        """
        SELECT session_id, seq, tool_name, is_error, denial_kind, ts, input_hash, output_bytes
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
            output_bytes=row[7],
        )
        for row in rows
    ]


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
