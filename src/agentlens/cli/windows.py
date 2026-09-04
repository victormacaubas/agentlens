from collections.abc import Mapping, Sequence
from typing import Any, cast

import click

from agentlens.models.windows import NAMED_WINDOW_THIS_WEEK, WindowSelector


class _MutuallyExclusiveOption(click.Option):
    """A Click option that rejects command-line use with any named sibling."""

    def __init__(
        self,
        param_decls: Sequence[str] | None = None,
        *,
        mutually_exclusive: frozenset[str] = frozenset(),
        **attrs: Any,
    ) -> None:
        self._mutually_exclusive = mutually_exclusive
        super().__init__(param_decls, **attrs)

    def handle_parse_result(
        self,
        ctx: click.Context,
        opts: Mapping[str, object],
        args: list[str],
    ) -> tuple[object, list[str]]:
        value, remaining = super().handle_parse_result(ctx, opts, args)
        if ctx.get_parameter_source(self.name or "") == click.ParameterSource.COMMANDLINE:
            supplied = {
                name
                for name in self._mutually_exclusive
                if ctx.get_parameter_source(name) == click.ParameterSource.COMMANDLINE
            }
            if supplied:
                names = ", ".join(sorted({self.name or "", *supplied}))
                raise click.UsageError(f"only one window selector may be supplied at once: {names}")
        return value, remaining


def build_window_selector(params: Mapping[str, object]) -> WindowSelector:
    """Build one validated selector from either window command's parsed parameters."""
    selector = WindowSelector(
        since_duration=cast("str | None", params["since_duration"]),
        named_window=cast("str | None", params["named_window"]),
        range_from=cast("str | None", params["range_from"]),
        range_to=cast("str | None", params["range_to"]),
    )
    _require_single_window_form(selector)
    return selector


def build_window_selector_options(*, action: str) -> list[click.Option]:
    """Return fresh Click options for a command selecting one reporting window."""
    return [
        _MutuallyExclusiveOption(
            ["--since", "since_duration"],
            mutually_exclusive=frozenset({"named_window", "range_from", "range_to"}),
            type=str,
            default=None,
            help=f"{action} a relative duration ending now, for example 7d.",
        ),
        _MutuallyExclusiveOption(
            ["--window", "named_window"],
            mutually_exclusive=frozenset({"since_duration", "range_from", "range_to"}),
            type=click.Choice([NAMED_WINDOW_THIS_WEEK]),
            default=None,
            help=f"{action} a named local-calendar window.",
        ),
        _MutuallyExclusiveOption(
            ["--from", "range_from"],
            mutually_exclusive=frozenset({"since_duration", "named_window"}),
            type=str,
            default=None,
            help="Explicit range lower bound (ISO-8601), paired with --to.",
        ),
        _MutuallyExclusiveOption(
            ["--to", "range_to"],
            mutually_exclusive=frozenset({"since_duration", "named_window"}),
            type=str,
            default=None,
            help="Explicit range upper bound (ISO-8601), paired with --from.",
        ),
    ]


def _require_single_window_form(selector: WindowSelector) -> None:
    has_range = selector.range_from is not None or selector.range_to is not None
    form_count = sum(
        (
            selector.since_duration is not None,
            selector.named_window is not None,
            has_range,
        )
    )
    if form_count != 1:
        raise click.UsageError(
            "exactly one window selector is required: --since, --window, or --from with --to"
        )
    if has_range and (selector.range_from is None or selector.range_to is None):
        raise click.UsageError("--from and --to must be supplied together")
