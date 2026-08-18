import json
import logging
from collections.abc import Mapping
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_REPORTS_DIR = Path("reports")


def session_artifact_path(session_id: str, *, reports_dir: Path | None = None) -> Path:
    """Return the stable artifact path for ``session_id``.

    Deterministic for a given ``session_id`` and ``reports_dir``, so repeated
    calls for the same session always resolve to the same file.
    """
    directory = reports_dir if reports_dir is not None else DEFAULT_REPORTS_DIR
    return directory / f"session_{session_id}.json"


def write_session_artifact(
    document: Mapping[str, object],
    *,
    session_id: str,
    reports_dir: Path | None = None,
) -> Path:
    """Write ``document`` to the session's artifact path.

    Creates ``reports_dir`` if it does not exist and overwrites any existing
    artifact at that path, so repeated runs for the same session leave exactly
    one file rather than accumulating timestamped copies.
    """
    path = session_artifact_path(session_id, reports_dir=reports_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    logger.debug("Wrote report artifact for session %s to %s", session_id, path)
    return path
