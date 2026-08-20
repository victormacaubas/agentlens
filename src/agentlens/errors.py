from __future__ import annotations


class WindowResolutionError(ValueError):
    """Raised when `--since`/`--from`/`--to`/`--today` cannot be resolved."""


class StoreLocationError(ValueError):
    """Raised when a resolved store path would write inside a `.claude/` tree."""


class StoreSchemaError(ValueError):
    """Raised when an existing store was built by a different schema version."""


class SessionLookupAmbiguityError(ValueError):
    """Raised when a raw session ID identifies multiple qualified sources."""


class JudgeError(ValueError):
    """Raised when a judge backend fails to produce a usable verdict."""


class JudgeTimeoutError(JudgeError):
    """Raised when a judge backend's subprocess exceeds its timeout."""


class JudgeUnavailableError(JudgeError):
    """Raised when a judge backend's dependency (e.g. the `claude` CLI) is unavailable."""


class ScoringClaimError(JudgeError):
    """Raised when scoring work cannot be finalized by the claimed owner."""


class StaleVerdictError(ScoringClaimError):
    """Raised when scored input no longer matches the session's current input."""
