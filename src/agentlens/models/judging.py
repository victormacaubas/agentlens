from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True, kw_only=True)
class JudgeResponse:
    """One response from a judge backend.

    ``resolved_model`` is read back from the envelope rather than echoed from the
    request, because an alias like ``sonnet`` floats and verdicts scored under
    different concrete models are not comparable.

    ``structured_output`` is typed as a mapping of ``object`` rather than ``Any``
    so that reading a field forces a narrowing step. Nothing in it is trusted
    until validated.
    """

    resolved_model: str
    is_error: bool
    raw_result: str | None = None
    structured_output: Mapping[str, object] | None = None
    cost_usd: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    duration_ms: int | None = None
