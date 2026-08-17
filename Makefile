.PHONY: check format test integration

# CI runs this exact target, so edits here change CI too.
# Cheap checks first, so a formatting slip fails before the suite runs.
check:
	uv run ruff format --check .
	uv run ruff check .
	uv run lint-imports
	uv run mypy
	uv run pytest

format:
	uv run ruff format .
	uv run ruff check --fix .

test:
	uv run pytest

# Needs a `claude` CLI, needs auth, costs money. Exits 5 until the first
# integration test exists; fix that by writing it, not by suppressing the code.
integration:
	uv run pytest -m integration
