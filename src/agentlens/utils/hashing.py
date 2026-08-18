import hashlib
import json
import os
from pathlib import Path

_CANONICAL_JSON_SEPARATORS = (",", ":")


def hash_text(text: str) -> str:
    """Return the SHA-256 hex digest of ``text``, encoded as UTF-8."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_json_fingerprint(value: object) -> str:
    """Hash ``value`` as canonical JSON: sorted keys, no insignificant whitespace.

    Two values that differ only in key order or in incidental whitespace
    fingerprint identically, so repeated-call detection over tool arguments is
    not defeated by key ordering.
    """
    canonical = json.dumps(value, sort_keys=True, separators=_CANONICAL_JSON_SEPARATORS)
    return hash_text(canonical)


def normalize_path(path: str | Path) -> str:
    """Return the normalized absolute form of ``path``, without touching disk."""
    expanded = Path(path).expanduser()
    return os.path.normpath(str(expanded.absolute()))


def file_identity(path: str | Path) -> str:
    """Hash the normalized absolute path only.

    Independent of any offset, range, or replacement text a caller's tool input
    might also carry alongside the path, so repeated reads of different regions
    of one file are not counted as distinct files.
    """
    return hash_text(normalize_path(path))
