"""Discovering every subagent source under a Claude projects tree.

A subagent transcript always sits at
``<projects_root>/<encoded-project>/<session-uuid>/subagents/agent-*.jsonl``.
A project-level main-session transcript, a plain ``<uuid>.jsonl`` sitting
directly under a project directory, never matches this shape and is
therefore never returned: location alone is sufficient to exclude it.

Discovery is also where every source's path relationships get interpreted,
once, into a :class:`~agentlens.ingest.identity.SubagentSourceBundle`:
parsing consumes that bundle rather than re-deriving the transcript's
location, its sidecar relationship, or its parent identity for itself.
"""

from pathlib import Path

from agentlens.errors import MalformedSourceError
from agentlens.ingest.identity import SubagentSourceBundle, build_subagent_source_bundle

_SUBAGENT_TRANSCRIPT_GLOB = "*/*/subagents/agent-*.jsonl"


def discover_subagent_sources(projects_root: Path) -> tuple[SubagentSourceBundle, ...]:
    """Return every subagent source bundle under ``projects_root``.

    Returns an empty tuple when ``projects_root`` does not exist. Results are
    ordered by the transcript's resolved path rather than filesystem order,
    so a report built from the same tree is reproducible from one run to the
    next.

    Raises:
        MalformedSourceError: The tree could not be scanned, including a
            symlink loop.
    """
    if not projects_root.is_dir():
        return ()
    try:
        matches = list(projects_root.glob(_SUBAGENT_TRANSCRIPT_GLOB))
    except OSError as exc:
        raise MalformedSourceError(f"could not scan {projects_root}") from exc
    resolved_paths = sorted((path.resolve() for path in matches), key=str)
    return tuple(build_subagent_source_bundle(path) for path in resolved_paths)
