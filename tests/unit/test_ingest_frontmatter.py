"""The bounded frontmatter parser: scalars, block lists, and what it deliberately ignores."""

import pytest

from agentlens.errors import MalformedSourceError
from agentlens.ingest.frontmatter import list_field, parse_frontmatter, scalar_field


def test_scalar_fields_are_read_verbatim_including_square_brackets() -> None:
    text = "---\nname: implementer\nmodel: claude-sonnet-5[1m]\neffort: high\n---\nbody\n"

    frontmatter = parse_frontmatter(text, source_path_label="definition.md")

    assert scalar_field(frontmatter, "name", source_path_label="definition.md") == "implementer"
    assert (
        scalar_field(frontmatter, "model", source_path_label="definition.md")
        == "claude-sonnet-5[1m]"
    )
    assert scalar_field(frontmatter, "effort", source_path_label="definition.md") == "high"


def test_scalar_field_returns_none_when_the_key_is_absent() -> None:
    text = "---\nname: implementer\n---\nbody\n"

    frontmatter = parse_frontmatter(text, source_path_label="definition.md")

    assert scalar_field(frontmatter, "effort", source_path_label="definition.md") is None


def test_list_field_splits_a_comma_separated_scalar_and_strips_whitespace() -> None:
    text = "---\nname: implementer\ntools: Read, Write, Edit, Bash\n---\nbody\n"

    frontmatter = parse_frontmatter(text, source_path_label="definition.md")

    assert list_field(frontmatter, "tools", source_path_label="definition.md") == (
        "Read",
        "Write",
        "Edit",
        "Bash",
    )


def test_list_field_reads_a_genuine_block_list() -> None:
    text = (
        "---\nname: implementer\nskills:\n"
        "  - craft:python-engineering-standards\n  - craft:sql\n---\nbody\n"
    )

    frontmatter = parse_frontmatter(text, source_path_label="definition.md")

    assert list_field(frontmatter, "skills", source_path_label="definition.md") == (
        "craft:python-engineering-standards",
        "craft:sql",
    )


def test_list_field_defaults_to_an_empty_tuple_when_the_key_is_absent() -> None:
    text = "---\nname: implementer\n---\nbody\n"

    frontmatter = parse_frontmatter(text, source_path_label="definition.md")

    assert list_field(frontmatter, "skills", source_path_label="definition.md") == ()


def test_list_field_rejects_a_bracketed_string_that_is_not_a_real_list() -> None:
    """``tools: ["Read", "Grep"]`` is a scalar in this bounded parser, not a list.

    Comma-splitting it would silently produce tool names with stray quotes
    and brackets baked in, so it must be rejected instead of guessed at.
    """
    text = '---\nname: skill-reviewer\ntools: ["Read", "Grep"]\n---\nbody\n'

    frontmatter = parse_frontmatter(text, source_path_label="definition.md")

    with pytest.raises(MalformedSourceError, match="tools"):
        list_field(frontmatter, "tools", source_path_label="definition.md")


def test_scalar_field_rejects_a_genuine_list_value() -> None:
    text = "---\nname: implementer\nmodel:\n  - not-a-scalar\n---\nbody\n"

    frontmatter = parse_frontmatter(text, source_path_label="definition.md")

    with pytest.raises(MalformedSourceError, match="model"):
        scalar_field(frontmatter, "model", source_path_label="definition.md")


def test_unknown_keys_are_captured_but_never_required() -> None:
    text = "---\nname: implementer\npermissionMode: acceptEdits\ncolor: cyan\n---\nbody\n"

    frontmatter = parse_frontmatter(text, source_path_label="definition.md")

    assert scalar_field(frontmatter, "name", source_path_label="definition.md") == "implementer"


def test_a_multiline_block_scalar_for_an_unknown_key_does_not_break_parsing() -> None:
    """Real plugin-provided agents write ``description: |`` with an indented,
    multi-line body full of colons and blank lines. None of that is a known
    field, so the parser must tolerate it rather than misreading a
    continuation line as a new top-level key.
    """
    text = (
        "---\n"
        "name: skill-reviewer\n"
        "description: |\n"
        "  Use this agent when the user asks to review a skill. Examples:\n"
        "\n"
        "  <example>\n"
        '  user: "please review my skill"\n'
        '  assistant: "Sure, reviewing now."\n'
        "  </example>\n"
        "model: inherit\n"
        'tools: ["Read", "Grep", "Glob"]\n'
        "---\n"
        "body\n"
    )

    frontmatter = parse_frontmatter(text, source_path_label="definition.md")

    assert scalar_field(frontmatter, "name", source_path_label="definition.md") == "skill-reviewer"
    assert scalar_field(frontmatter, "model", source_path_label="definition.md") == "inherit"
    with pytest.raises(MalformedSourceError, match="tools"):
        list_field(frontmatter, "tools", source_path_label="definition.md")


def test_missing_opening_delimiter_is_rejected() -> None:
    with pytest.raises(MalformedSourceError, match="delimiter"):
        parse_frontmatter("name: implementer\n---\nbody\n", source_path_label="definition.md")


def test_missing_closing_delimiter_is_rejected() -> None:
    with pytest.raises(MalformedSourceError, match="closing delimiter"):
        parse_frontmatter("---\nname: implementer\n", source_path_label="definition.md")
