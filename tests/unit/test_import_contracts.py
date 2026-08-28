"""Proves the import-linter contracts are enforced, not merely declared.

A contract in ``pyproject.toml`` that nobody ever breaks is indistinguishable
from a contract nobody checks. These tests run the real ``lint-imports`` CLI
against the repo, once as a baseline and once against a deliberately broken
tree, so the report-path contract's protection is demonstrated rather than
assumed.
"""

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
REPORT_MODULE_PATH = REPO_ROOT / "src" / "agentlens" / "core" / "report.py"
REPORT_PATH_CONTRACT_NAME = "The deterministic report path never reaches the judge"
_JUDGE_IMPORT_LINE = "import agentlens.judge  # noqa: F401\n"


def _run_lint_imports() -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["uv", "run", "lint-imports"],  # noqa: S607 -- "uv" is this project's mandated runner
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_lint_imports_passes_on_the_repo_as_it_stands() -> None:
    result = _run_lint_imports()

    assert result.returncode == 0, result.stdout + result.stderr


def test_lint_imports_reports_report_path_contract_broken_when_report_imports_judge() -> None:
    original_text = REPORT_MODULE_PATH.read_text()
    mutated_text = original_text.replace(
        "import logging\n", "import logging\n\n" + _JUDGE_IMPORT_LINE, 1
    )
    assert mutated_text != original_text, "the anchor line was not found; the file layout changed"

    try:
        REPORT_MODULE_PATH.write_text(mutated_text)
        broken_result = _run_lint_imports()

        assert broken_result.returncode != 0
        assert f"{REPORT_PATH_CONTRACT_NAME} BROKEN" in broken_result.stdout
    finally:
        REPORT_MODULE_PATH.write_text(original_text)

    assert REPORT_MODULE_PATH.read_text() == original_text
    restored_result = _run_lint_imports()
    assert restored_result.returncode == 0, restored_result.stdout + restored_result.stderr
