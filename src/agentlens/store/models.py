from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class SourceRevision:
    """A transcript snapshot's filesystem metadata and exact byte hash."""

    mtime_ns: int
    size: int
    content_hash: str

    @property
    def identity(self) -> str:
        payload = json.dumps(
            [self.mtime_ns, self.size, self.content_hash],
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def agent_definition_key(
    *,
    agent_type: str,
    scope: str,
    source_project: str | None,
    definition_hash: str,
) -> str:
    """Derive the immutable identity of one agent-definition version."""
    payload = json.dumps(
        [agent_type, scope, source_project, definition_hash],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ToolEventRecord:
    """One `fact_tool_event` row: a paired tool_use/tool_result observation."""

    session_id: str
    seq: int
    tool_name: str
    is_error: bool
    denial_kind: str | None
    ts: str | None
    input_hash: str | None
    output_bytes: int | None
    file_path_hash: str | None = None


@dataclass(frozen=True)
class AgentDefRecord:
    """One `dim_agent` row: an agent definition scanned from `.claude/agents/**`."""

    agent_type: str
    name: str
    model: str | None
    effort: str | None
    declared_tools: Sequence[str]
    declared_skills: Sequence[str]
    definition_hash: str
    scope: str = "user"
    source_project: str | None = None
    definition_id: str | None = None

    @property
    def effective_definition_id(self) -> str:
        return self.definition_id or agent_definition_key(
            agent_type=self.agent_type,
            scope=self.scope,
            source_project=self.source_project,
            definition_hash=self.definition_hash,
        )


@dataclass(frozen=True)
class SessionRecord:
    """One `fact_session` row: the per-spawn deterministic grain.

    Event-derived counts (aggregated from `fact_tool_event`) and
    transcript-read fields (usage, turns, duration — a documented exception
    to "store `fact_tool_event`, derive the rest") are combined by
    `agentlens.aggregation.derive_fact_session`.
    """

    session_id: str
    agent_id: str | None
    agent_type: str | None
    name_source: str | None
    session_kind: str
    spawn_depth: int | None
    parent_session_id: str | None
    spawn_tool_use_id: str | None
    task_description: str | None
    session_date: str | None
    n_turns: int
    n_tool_calls: int
    n_reads: int
    n_edits: int
    n_writes: int
    n_bash: int
    n_files_touched: int
    n_errors: int
    n_permission_denials: int
    n_duplicate_tool_calls: int
    final_report_flagged_partial: bool
    duration_sec: float
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    task_prompt_len: int | None
    n_skills_fired: int
    raw_session_id: str = ""
    source_project: str = ""
    source_revision: str = ""
    source_mtime_ns: int = 0
    source_size: int = 0
    source_content_hash: str = ""
    judge_input_hash: str | None = None
    agent_definition_id: str | None = None


@dataclass(frozen=True)
class SkillBridgeRecord:
    """One `bridge_session_skill` row: declared/available/fired for one skill."""

    session_id: str
    skill_name: str
    declared: bool
    available: bool
    fired: bool


@dataclass(frozen=True)
class ScoringClaimRecord:
    """Ownership of one pending judge call."""

    session_id: str
    judge_input_hash: str
    rubric_version: str
    judge_model: str
    owner_id: str
    expires_at: str


@dataclass(frozen=True)
class VerdictRecord:
    """One input-bound verdict ready for atomic claim finalization."""

    session_id: str
    judge_input_hash: str
    rubric_version: str
    judge_model: str
    verdict_json: str
    judge_cost_usd: float
    judge_input_tokens: int
    judge_output_tokens: int
