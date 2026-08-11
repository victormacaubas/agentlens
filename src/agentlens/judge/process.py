"""Process-execution seam for the judge backend: a `CommandRunner` protocol
narrowed to exactly what `ClaudeCliJudge` calls, plus the one real
implementation. Injecting this lets tests exercise the judge without ever
shelling out to a real `claude` binary.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import Protocol


class CommandRunner(Protocol):
    """Structural interface over the two OS calls `ClaudeCliJudge` makes:
    launching the `claude` subprocess and locating it on `PATH`.
    """

    def run(
        self,
        args: list[str],
        *,
        input: str,
        capture_output: bool,
        text: bool,
        timeout: int,
        cwd: str,
        env: dict[str, str],
    ) -> subprocess.CompletedProcess[str]: ...

    def which(self, name: str) -> str | None: ...


class SubprocessCommandRunner:
    """The real `CommandRunner`: a thin passthrough to `subprocess.run` and
    `shutil.which`.
    """

    def run(
        self,
        args: list[str],
        *,
        input: str,
        capture_output: bool,
        text: bool,
        timeout: int,
        cwd: str,
        env: dict[str, str],
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            args,
            input=input,
            capture_output=capture_output,
            text=text,
            timeout=timeout,
            cwd=cwd,
            env=env,
        )

    def which(self, name: str) -> str | None:
        return shutil.which(name)
