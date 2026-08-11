"""Test double for `agentlens.judge.process.CommandRunner`."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class RecordedRun:
    """One `run()` call's argv and keyword arguments, as seen by the fake."""

    args: list[str]
    input: str
    timeout: int
    cwd: str
    env: dict[str, str]


class FakeCommandRunner:
    """A `CommandRunner` double that returns a canned result instead of
    shelling out to a real `claude` binary, and records every call so a
    test can assert on the argv agentlens built.

    `responses` is consumed in call order, one entry per `run()` call; once
    exhausted (or if never supplied), `returncode`/`stdout`/`stderr` supply
    a constant fallback result. `run_exception` and `which_exception` are
    raised instead of returning, so a test can simulate a timeout, a launch
    failure, or a boundary that must never be reached.
    """

    def __init__(
        self,
        *,
        returncode: int = 0,
        stdout: str = "",
        stderr: str = "",
        responses: list[subprocess.CompletedProcess[str]] | None = None,
        run_exception: BaseException | None = None,
        which_result: str | None = "/usr/bin/claude",
        which_exception: BaseException | None = None,
    ) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self._responses = list(responses) if responses is not None else None
        self.run_exception = run_exception
        self.which_result = which_result
        self.which_exception = which_exception
        self.calls: list[RecordedRun] = []
        self.which_calls: list[str] = []

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
        self.calls.append(
            RecordedRun(args=list(args), input=input, timeout=timeout, cwd=cwd, env=dict(env))
        )
        if self.run_exception is not None:
            raise self.run_exception
        if self._responses:
            return self._responses.pop(0)
        return subprocess.CompletedProcess(
            args=args, returncode=self.returncode, stdout=self.stdout, stderr=self.stderr
        )

    def which(self, name: str) -> str | None:
        self.which_calls.append(name)
        if self.which_exception is not None:
            raise self.which_exception
        return self.which_result
