"""Fact row types: what the store persists, one row per tool invocation, spawn, and verdict.

All three are pure data. Deriving a measured row's field values from a transcript is
``ingest``'s job; deriving a verdict's is ``judge``'s; writing any of them is
``store``'s.
"""

from dataclasses import dataclass
from datetime import datetime

from agentlens.models.identity import NameSource, SessionIdentity, SourceRevision
from agentlens.models.judging import Verdict


@dataclass(frozen=True, slots=True, kw_only=True)
class FactToolEvent:
    """One tool invocation and its matching result, ordered within its session.

    ``file_identity`` is ``None`` when the tool did not act on a file. When the
    invocation never received a result, the result fields are empty: ``is_error``
    is ``False``, ``denial_kind`` is ``None``, and ``result_size`` is ``None``,
    the same shape as a result that simply succeeded with nothing to report.
    """

    session_id: str
    ordinal: int
    tool_name: str
    input_fingerprint: str
    file_identity: str | None
    timestamp: datetime
    is_error: bool
    denial_kind: str | None
    result_size: int | None


@dataclass(frozen=True, slots=True, kw_only=True)
class FactSession:
    """One agent run: one spawn, never one agent type.

    ``identity`` and ``revision`` are embedded rather than flattened, so the
    session-identity and snapshot-soundness concepts stay defined in exactly one
    place. The remaining fields split into two provenances: the counters and
    token totals are aggregations over this session's tool-invocation rows,
    while ``agent_type``, ``task_description``, ``spawning_tool_use_id``, and
    ``spawn_depth`` come from the metadata sidecar (or its fallback) and are not
    derivable from any invocation.

    ``agent_id`` is the raw subagent identifier read off the transcript.
    ``agent_definition_id`` is the effective agent-definition identity the
    spawn was bound to, or ``None`` when no historically applicable
    definition could be proven. ``n_skills_fired`` is unresolved in this
    slice (always ``0``); later work binds it to the skill bridge.
    ``parent_session_id`` is the qualified key of the spawning main session, or
    ``None`` when a parent could not be derived. ``started_at`` is the earliest
    usable transcript timestamp, and ``task_prompt_len`` is ``task_description``'s
    character length.

    ``derivation_fingerprint`` and ``derivation_observed_mtime_ns`` are distinct
    from ``revision``: they cover every input that shaped this row, not only the
    transcript, so a sidecar or other context change can be detected even when
    the transcript's own content is unchanged.

    ``unreadable_line_count`` is the number of transcript lines the parser could
    not read; a session can still be sound and reported with this above zero.
    """

    identity: SessionIdentity
    revision: SourceRevision
    agent_id: str
    agent_definition_id: str | None
    parent_session_id: str | None
    agent_type: str
    name_source: NameSource
    task_description: str
    task_prompt_len: int
    spawning_tool_use_id: str | None
    spawn_depth: int
    started_at: datetime
    n_turns: int
    n_invocations: int
    n_reads: int
    n_edits: int
    n_writes: int
    n_bash: int
    n_distinct_files: int
    n_errors: int
    n_denials: int
    n_repeated_invocations: int
    n_skills_fired: int
    duration_ms: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    unreadable_line_count: int
    derivation_fingerprint: str
    derivation_observed_mtime_ns: int


@dataclass(frozen=True, slots=True, kw_only=True)
class FactVerdict:
    """One modeled verdict for one spawn, under one input, rubric, and judge model.

    The four identity fields are the natural key. ``judge_model`` is the concrete
    identifier read back from the response envelope, never the alias that was
    requested, because verdicts scored under different concrete models are not
    comparable. ``judge_input_hash`` covers the exact prepared prompt, so a
    projection that elided different content hashes differently.

    ``verdict`` carries the rubric output and its own provenance split. The cost
    fields are agentlens's own spend, which is the only spend reported in currency;
    the analyzed spawn's token usage lives on :class:`FactSession` and is never
    dollarized.

    ``scored_at`` is stamped from the injected clock rather than left to be inferred.
    Unlike a measured row, a verdict cannot be regenerated for free, so a column
    added later would cost real money to backfill.
    """

    session_id: str
    judge_input_hash: str
    rubric_version: str
    judge_model: str
    verdict: Verdict
    judge_cost_usd: float
    judge_input_tokens: int
    judge_output_tokens: int
    scored_at: datetime
