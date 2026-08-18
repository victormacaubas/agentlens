"""Streaming a transcript file once: soundness and raw records together.

Soundness is stat, read, stat, compare: the file's modification time and size
are captured before the read and compared against the same stat taken
immediately after it, so a file appended to mid-read is caught rather than
half-ingested. The content hash is computed incrementally in the same pass,
so the whole file is never held in memory at once, which matters because
transcript size is unbounded.
"""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from agentlens.errors import SourceChangedError
from agentlens.ingest.records import JsonRecord
from agentlens.models.identity import SourceRevision


@dataclass(frozen=True, slots=True, kw_only=True)
class TranscriptContents:
    """The result of one streaming pass over a transcript file."""

    revision: SourceRevision
    records: tuple[JsonRecord, ...]
    unreadable_line_count: int


def read_transcript(path: Path) -> TranscriptContents:
    """Stream ``path`` line by line, hashing its content as it is read.

    Lines that are not valid JSON, or that decode to something other than a
    JSON object, are skipped and counted rather than aborting the read.

    Raises:
        SourceChangedError: The file's size or modification time differs
            between the stat taken before the read and the one taken
            immediately after it, meaning the file was written to while
            being read.
        OSError: The file could not be opened or statted.
    """
    stat_before = path.stat()
    hasher = hashlib.sha256()
    records: list[JsonRecord] = []
    unreadable_line_count = 0
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            hasher.update(line.encode("utf-8"))
            stripped = line.strip()
            if not stripped:
                continue
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                unreadable_line_count += 1
                continue
            if isinstance(parsed, dict):
                records.append(parsed)
            else:
                unreadable_line_count += 1
    stat_after = path.stat()
    if (stat_before.st_mtime_ns, stat_before.st_size) != (
        stat_after.st_mtime_ns,
        stat_after.st_size,
    ):
        raise SourceChangedError(f"{path} changed while being read")

    revision = SourceRevision(
        mtime_ns=stat_after.st_mtime_ns,
        size=stat_after.st_size,
        content_hash=hasher.hexdigest(),
    )
    return TranscriptContents(
        revision=revision, records=tuple(records), unreadable_line_count=unreadable_line_count
    )
