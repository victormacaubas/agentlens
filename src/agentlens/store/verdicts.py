"""SQL over modeled judge output and the claims that coordinate buying it:
``fact_verdict`` and ``verdict_claim``.

The two grains are keyed alike on session, judge-input hash, and rubric version,
and deliberately differ on the fourth component. A verdict carries the concrete
``judge_model`` read back from the response envelope. A claim is taken before the
call, when the only model string in existence is the ``requested_model`` the
caller asked for, so it cannot carry a concrete identifier.
"""

import sqlite3
from datetime import datetime

from agentlens.models.claims import VerdictClaim, VerdictClaimIdentity
from agentlens.models.facts import FactVerdict
from agentlens.models.identity import VerdictIdentity
from agentlens.store.outcomes import ClaimOutcome
from agentlens.store.rows import (
    fact_verdict_to_row,
    row_to_fact_verdict,
    row_to_verdict_claim,
    verdict_claim_to_row,
)
from agentlens.store.schema import FACT_VERDICT_COLUMN_NAMES, VERDICT_CLAIM_COLUMN_NAMES

_FACT_VERDICT_CONFLICT_TARGET = "session_id, judge_input_hash, rubric_version, judge_model"
_FACT_VERDICT_NATURAL_KEY_COLUMNS = (
    "session_id",
    "judge_input_hash",
    "rubric_version",
    "judge_model",
)

_FACT_VERDICT_COLUMN_LIST = ", ".join(FACT_VERDICT_COLUMN_NAMES)
_FACT_VERDICT_PLACEHOLDERS = ", ".join(["?"] * len(FACT_VERDICT_COLUMN_NAMES))
_FACT_VERDICT_UPDATE_ASSIGNMENTS = ",\n    ".join(
    f"{name} = excluded.{name}"
    for name in FACT_VERDICT_COLUMN_NAMES
    if name not in _FACT_VERDICT_NATURAL_KEY_COLUMNS
)

_UPSERT_VERDICT_SQL = f"""
INSERT INTO fact_verdict (
    {_FACT_VERDICT_COLUMN_LIST}
) VALUES ({_FACT_VERDICT_PLACEHOLDERS})
ON CONFLICT({_FACT_VERDICT_CONFLICT_TARGET}) DO UPDATE SET
    {_FACT_VERDICT_UPDATE_ASSIGNMENTS}
"""  # noqa: S608

_SELECT_VERDICTS_FOR_SESSION_SQL = f"""
SELECT
    {_FACT_VERDICT_COLUMN_LIST}
FROM fact_verdict
WHERE session_id = ?
ORDER BY judge_input_hash, rubric_version, judge_model
"""  # noqa: S608

_SELECT_VERDICT_SQL = f"""
SELECT
    {_FACT_VERDICT_COLUMN_LIST}
FROM fact_verdict
WHERE session_id = ?
  AND judge_input_hash = ?
  AND rubric_version = ?
  AND judge_model = ?
"""  # noqa: S608

_VERDICT_CLAIM_CONFLICT_TARGET = "session_id, judge_input_hash, rubric_version, requested_model"
_VERDICT_CLAIM_COLUMN_LIST = ", ".join(VERDICT_CLAIM_COLUMN_NAMES)
_VERDICT_CLAIM_PLACEHOLDERS = ", ".join(["?"] * len(VERDICT_CLAIM_COLUMN_NAMES))

_UPSERT_VERDICT_CLAIM_SQL = f"""
INSERT INTO verdict_claim (
    {_VERDICT_CLAIM_COLUMN_LIST}
) VALUES ({_VERDICT_CLAIM_PLACEHOLDERS})
ON CONFLICT({_VERDICT_CLAIM_CONFLICT_TARGET}) DO UPDATE SET
    owner = excluded.owner,
    expires_at = excluded.expires_at
"""  # noqa: S608

_SELECT_VERDICT_CLAIM_SQL = f"""
SELECT
    {_VERDICT_CLAIM_COLUMN_LIST}
FROM verdict_claim
WHERE session_id = ?
  AND judge_input_hash = ?
  AND rubric_version = ?
  AND requested_model = ?
"""  # noqa: S608

_DELETE_VERDICT_CLAIM_SQL = """
DELETE FROM verdict_claim
WHERE session_id = ?
  AND judge_input_hash = ?
  AND rubric_version = ?
  AND requested_model = ?
"""


def upsert_verdict(connection: sqlite3.Connection, verdict: FactVerdict) -> None:
    """Write ``verdict``, replacing any row already stored under its natural key.

    The judge is nondeterministic, so a conflict on the natural key of
    session, judge-input hash, rubric version, and resolved model always
    replaces the stored row unconditionally; there is no staleness
    comparison and nothing to report back.
    """
    with connection:
        connection.execute(_UPSERT_VERDICT_SQL, fact_verdict_to_row(verdict))


