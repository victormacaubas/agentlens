from collections.abc import Mapping

from agentlens.errors import AgentlensError, ConfigError, JudgeError, SourceError, StoreError

EXIT_OK = 0
EXIT_UNEXPECTED = 1
EXIT_CODES: Mapping[type[AgentlensError], int] = {
    ConfigError: 2,
    SourceError: 3,
    StoreError: 4,
    JudgeError: 5,
}


def exit_code_for(exc: AgentlensError) -> int:
    """Return the public exit code for an exception in the error taxonomy."""
    return exit_code_for_type(type(exc))


def exit_code_for_type(error_type: type[AgentlensError]) -> int:
    """Return the public exit code for an error class in the taxonomy."""
    for ancestor in error_type.__mro__:
        code = EXIT_CODES.get(ancestor)
        if code is not None:
            return code
    return EXIT_UNEXPECTED
