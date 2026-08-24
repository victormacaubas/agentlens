"""The bounded projection of one spawn's transcript that a judge is rendered from.

``SpawnNarrative`` carries the task prompt, every assistant text message, and
every tool event ``ingest`` found, in the order the transcript carried them.
It does no capping or truncation of its own: bounding the projection to a
byte budget and marking what was elided belongs to ``judge``, the package
that renders this into a prepared prompt.
"""

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True, kw_only=True)
class ToolNarrativeEvent:
    """One tool invocation as the judge will see it: what was asked, and its outcome.

    ``tool_input`` is carried in full, not fingerprinted, because judging
    honesty and scope adherence needs to see what the tool was actually
    asked to do. An invocation with no matching result by end of transcript
    still appears, with ``is_error`` false and ``denial_kind`` unset, the
    same shape as one that simply succeeded with nothing to report.
    """

    tool_name: str
    tool_input: Mapping[str, object]
    is_error: bool
    denial_kind: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class SpawnNarrative:
    """The task prompt, ordered assistant text, and ordered tool sequence for one spawn.

    ``messages`` holds one entry per logical turn that carried assistant
    text, in transcript order; a turn with no text block contributes no
    entry rather than an empty one. ``tool_events`` holds one entry per tool
    invocation, ordered by when it was issued.
    """

    task_prompt: str
    messages: tuple[str, ...]
    tool_events: tuple[ToolNarrativeEvent, ...]
