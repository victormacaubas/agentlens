"""Fact row types: what the store persists, one row per tool invocation and per spawn.

Both are pure data. Deriving their field values from a transcript is ``ingest``'s
job; writing them is ``store``'s.
"""

from dataclasses import dataclass
from datetime import datetime

from agentlens.models.identity import NameSource, SessionIdentity, SourceRevision


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

    ``unreadable_line_count`` is the number of transcript lines the parser could
    not read; a session can still be sound and reported with this above zero.
    """

    identity: SessionIdentity
    revision: SourceRevision
    agent_type: str
    name_source: NameSource
    task_description: str
    spawning_tool_use_id: str | None
    spawn_depth: int
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
    duration_ms: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    unreadable_line_count: int
