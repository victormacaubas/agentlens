from __future__ import annotations


class WindowResolutionError(ValueError):
    """Raised when `--since`/`--from`/`--to`/`--today` cannot be resolved."""


class StoreLocationError(ValueError):
    """Raised when a resolved store path would write inside a `.claude/` tree."""
