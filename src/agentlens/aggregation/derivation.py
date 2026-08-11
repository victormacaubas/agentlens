"""Derive the deterministic `fact_session` grain and `bridge_session_skill`
rows from a `ParsedSession`.

`fact_session` is not a pure rollup of `fact_tool_event`: tool
counts are aggregated from `ParsedSession.events` here, while usage, turn
count, and duration are read directly off the transcript by the parser and
passed straight through.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Final

from agentlens.parser.extraction import parse_timestamp
from agentlens.parser.session import ParsedSession
from agentlens.store.models import SessionRecord, SkillBridgeRecord, ToolEventRecord

FILE_TOUCHING_TOOLS: Final[frozenset[str]] = frozenset(
    {"Read", "Edit", "Write", "MultiEdit", "NotebookEdit"}
)


def _safe_date_from_ts(ts: str | None) -> str | None:
    if ts is None:
        return None
    parsed = parse_timestamp(ts)
    return parsed.date().isoformat() if parsed is not None else None


def derive_fact_session(parsed: ParsedSession) -> SessionRecord:
    """Combine event-derived tool counts with the parser's transcript-read
    usage/turn/duration fields into one `fact_session` row.
    """

    events = parsed.events
    n_reads = sum(1 for e in events if e.tool_name == "Read")
    n_edits = sum(1 for e in events if e.tool_name == "Edit")
    n_writes = sum(1 for e in events if e.tool_name == "Write")
    n_bash = sum(1 for e in events if e.tool_name == "Bash")
    n_files_touched = len(
        {
            e.file_path_hash
            for e in events
            if e.tool_name in FILE_TOUCHING_TOOLS and e.file_path_hash is not None
        }
    )
    n_errors = sum(1 for e in events if e.is_error)
    n_permission_denials = sum(1 for e in events if e.denial_kind is not None)

    return SessionRecord(
        session_id=parsed.session_id,
        agent_id=parsed.agent_id,
        agent_type=parsed.name,
        name_source=parsed.name_source,
        session_kind=parsed.session_kind,
        spawn_depth=parsed.spawn_depth,
        parent_session_id=parsed.parent_session_id,
        spawn_tool_use_id=parsed.spawn_tool_use_id,
        task_description=parsed.task_description,
        session_date=_safe_date_from_ts(parsed.first_ts),
        n_turns=parsed.n_turns,
        n_tool_calls=len(events),
        n_reads=n_reads,
        n_edits=n_edits,
        n_writes=n_writes,
        n_bash=n_bash,
        n_files_touched=n_files_touched,
        n_errors=n_errors,
        n_permission_denials=n_permission_denials,
        n_duplicate_tool_calls=count_duplicate_tool_calls(events),
        final_report_flagged_partial=parsed.final_report_flagged_partial,
        duration_sec=parsed.duration_sec,
        input_tokens=parsed.input_tokens,
        output_tokens=parsed.output_tokens,
        cache_read_tokens=parsed.cache_read_tokens,
        cache_creation_tokens=parsed.cache_creation_tokens,
        task_prompt_len=len(parsed.task_description) if parsed.task_description else None,
        n_skills_fired=len(parsed.fired_skills),
        raw_session_id=parsed.raw_session_id,
        source_project=parsed.source_project,
        source_revision=parsed.source_revision.identity,
        source_mtime_ns=parsed.source_revision.mtime_ns,
        source_size=parsed.source_revision.size,
        source_content_hash=parsed.source_revision.content_hash,
        judge_input_hash=parsed.judge_input_hash,
        agent_definition_id=parsed.agent_definition_id,
    )


def count_duplicate_tool_calls(events: Sequence[ToolEventRecord]) -> int:
    """Session-wide count of `(tool_name, input_hash)` occurrences beyond
    the first, for each distinct pair — not consecutive-only.

    Events with no `input_hash` (e.g. tool inputs that failed to hash) do
    not participate; they can neither confirm nor rule out a duplicate.
    """
    counts: dict[tuple[str, str], int] = {}
    for event in events:
        if event.input_hash is None:
            continue
        key = (event.tool_name, event.input_hash)
        counts[key] = counts.get(key, 0) + 1
    return sum(count - 1 for count in counts.values() if count > 1)


def derive_skill_bridge(
    parsed: ParsedSession,
    *,
    declared_skills: Sequence[str] = (),
    available_skills: Iterable[str] = (),
) -> list[SkillBridgeRecord]:
    """Union of declared and fired skills — a skill can fire without
    being declared (injection), so the row set is not just declared skills.
    """
    declared_set = set(declared_skills)
    fired_set = set(parsed.fired_skills)
    available_set = set(available_skills)

    return [
        SkillBridgeRecord(
            session_id=parsed.session_id,
            skill_name=skill_name,
            declared=skill_name in declared_set,
            available=skill_name in available_set,
            fired=skill_name in fired_set,
        )
        for skill_name in sorted(declared_set | fired_set)
    ]
