"""Command-line entry point, exit-code mapping, and composition root.

The only module that constructs concrete implementations of the seams in
``agentlens.models.protocols``, so this is the one place to read to learn what
the program is wired from. Nothing below it builds its own collaborators.
"""

from agentlens.errors import (
    AgentlensError,
    ConfigError,
    JudgeError,
    SourceError,
    StoreError,
)

EXIT_OK = 0
EXIT_UNEXPECTED = 1  # escaped the taxonomy: a bug, not a handled condition

EXIT_CODES: dict[type[AgentlensError], int] = {
    ConfigError: 2,
    SourceError: 3,
    StoreError: 4,
    JudgeError: 5,
}
"""Process exit code per error family.

Keyed by family base class rather than by concrete exception, so a new subclass
inherits its family's code. Resolution walks ``type(exc).__mro__`` and takes the
first match.

These numbers are a public contract: scripts branch on them, so renumbering is a
breaking change.
"""
