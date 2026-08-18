"""Turning one subagent transcript into ``SessionFacts``.

:func:`parse_transcript` is the package's sole entry point: derive identity
from the path, read the file once while verifying it did not change,
resolve the agent's name, and derive the session row from what was read.
This is the only place in the package where a filesystem ``OSError`` is
caught and translated, since the read itself happens here and nowhere lower.
"""

from pathlib import Path

from agentlens.errors import MalformedSourceError
from agentlens.ingest.identity import build_session_identity, derive_transcript_location
from agentlens.ingest.name_resolution import resolve_agent_type
from agentlens.ingest.reading import read_transcript
from agentlens.ingest.session import build_fact_session
from agentlens.ingest.sidecar import read_sidecar
from agentlens.ingest.tool_events import pair_tool_events
from agentlens.models.session_facts import SessionFacts


def parse_transcript(path: Path) -> SessionFacts:
    """Parse the subagent transcript at ``path`` into one session and its rows.

    An instance this function returns is always safe to persist: every case
    that would make it unsound raises instead of producing a flagged value.

    Raises:
        MalformedSourceError: ``path`` does not identify an owning subagent
            transcript, its sidecar could not be parsed, or the transcript
            yielded no usable records.
        ~agentlens.errors.SourceChangedError: The file changed while being
            read.
    """
    location = derive_transcript_location(path)
    identity = build_session_identity(location)

    try:
        contents = read_transcript(path)
    except OSError as exc:
        raise MalformedSourceError(f"could not read {path}") from exc
    if not contents.records:
        raise MalformedSourceError(f"{path} yielded no usable records")

    sidecar = read_sidecar(path)
    name_resolution = resolve_agent_type(
        sidecar_agent_type=sidecar.agent_type if sidecar is not None else None,
        raw_session_id=identity.raw_session_id,
    )
    tool_events = pair_tool_events(contents.records, session_id=identity.session_id)
    session = build_fact_session(
        identity=identity,
        revision=contents.revision,
        records=contents.records,
        tool_events=tool_events,
        sidecar=sidecar,
        name_resolution=name_resolution,
        unreadable_line_count=contents.unreadable_line_count,
    )
    return SessionFacts(session=session, tool_events=tool_events)
