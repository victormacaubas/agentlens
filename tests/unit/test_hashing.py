"""Hashing helpers: stable across calls, blind to key order, blind to path noise."""

from pathlib import Path

from agentlens.utils.hashing import canonical_json_fingerprint, file_identity, hash_text


def test_hash_text_is_stable_and_hex() -> None:
    first = hash_text("hello")
    second = hash_text("hello")
    assert first == second
    assert len(first) == 64
    assert all(char in "0123456789abcdef" for char in first)


def test_hash_text_differs_for_different_input() -> None:
    assert hash_text("hello") != hash_text("world")


def test_canonical_json_fingerprint_ignores_key_order() -> None:
    first = canonical_json_fingerprint({"b": 2, "a": 1})
    second = canonical_json_fingerprint({"a": 1, "b": 2})
    assert first == second


def test_canonical_json_fingerprint_ignores_nested_key_order() -> None:
    first = canonical_json_fingerprint({"outer": {"z": 1, "y": 2}, "list": [1, 2]})
    second = canonical_json_fingerprint({"list": [1, 2], "outer": {"y": 2, "z": 1}})
    assert first == second


def test_canonical_json_fingerprint_distinguishes_different_values() -> None:
    assert canonical_json_fingerprint({"a": 1}) != canonical_json_fingerprint({"a": 2})


def test_file_identity_ignores_dot_segments(tmp_path: Path) -> None:
    direct = tmp_path / "a" / "b.txt"
    with_dot = tmp_path / "a" / "." / "b.txt"
    assert file_identity(direct) == file_identity(with_dot)


def test_file_identity_ignores_dotdot_segments(tmp_path: Path) -> None:
    direct = tmp_path / "a" / "b.txt"
    via_sibling = tmp_path / "a" / "c" / ".." / "b.txt"
    assert file_identity(direct) == file_identity(via_sibling)


def test_file_identity_distinguishes_different_paths(tmp_path: Path) -> None:
    assert file_identity(tmp_path / "a.txt") != file_identity(tmp_path / "b.txt")


def test_file_identity_is_stable_across_calls(tmp_path: Path) -> None:
    path = tmp_path / "a.txt"
    assert file_identity(path) == file_identity(path)
