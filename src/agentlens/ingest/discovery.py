"""Discovering every subagent transcript under a Claude projects tree.

A subagent transcript always sits at
``<projects_root>/<encoded-project>/<session-uuid>/subagents/agent-*.jsonl``.
Its optional ``.meta.json`` sidecar is read later, by
:func:`agentlens.ingest.transcript.parse_transcript`, from the same
basename — discovery itself only locates the transcript. A project-level
main-session transcript, a plain ``<uuid>.jsonl`` sitting directly under a
project directory, never matches this shape and is therefore never returned:
location alone is sufficient to exclude it.
"""

from pathlib import Path

from agentlens.errors import MalformedSourceError

_SUBAGENT_TRANSCRIPT_GLOB = "*/*/subagents/agent-*.jsonl"


def discover_subagent_sources(projects_root: Path) -> tuple[Path, ...]:
    """Return every subagent transcript path under ``projects_root``.

    Returns an empty tuple when ``projects_root`` does not exist. Results are
    sorted by resolved path rather than filesystem order, so a report built
    from the same tree is reproducible from one run to the next.

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
    return tuple(sorted((path.resolve() for path in matches), key=str))
