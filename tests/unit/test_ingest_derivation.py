"""The derivation fingerprint: covers every shaping input, not just the transcript."""

import json
import os
from pathlib import Path

import pytest

from agentlens.ingest.derivation import derive_session_derivation
from agentlens.ingest.transcript import parse_transcript
from tests.factories import (
    build_sidecar,
    build_tool_invocation_pair,
    build_transcript_path,
    build_transcript_text,
)

_FIVE_SECONDS_IN_NS = 5_000_000_000


def _write_transcript(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_transcript_text(build_tool_invocation_pair()))


def test_derive_session_derivation_rejects_an_empty_input_list() -> None:
    with pytest.raises(ValueError, match="shaping input"):
        derive_session_derivation([])


def test_fingerprint_changes_when_the_sidecar_changes_but_the_transcript_does_not(
    tmp_path: Path,
) -> None:
    path = build_transcript_path(tmp_path)
    _write_transcript(path)
    sidecar_path = path.with_suffix(".meta.json")
    sidecar_path.write_text(json.dumps(build_sidecar(description="First description")))

    before = parse_transcript(path)

    sidecar_path.write_text(json.dumps(build_sidecar(description="Second description")))
    after = parse_transcript(path)

    assert before.session.revision.content_hash == after.session.revision.content_hash
    assert before.session.derivation_fingerprint != after.session.derivation_fingerprint


def test_repeated_parses_of_unchanged_inputs_produce_the_same_derivation(tmp_path: Path) -> None:
    path = build_transcript_path(tmp_path)
    _write_transcript(path)
    sidecar_path = path.with_suffix(".meta.json")
    sidecar_path.write_text(json.dumps(build_sidecar()))

    first = parse_transcript(path)
    second = parse_transcript(path)

    assert first.session.derivation_fingerprint == second.session.derivation_fingerprint
    assert first.session.derivation_observed_mtime_ns == second.session.derivation_observed_mtime_ns


def test_derivation_observed_mtime_ns_is_the_newer_of_the_transcript_and_sidecar_mtimes(
    tmp_path: Path,
) -> None:
    path = build_transcript_path(tmp_path)
    _write_transcript(path)
    sidecar_path = path.with_suffix(".meta.json")
    sidecar_path.write_text(json.dumps(build_sidecar()))

    transcript_mtime_ns = path.stat().st_mtime_ns
    older_sidecar_mtime_ns = transcript_mtime_ns - _FIVE_SECONDS_IN_NS
    os.utime(sidecar_path, ns=(older_sidecar_mtime_ns, older_sidecar_mtime_ns))

    facts = parse_transcript(path)

    assert facts.session.derivation_observed_mtime_ns == transcript_mtime_ns


def test_derivation_fingerprint_is_unaffected_by_a_content_preserving_mtime_touch(
    tmp_path: Path,
) -> None:
    """Touching a file's mtime without changing its bytes must not look like a
    content change, or an unrelated filesystem touch would force every
    dependent row to be treated as new.
    """
    path = build_transcript_path(tmp_path)
    _write_transcript(path)

    before = parse_transcript(path)

    touched_mtime_ns = path.stat().st_mtime_ns + _FIVE_SECONDS_IN_NS
    os.utime(path, ns=(touched_mtime_ns, touched_mtime_ns))
    after = parse_transcript(path)

    assert before.session.derivation_fingerprint == after.session.derivation_fingerprint
    assert after.session.derivation_observed_mtime_ns == touched_mtime_ns
