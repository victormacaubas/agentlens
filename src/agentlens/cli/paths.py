from pathlib import Path

import click

_STORE_DB_FILENAME = "agentlens.db"


def default_store_path() -> Path:
    """Return the store's default location under the user's cache directory."""
    return Path(click.get_app_dir("agentlens")) / _STORE_DB_FILENAME


def default_claude_root() -> Path:
    """Return the default location of the user's ``.claude`` directory."""
    return Path.home() / ".claude"