def finalize_verdict(
    connection: sqlite3.Connection,
    verdict: FactVerdict,
    *,
    claim_identity: VerdictClaimIdentity,
) -> None:
    """Store ``verdict`` and release ``claim_identity`` in one transaction.

    One transaction rather than two calls, so a crash between them cannot
    leave a stored verdict guarded by a claim that outlives it, or release a
    claim for work that was never recorded.
    """
    with connection:
        connection.execute("BEGIN")
        connection.execute(_UPSERT_VERDICT_SQL, fact_verdict_to_row(verdict))
        connection.execute(_DELETE_VERDICT_CLAIM_SQL, _claim_identity_values(claim_identity))


def read_verdicts_for_session(
    connection: sqlite3.Connection, session_id: str
) -> tuple[FactVerdict, ...]:
    """Return every stored verdict for ``session_id``, ordered for reproducibility.

    A session with no verdicts returns an empty tuple.
    """
    rows = connection.execute(_SELECT_VERDICTS_FOR_SESSION_SQL, (session_id,)).fetchall()
    return tuple(row_to_fact_verdict(row) for row in rows)


def read_verdict(connection: sqlite3.Connection, identity: VerdictIdentity) -> FactVerdict | None:
    """Return the verdict stored under ``identity``, or ``None`` when it is unscored."""
    return _select_verdict(connection, _verdict_identity_values(identity))


def read_verdict_for_request(
    connection: sqlite3.Connection, identity: VerdictClaimIdentity
) -> FactVerdict | None:
    """Return the verdict whose judge model is exactly ``identity``'s requested model.

    Matches a requested model against the concrete identifier a verdict is keyed
    on, which is why this is a separate read rather than a ``VerdictIdentity``
    lookup: a floating alias is never a stored ``judge_model``, so a request under
    one always misses here and goes on to call the judge. Pinning a concrete
    identifier is what makes a request reusable.
    """
    return _select_verdict(connection, _claim_identity_values(identity))


def _select_verdict(
    connection: sqlite3.Connection, key: tuple[str, str, str, str]
) -> FactVerdict | None:
    row = connection.execute(_SELECT_VERDICT_SQL, key).fetchone()
    if row is None:
        return None
    return row_to_fact_verdict(row)


def acquire_verdict_claim(
    connection: sqlite3.Connection, claim: VerdictClaim, *, now: datetime
) -> ClaimOutcome:
    """Atomically acquire ``claim`` unless another owner holds it live.

    ``BEGIN IMMEDIATE`` takes the write lock before the liveness read, which is
    what makes the check and the write one decision. Under a deferred
    transaction both racers would read the identity as unclaimed and the second
    write would fail on lock upgrade, reporting a store error rather than a lost
    race.
    """
    with connection:
        connection.execute("BEGIN IMMEDIATE")
        existing_row = connection.execute(
            _SELECT_VERDICT_CLAIM_SQL, _claim_identity_values(claim.identity)
        ).fetchone()
        if existing_row is not None:
            existing = row_to_verdict_claim(existing_row)
            if existing.expires_at > now and existing.owner != claim.owner:
                return ClaimOutcome.HELD_ELSEWHERE
        connection.execute(_UPSERT_VERDICT_CLAIM_SQL, verdict_claim_to_row(claim))
    return ClaimOutcome.ACQUIRED


def release_verdict_claim(connection: sqlite3.Connection, identity: VerdictClaimIdentity) -> None:
    """Delete the transient claim under ``identity`` so another owner can acquire it."""
    with connection:
        connection.execute(_DELETE_VERDICT_CLAIM_SQL, _claim_identity_values(identity))


def read_verdict_claim(
    connection: sqlite3.Connection, identity: VerdictClaimIdentity
) -> VerdictClaim | None:
    """Return the claim stored under ``identity``, or ``None`` when it is available."""
    row = connection.execute(_SELECT_VERDICT_CLAIM_SQL, _claim_identity_values(identity)).fetchone()
    if row is None:
        return None
    return row_to_verdict_claim(row)


def _verdict_identity_values(identity: VerdictIdentity) -> tuple[str, str, str, str]:
    return (
        identity.session_id,
        identity.judge_input_hash,
        identity.rubric_version,
        identity.judge_model,
    )


def _claim_identity_values(identity: VerdictClaimIdentity) -> tuple[str, str, str, str]:
    return (
        identity.session_id,
        identity.judge_input_hash,
        identity.rubric_version,
        identity.requested_model,
    )
