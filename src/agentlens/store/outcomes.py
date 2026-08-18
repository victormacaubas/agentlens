from enum import StrEnum


class UpsertOutcome(StrEnum):
    """What happened when a session snapshot was presented for writing.

    ``REPLACED`` covers both a session seen for the first time and a sound,
    newer snapshot of one already stored. ``SKIPPED_IDENTICAL`` and
    ``REFUSED_STALE`` are both no-ops that leave the stored rows untouched; they
    are distinguished only so the caller can report which one happened.
    """

    REPLACED = "replaced"
    SKIPPED_IDENTICAL = "skipped_identical"
    REFUSED_STALE = "refused_stale"
