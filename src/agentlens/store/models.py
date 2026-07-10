from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


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


@dataclass(frozen=True)
class SessionRecord:
    """One `fact_session` row: the per-spawn deterministic grain (D1).

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


@dataclass(frozen=True)
class SkillBridgeRecord:
    """One `bridge_session_skill` row: declared/available/fired for one skill."""

    session_id: str
    skill_name: str
    declared: bool
    available: bool
    fired: bool
