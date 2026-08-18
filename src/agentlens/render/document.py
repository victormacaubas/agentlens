import json
from collections.abc import Mapping

from agentlens.models.protocols import Clock
from agentlens.models.session_facts import SessionFacts

SCHEMA_VERSION = 1
SCORING_STATUS_UNSCORED = "unscored"


def build_session_document(facts: SessionFacts, *, clock: Clock) -> dict[str, object]:
    """Build the JSON-serializable report document for one analyzed spawn.

    Carries a schema version, a UTC generation timestamp read from ``clock``,
    one row per qualified spawn, and an explicit unscored marker. This slice
    never runs a judge, so no score, verdict, or fix field appears anywhere in
    the result.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": clock.now().isoformat(),
        "scoring_status": SCORING_STATUS_UNSCORED,
        "spawns": [_build_spawn_row(facts)],
    }


def render_document_json(document: Mapping[str, object]) -> str:
    """Serialize a session document to indented JSON text.

    Mirrors the serialization ``render.artifact.write_session_artifact`` uses
    for the file case, so the stream and file outputs are formatted alike.
    """
    return json.dumps(document, indent=2)


def _build_spawn_row(facts: SessionFacts) -> dict[str, object]:
    session = facts.session
    identity = session.identity
    return {
        "session_id": identity.session_id,
        "source_project": identity.source_project,
        "session_kind": identity.session_kind,
        "raw_session_id": identity.raw_session_id,
        "agent_type": session.agent_type,
        "name_source": session.name_source,
        "task_description": session.task_description,
        "spawning_tool_use_id": session.spawning_tool_use_id,
        "spawn_depth": session.spawn_depth,
        "n_turns": session.n_turns,
        "n_invocations": session.n_invocations,
        "n_reads": session.n_reads,
        "n_edits": session.n_edits,
        "n_writes": session.n_writes,
        "n_bash": session.n_bash,
        "n_distinct_files": session.n_distinct_files,
        "n_errors": session.n_errors,
        "n_denials": session.n_denials,
        "n_repeated_invocations": session.n_repeated_invocations,
        "duration_ms": session.duration_ms,
        "input_tokens": session.input_tokens,
        "output_tokens": session.output_tokens,
        "cache_read_tokens": session.cache_read_tokens,
        "cache_creation_tokens": session.cache_creation_tokens,
        "unreadable_line_count": session.unreadable_line_count,
    }
