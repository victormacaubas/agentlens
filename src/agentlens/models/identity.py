from dataclasses import dataclass
from enum import StrEnum


class SessionKind(StrEnum):
    """Which kind of transcript a session came from.

    Part of the qualified key, because the same raw ID can appear as both a main
    session and a subagent run.
    """

    MAIN = "main"
    SUBAGENT = "subagent"


class NameSource(StrEnum):
    """Which link in the name-resolution chain supplied ``agent_type``.

    Ordered authoritative first. ``AGENT_ID_HASH`` is the last resort that keeps
    a session from being dropped; ``AMBIGUOUS`` records that two links disagreed
    and the result should not be trusted for grouping.
    """

    META_JSON = "meta_json"
    ATTRIBUTION_AGENT = "attribution_agent"
    PARENT_TASK = "parent_task"
    AGENT_ID_HASH = "agent_id_hash"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceRevision:
    """The observable state of a source file, used to judge snapshot soundness.

    Captured before a read and verified after it. A grain may only be replaced by
    a snapshot whose revision proves the file did not change mid-read and is not
    older than what the store already holds.
    """

    mtime_ns: int
    size: int
    content_hash: str


@dataclass(frozen=True, slots=True, kw_only=True)
class SessionIdentity:
    """One agent run: one spawn, never one agent type.

    ``session_id`` is the SHA-256 of the other three fields. The components are
    retained because the derived key is not human-readable, so every display
    path and error message needs the raw tuple.
    """

    session_id: str
    source_project: str
    session_kind: SessionKind
    raw_session_id: str


@dataclass(frozen=True, slots=True, kw_only=True)
class VerdictIdentity:
    """One scored identity, and the cache key for judge output.

    ``judge_model`` is the concrete identifier resolved from the response
    envelope, never the alias typed at the CLI, so two runs that both said
    ``sonnet`` are only comparable if they actually reached the same model.
    """

    session_id: str
    judge_input_hash: str
    rubric_version: str
    judge_model: str
