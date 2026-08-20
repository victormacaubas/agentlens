import logging
from collections.abc import Mapping
from pathlib import Path

from agentlens.models.windows import WindowSelector
from agentlens.render.document import render_document_json
from agentlens.utils.hashing import canonical_json_fingerprint

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
    path.write_text(render_document_json(document), encoding="utf-8")
    logger.debug("Wrote report artifact for session %s to %s", session_id, path)
    return path


def report_artifact_path(
    *, selector: WindowSelector, agent_filter: str | None, reports_dir: Path | None = None
) -> Path:
    """Return the stable artifact path for one report scope.

    Deterministic for a given ``selector`` and ``agent_filter``: the same
    scope always resolves to the same file, so repeated reports overwrite
    rather than accumulate. The filename is a canonical-JSON fingerprint of
    the normalized selector fields and the filter rather than their raw
    text, so unsanitized user input never reaches the filesystem path.
    """
    directory = reports_dir if reports_dir is not None else DEFAULT_REPORTS_DIR
    fingerprint = _report_scope_fingerprint(selector=selector, agent_filter=agent_filter)
    return directory / f"report_{fingerprint}.json"


def write_report_artifact(
    document: Mapping[str, object],
    *,
    selector: WindowSelector,
    agent_filter: str | None,
    reports_dir: Path | None = None,
) -> Path:
    """Write ``document`` to its scope's stable artifact path.

    Creates ``reports_dir`` if it does not exist and overwrites any existing
    artifact for the same scope, so repeated reports for the same selector
    and agent filter leave exactly one current file.
    """
    path = report_artifact_path(
        selector=selector, agent_filter=agent_filter, reports_dir=reports_dir
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_document_json(document), encoding="utf-8")
    logger.debug("Wrote report artifact to %s", path)
    return path


def _report_scope_fingerprint(*, selector: WindowSelector, agent_filter: str | None) -> str:
    scope = {
        "since_duration": selector.since_duration,
        "named_window": selector.named_window,
        "range_from": selector.range_from,
        "range_to": selector.range_to,
        "agent_filter": agent_filter,
    }
    return canonical_json_fingerprint(scope)
