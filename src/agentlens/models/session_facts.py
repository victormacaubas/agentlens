"""One session's facts: the row and its invocations, moved between packages.

Produced by ``ingest`` and returned by ``store``'s read, which is what lets those
two packages exchange a session without importing each other: both depend on this
type, neither on the other. ``render`` consumes the same shape.
"""

from dataclasses import dataclass

from agentlens.models.facts import FactSession, FactToolEvent
from agentlens.models.skill_signals import SessionSkillSignal


@dataclass(frozen=True, slots=True, kw_only=True)
class SessionFacts:
    """One session row, its ordered tool-invocation rows, and its skill bridge rows.

    An instance is always safe to persist. A snapshot that changed mid-read,
    yielded no usable records, or carried no derivable identity raises a
    ``SourceError`` at the point of detection instead of producing an instance
    that callers must remember not to write.

    The count of lines the parser could not read lives on ``session``, since a
    sound parse can still report it above zero. ``skill_signals`` holds the
    session-skill bridge rows at ``(session_id, skill_name)`` grain.
    """

    session: FactSession
    tool_events: tuple[FactToolEvent, ...]
    skill_signals: tuple[SessionSkillSignal, ...]
