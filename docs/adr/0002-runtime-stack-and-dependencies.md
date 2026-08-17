# 0002. Runtime stack and dependencies

## Status

Accepted

## Context

Python and `uv`/PyPI distribution were locked in the design doc, with `uvx
agentlens` as the primary entry point. What was not settled is the dependency
set, and this is the decision that gets made by accident: whoever writes the
first file picks the CLI framework, the templating engine, and the serialization
library, and nobody revisits any of it.

Two properties of the tool shape the choices. It is run via `uvx`, so cold-start
install weight is a user-visible cost. And it renders untrusted LLM output into
an HTML file the user opens in a browser, so escaping is a security control
rather than a cosmetic concern.

## Decision

**Runtime dependencies, each paired with its owning package (ADR 0001):**

| Dependency | Owned by | Why |
|---|---|---|
| `click` | `cli` | Three commands with ~10 flags, including mutually-exclusive window selectors (`--since` / `--window` / `--from`+`--to`) and options shared across commands. Small, stable, and the prototype already proved it fits. |
| `jinja2` | `render` | Autoescape-by-default is the control that keeps untrusted judge output (evidence strings, fix recommendations) from injecting into the HTML report. The report has real loops over spawns and fixes. |

**Standard library, deliberately:**

| Module | Owned by | Replaces |
|---|---|---|
| `sqlite3` | `store` | an ORM |
| `subprocess` | `judge` | an HTTP SDK |
| `json` | `judge`, `render` | a serialization library |
| `hashlib` | `utils` | n/a |
| `dataclasses` | `models` | pydantic |
| `datetime`, `pathlib` | `core`, `ingest` | n/a |

**Development dependencies:** `pytest`, `pytest-socket`, `ruff`, `mypy`,
`import-linter`, `pre-commit`.

**Deliberately excluded, with reasons:**

- **No ORM (SQLAlchemy et al.).** The store is a hand-designed star schema whose
  whole value is explicit grain and explicit upsert semantics. Hand-written SQL
  stays legible and an ORM would obscure exactly the part that matters. The store
  is also a disposable cache rebuilt from source, so migrations, the strongest
  argument for an ORM, do not apply.
- **No pydantic.** The one place validation is genuinely needed is the verdict
  returned by the judge, where the design doc requires splitting locally-derived
  fields from untrusted model output. That split is a hand-written validator in
  `judge` returning a frozen dataclass; pydantic would validate shape but not
  provenance, so it would add weight without removing the code that matters.
- **No pandas or polars.** Aggregation is SQL over SQLite. A dataframe library
  would be the largest thing in the install for work the database already does.
- **No `rich`.** The terminal surface is specified as a thin summary: headline
  score plus a path. Plain `print` is sufficient, and `rich` would invite the
  terminal surface to grow into the report.
- **No `requests` / `httpx`.** The judge shells out to the user's `claude` CLI.
  If an `ANTHROPIC_API_KEY` backend is added later it goes behind the same
  `JudgeBackend` Protocol, and adding an HTTP client then is an ADR.
- **No `jsonschema`.** The `--json-schema` argument passed to `claude -p` is a
  dict literal we author; validating the response is the hand-written validator
  above.
- **No `typer`.** It sits on top of `click` and buys signature ergonomics that a
  deliberately thin `cli.py` does not need.
- **No Black or isort.** Ruff covers both.

**The set is closed.** Adding a runtime dependency is a decision that gets its
own ADR, not an implementation detail resolved mid-task. An implementer that
reaches a task requiring a library not listed above stops and hands the task
back rather than choosing one.

Layout is `src/agentlens/`, so the installed package is what gets imported and
tested rather than whatever happens to be in the working directory.

## Consequences

- **`click` costs a dependency for something `argparse` could do.** What we
  bought is shared options and mutually-exclusive window flags without parser
  plumbing in every test. What we gave up is the ability to say the tool has zero
  runtime dependencies, which matters for `uvx` cold start, though `click` is
  small enough that the honest cost is milliseconds.
- **`jinja2` is the heavier of the two.** It is justified by escaping rather than
  by convenience, which means the justification disappears if the HTML report is
  ever dropped. If that happens, drop `jinja2` with it rather than keeping it for
  the markdown renderer.
- **Hand-written SQL means hand-written everything.** No migration tooling, no
  query builder, no lazy relationship loading. The store being a rebuildable
  cache is what makes this affordable; if the store ever becomes durable state
  that must be migrated in place, this decision needs revisiting.
- **Hand-written verdict validation is code we own and must test.** The upside is
  that provenance, which fields are ours and which are untrusted, is explicit
  in the type rather than implied.
- **No dataframe library means aggregation is SQL.** Anyone more fluent in pandas
  than in window functions will find the reporting layer harder to contribute to.
