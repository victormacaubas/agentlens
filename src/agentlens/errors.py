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
    """The judge responded, but it reported an error or failed validation.

    ``cost_usd``, ``input_tokens``, and ``output_tokens`` carry whatever a
    completed call had already spent before this error was raised, so that
    spend is not lost when the call propagates past the point where it was
    known. They default to zero, which stands for both "no call happened"
    and "the call reported no cost" -- there is no spend to carry in either
    case, so the two are not distinguished here.
    """

    def __init__(
        self,
        message: str,
        *,
        cost_usd: float = 0.0,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> None:
        super().__init__(message)
        self.cost_usd = cost_usd
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
