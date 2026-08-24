"""Building the hardened ``claude -p`` invocation as one pure, inspectable value.

Every part of the invocation this module assembles is a security control:
``--tools ""`` and an explicit ``cwd`` stop the judge touching the caller's
filesystem, ``--bare`` and ``--setting-sources "user"`` stop a project's own
configuration from influencing its own score, ``--settings`` is what makes
authentication work at all under ``--bare``, and the reduced environment
stops unrelated variables from reaching the call. Building it as one pure
function, rather than assembling flags inline at the call site, is what lets
a test assert directly on argv and env instead of on a transcript of what a
mocked subprocess received.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from agentlens.judge.rubric import JUDGE_INSTRUCTIONS, VERDICT_JSON_SCHEMA

DEFAULT_CLAUDE_BINARY: Final = "claude"

_ALLOWED_STATIC_ENV_VARS: Final = frozenset({"PATH", "HOME"})
_ANTHROPIC_ENV_PREFIX: Final = "ANTHROPIC_"
_DEFAULT_USER_SETTINGS_PATH: Final = Path("~/.claude/settings.json")


def default_user_settings_path() -> Path:
    """The invoking user's Claude Code settings file, expanded to an absolute path.

    ``--bare`` reads an ``apiKeyHelper`` only from ``--settings``, never from
    the keychain, so passing this path is what makes authentication succeed
    for a user who authenticates through a helper rather than a bare API key.
    """
    return _DEFAULT_USER_SETTINGS_PATH.expanduser()


@dataclass(frozen=True, slots=True, kw_only=True)
class ClaudeInvocation:
    """Everything one hardened judge call needs: its argv, environment, and cwd."""

    argv: tuple[str, ...]
    env: Mapping[str, str]
    cwd: Path
    timeout_s: float


def build_invocation(
    *,
    prompt: str,
    model: str,
    spend_ceiling_usd: float,
    timeout_s: float,
    settings_path: Path,
    temp_dir: Path,
    source_env: Mapping[str, str],
    binary: str = DEFAULT_CLAUDE_BINARY,
) -> ClaudeInvocation:
    """Build the full hardened invocation for one scoring call.

    Args:
        prompt: The prepared judge input, passed as a positional argument so
            it never needs shell interpolation.
        model: The requested model alias or id.
        spend_ceiling_usd: The maximum ``claude`` may spend before ending the
            call, passed as ``--max-budget-usd``.
        timeout_s: The wall-clock limit the caller enforces on the process;
            carried alongside argv rather than expressed as a flag, since
            there is no ``--max-turns`` or equivalent flag to enforce it.
        settings_path: The user settings file ``--bare`` reads auth through.
        temp_dir: An explicit working directory, so the call cannot read or
            write anything in the caller's own filesystem.
        source_env: The process environment to reduce before the call runs.
        binary: The executable to invoke. Overridable so a test can point at
            a stub rather than the real ``claude`` CLI.
    """
    argv = (
        binary,
        "-p",
        prompt,
        "--output-format",
        "json",
        "--model",
        model,
        "--json-schema",
        json.dumps(VERDICT_JSON_SCHEMA),
        "--bare",
        "--tools",
        "",
        "--setting-sources",
        "user",
        "--settings",
        str(settings_path),
        "--max-budget-usd",
        str(spend_ceiling_usd),
        "--append-system-prompt",
        JUDGE_INSTRUCTIONS,
    )
    return ClaudeInvocation(
        argv=argv,
        env=_reduce_env(source_env),
        cwd=temp_dir,
        timeout_s=timeout_s,
    )


def _reduce_env(source_env: Mapping[str, str]) -> dict[str, str]:
    return {
        key: value
        for key, value in source_env.items()
        if key in _ALLOWED_STATIC_ENV_VARS or key.startswith(_ANTHROPIC_ENV_PREFIX)
    }
