"""The exit-code table stays complete as the taxonomy grows.

No linter can check this, so an error family added without a code would otherwise
reach users as a silent exit 1.
"""

from agentlens import errors
from agentlens.cli import EXIT_CODES, EXIT_OK, EXIT_UNEXPECTED


def _error_subclasses() -> list[type[errors.AgentlensError]]:
    found: list[type[errors.AgentlensError]] = []
    pending: list[type[errors.AgentlensError]] = list(errors.AgentlensError.__subclasses__())
    while pending:
        current = pending.pop()
        found.append(current)
        pending.extend(current.__subclasses__())
    return found


def _exit_code_for(error_class: type[errors.AgentlensError]) -> int | None:
    # Must mirror how cli resolves a raised exception, or the test proves nothing.
    for ancestor in error_class.__mro__:
        code = EXIT_CODES.get(ancestor)
        if code is not None:
            return code
    return None


def test_every_error_class_resolves_to_a_family_exit_code() -> None:
    unmapped = [
        error_class.__name__
        for error_class in _error_subclasses()
        if _exit_code_for(error_class) is None
    ]
    assert unmapped == []


def test_exit_codes_are_distinct() -> None:
    codes = list(EXIT_CODES.values())
    assert len(set(codes)) == len(codes), f"duplicate exit codes: {codes}"


def test_exit_codes_avoid_success_and_unexpected() -> None:
    assert EXIT_OK not in EXIT_CODES.values()
    assert EXIT_UNEXPECTED not in EXIT_CODES.values()
