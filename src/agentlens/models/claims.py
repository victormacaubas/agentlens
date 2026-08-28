"""Identity and transient coordination types for reusable verdicts.

``CLAIM_LEASE_MARGIN_S`` extends the judge wall-clock timeout when callers
derive a claim expiry, leaving time to start and finalize the work.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Final

CLAIM_LEASE_MARGIN_S: Final = 30.0


@dataclass(frozen=True, slots=True, kw_only=True)
class VerdictClaimIdentity:
    """The key a scorer coordinates on before the judge has answered.

    Shares three components with :class:`~agentlens.models.identity.VerdictIdentity`
    and deliberately differs on the fourth. A claim is taken before the call, so the
    only model string that exists yet is the one the caller asked for, which may be a
    floating alias; a verdict is keyed on the concrete identifier the envelope reports.
    Naming this field ``requested_model`` is what keeps the two from being read as each
    other.
    """

    session_id: str
    judge_input_hash: str
    rubric_version: str
    requested_model: str


@dataclass(frozen=True, slots=True, kw_only=True)
class VerdictClaim:
    """A transient owner-scoped claim over one requested-model identity."""

    identity: VerdictClaimIdentity
    owner: str
    expires_at: datetime
