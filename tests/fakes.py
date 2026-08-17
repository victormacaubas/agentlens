"""One fake per Protocol in ``agentlens.models.protocols``.

Tests inject these instead of patching, so a test breaks when a contract changes
rather than when an import moves. A fake is written in the same commit as the
Protocol it satisfies, which is what gives that Protocol a second implementation.
"""
