"""Building the hardened ``claude -p`` invocation as a pure, inspectable value.

Every element of this invocation is a security control, so these tests assert
directly on the constructed argv and environment rather than on a subprocess
call, which is what a pure builder function is for.
"""

import json
from pathlib import Path

from agentlens.judge.invocation import (
    ClaudeInvocation,
    build_invocation,
    default_user_settings_path,
)
from agentlens.judge.rubric import JUDGE_INSTRUCTIONS, VERDICT_JSON_SCHEMA


def _build(
    *,
    temp_dir: Path,
    source_env: dict[str, str] | None = None,
    settings_path: Path | None = None,
    binary: str = "claude",
) -> ClaudeInvocation:
    return build_invocation(
        prompt="Score this run.",
        model="sonnet",
        spend_ceiling_usd=0.5,
        timeout_s=120.0,
        settings_path=(
            settings_path if settings_path is not None else Path("/home/user/.claude/settings.json")
        ),
        temp_dir=temp_dir,
        source_env=source_env
        if source_env is not None
        else {"PATH": "/usr/bin", "HOME": "/home/user"},
        binary=binary,
    )


def test_argv_contains_every_element_of_the_hardened_contract(tmp_path: Path) -> None:
    invocation = _build(temp_dir=tmp_path)

    argv = invocation.argv
    assert argv[0] == "claude"
    assert argv[1] == "-p"
    assert argv[2] == "Score this run."
    assert "--output-format" in argv and argv[argv.index("--output-format") + 1] == "json"
    assert "--model" in argv and argv[argv.index("--model") + 1] == "sonnet"
    assert "--json-schema" in argv
    schema_arg = argv[argv.index("--json-schema") + 1]
    assert schema_arg == json.dumps(VERDICT_JSON_SCHEMA)
    assert "--bare" in argv
    assert "--tools" in argv and argv[argv.index("--tools") + 1] == ""
    assert "--setting-sources" in argv and argv[argv.index("--setting-sources") + 1] == "user"
    assert "--append-system-prompt" in argv
    assert argv[argv.index("--append-system-prompt") + 1] == JUDGE_INSTRUCTIONS


def test_argv_carries_the_expanded_settings_path(tmp_path: Path) -> None:
    settings_path = Path("/home/user/.claude/settings.json")
    invocation = _build(temp_dir=tmp_path, settings_path=settings_path)

    argv = invocation.argv
    assert "--settings" in argv
    assert argv[argv.index("--settings") + 1] == str(settings_path)


def test_argv_carries_the_spend_ceiling(tmp_path: Path) -> None:
    invocation = build_invocation(
        prompt="Score this run.",
        model="sonnet",
        spend_ceiling_usd=0.5,
        timeout_s=120.0,
        settings_path=Path("/home/user/.claude/settings.json"),
        temp_dir=tmp_path,
        source_env={},
    )

    argv = invocation.argv
    assert "--max-budget-usd" in argv
    assert argv[argv.index("--max-budget-usd") + 1] == "0.5"


def test_argv_never_carries_max_turns(tmp_path: Path) -> None:
    invocation = _build(temp_dir=tmp_path)

    assert "--max-turns" not in invocation.argv


def test_cwd_is_the_given_temporary_directory(tmp_path: Path) -> None:
    invocation = _build(temp_dir=tmp_path)

    assert invocation.cwd == tmp_path


def test_env_keeps_only_path_home_and_anthropic_prefixed_vars(tmp_path: Path) -> None:
    source_env = {
        "PATH": "/usr/bin",
        "HOME": "/home/user",
        "ANTHROPIC_API_KEY": "secret",
        "ANTHROPIC_BASE_URL": "https://example.invalid",
        "SHELL": "/bin/zsh",
        "AWS_ACCESS_KEY_ID": "unrelated",
    }
    invocation = _build(temp_dir=tmp_path, source_env=source_env)

    assert invocation.env == {
        "PATH": "/usr/bin",
        "HOME": "/home/user",
        "ANTHROPIC_API_KEY": "secret",
        "ANTHROPIC_BASE_URL": "https://example.invalid",
    }


def test_env_omits_path_and_home_when_absent_from_source(tmp_path: Path) -> None:
    invocation = _build(temp_dir=tmp_path, source_env={"ANTHROPIC_API_KEY": "secret"})

    assert invocation.env == {"ANTHROPIC_API_KEY": "secret"}


def test_binary_override_replaces_argv_zero(tmp_path: Path) -> None:
    stub_binary = str(tmp_path / "stub-claude")
    invocation = _build(temp_dir=tmp_path, binary=stub_binary)

    assert invocation.argv[0] == stub_binary


def test_default_user_settings_path_is_expanded_and_absolute() -> None:
    path = default_user_settings_path()

    assert path.is_absolute()
    assert path.name == "settings.json"
    assert str(path).endswith(".claude/settings.json")
