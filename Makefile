.PHONY: check quick format test integration

# CI runs this exact target, so edits here change CI too.
# Cheap checks first, so a formatting slip fails before the suite runs.
check:
	uv run ruff format --check .
	uv run ruff check .
	uv run lint-imports
	uv run mypy
	uv run pytest

# Per-task loop while building a slice. Same tools as `check`, minus the import
# contracts and the full suite: pass the slice's own test paths as T.
#   make quick T=tests/unit/test_ingest_skill_firing.py
# `check` stays the gate that runs before a change is done, and CI runs `check`.
quick:
	uv run ruff format --check .
	uv run ruff check .
	uv run mypy
	uv run pytest $(or $(T),tests)

format:
	uv run ruff format .
	uv run ruff check --fix .

test:
	uv run pytest

# Needs a `claude` CLI, needs auth, costs money. Exits 5 until the first
# integration test exists; fix that by writing it, not by suppressing the code.
integration:
	uv run pytest -m integration
