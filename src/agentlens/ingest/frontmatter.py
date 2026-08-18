"""Parsing bounded, ``---``-delimited frontmatter without a YAML dependency.

Claude agent and skill definitions open with a frontmatter block that uses a
small, predictable subset of YAML: single-line scalars, and block-style lists
of single-line items indented under their key. This module recognizes exactly
that subset. It does not implement nested mappings, flow collections
(``[a, b]``), or multi-line block scalars (``|``, ``>``): those are common in
the free-text fields this product never reads (``description`` and the
like), so unindented lines close the field they belong to and any indented
lines that are not a recognized list item are treated as content belonging to
whichever field is open and ignored, rather than rejected outright. A known
field is never expected to take one of those unsupported shapes; when it
does, the caller that extracts it is responsible for rejecting it explicitly.
"""

from dataclasses import dataclass, field

from agentlens.errors import MalformedSourceError

_DELIMITER = "---"
_LIST_ITEM_PREFIX = "- "
_BLOCK_SCALAR_INDICATORS = frozenset({"|", "|-", "|+", ">", ">-", ">+"})

FrontmatterValue = str | tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class Frontmatter:
    """The raw field values parsed from one frontmatter block.

    Each value is a scalar string or a tuple of strings. Distinguishing which
    shape a given key is supposed to have is the caller's job: this parser
    only reports what it found.
    """

    fields: dict[str, FrontmatterValue] = field(default_factory=dict)


def parse_frontmatter(text: str, *, source_path_label: str) -> Frontmatter:
    """Parse the leading ``---``-delimited frontmatter block of ``text``.

    Raises:
        MalformedSourceError: ``text`` does not open with a frontmatter
            delimiter, the block has no closing delimiter, or a top-level
            (unindented) line inside the block is neither blank nor a
            ``key: value`` pair.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != _DELIMITER:
        raise MalformedSourceError(f"{source_path_label} has no frontmatter delimiter")

    fields: dict[str, FrontmatterValue] = {}
    pending_key: str | None = None
    pending_items: list[str] = []
    closed = False

    def flush_pending() -> None:
        nonlocal pending_key, pending_items
        if pending_key is not None:
            fields[pending_key] = tuple(pending_items)
        pending_key = None
        pending_items = []

    for line in lines[1:]:
        if line.strip() == _DELIMITER:
            closed = True
            break
        if not line.strip():
            continue
        if line[0].isspace():
            stripped = line.strip()
            if pending_key is not None and stripped.startswith(_LIST_ITEM_PREFIX):
                pending_items.append(stripped[len(_LIST_ITEM_PREFIX) :].strip())
            continue
        flush_pending()
        if ":" not in line:
            raise MalformedSourceError(
                f"{source_path_label} has an unparseable frontmatter line: {line!r}"
            )
        key, _, rest = line.partition(":")
        key = key.strip()
        rest = rest.strip()
        if rest and rest not in _BLOCK_SCALAR_INDICATORS:
            fields[key] = rest
        else:
            pending_key = key
            pending_items = []

    flush_pending()
    if not closed:
        raise MalformedSourceError(f"{source_path_label} frontmatter has no closing delimiter")
    return Frontmatter(fields=fields)


def scalar_field(frontmatter: Frontmatter, key: str, *, source_path_label: str) -> str | None:
    """Return the scalar value of ``key``, or ``None`` when the key is absent.

    Raises:
        MalformedSourceError: ``key`` is present as a list, or as a scalar
            that looks like an inline flow collection (``[...]``) rather than
            a genuine scalar; this parser does not support flow syntax, so a
            value shaped like one is rejected rather than guessed at.
    """
    value = frontmatter.fields.get(key)
    if value is None:
        return None
    if isinstance(value, tuple):
        raise MalformedSourceError(f"{source_path_label}: field {key!r} has an unsupported shape")
    if _is_unsupported_shape(value):
        raise MalformedSourceError(f"{source_path_label}: field {key!r} has an unsupported shape")
    return value


def list_field(frontmatter: Frontmatter, key: str, *, source_path_label: str) -> tuple[str, ...]:
    """Return the list value of ``key``, defaulting to an empty tuple when absent.

    Accepts a genuine block-style list, or a scalar comma-separated string
    (split on commas and stripped), the shape every real ``tools:`` field on
    this machine uses.

    Raises:
        MalformedSourceError: ``key`` holds a scalar that looks like an
            inline flow collection (``[...]``); this parser does not support
            flow syntax, so a value shaped like one is rejected rather than
            comma-split into garbage.
    """
    value = frontmatter.fields.get(key)
    if value is None:
        return ()
    if isinstance(value, tuple):
        return value
    if _is_unsupported_shape(value):
        raise MalformedSourceError(f"{source_path_label}: field {key!r} has an unsupported shape")
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _is_unsupported_shape(value: FrontmatterValue) -> bool:
    return isinstance(value, str) and value.startswith("[") and value.endswith("]")
