class AgentlensError(Exception):
    """Base class for every error agentlens raises deliberately."""


class ConfigError(AgentlensError):
    """Unusable invocation: bad flags, bad configuration, or an ambiguous cohort.

    Includes the case where a report window spans more than one concrete judge
    model and ``--judge-model`` was not supplied to pick one.
    """


class SourceError(AgentlensError):
    """A session source under ``.claude/`` could not be read soundly."""


class MalformedSourceError(SourceError):
    """A transcript or its ``.meta.json`` sidecar could not be parsed."""


class SourceChangedError(SourceError):
    """The file changed while being read, so the snapshot cannot be trusted."""


class SessionNotFoundError(SourceError):
    """No session on disk matches the requested identity."""


class StoreError(AgentlensError):
    """The store could not be read or written. Translates ``sqlite3.Error``."""


class JudgeError(AgentlensError):
    """The LLM judge could not produce a usable verdict."""


class JudgeUnavailableError(JudgeError):
    """The backend cannot be reached: no ``claude`` CLI, or not authenticated."""


class JudgeResponseError(JudgeError):
    """The judge responded, but it reported an error or failed validation."""
