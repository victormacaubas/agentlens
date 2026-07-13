from __future__ import annotations


class WindowResolutionError(ValueError):
    """Raised when `--since`/`--from`/`--to`/`--today` cannot be resolved."""


class StoreLocationError(ValueError):
    """Raised when a resolved store path would write inside a `.claude/` tree."""


class JudgeError(ValueError):
    """Raised when a judge backend fails to produce a usable verdict."""


class JudgeTimeoutError(JudgeError):
    """Raised when a judge backend's subprocess exceeds its timeout."""


class JudgeUnavailableError(JudgeError):
    """Raised when a judge backend's dependency (e.g. the `claude` CLI) is unavailable."""
