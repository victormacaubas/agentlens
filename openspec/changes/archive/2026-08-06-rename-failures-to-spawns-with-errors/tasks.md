## 1. Rename the field

- [x] 1.1 Rename `n_failures` to `n_spawns_with_errors` on `AgentAggregate` and `ParentLensRow` in `src/agentlens/reporting/queries.py`, leaving the computation (`n_errors > 0 OR final_report_flagged_partial = 1`) unchanged
- [x] 1.2 Update both SQL column aliases (`_query_agent_aggregates` and the parent-lens query) and the positional row unpacking that reads them
- [x] 1.3 Update the `_DELTA_FIELDS` tuple so the prior-window delta map keys on the new name
- [x] 1.4 Update both dict literals in `ReportResult`'s JSON serialization (the `agents` array and the `parent_lens` array)
- [x] 1.5 Add a short comment at the dataclass field recording what the metric counts and how it differs from `n_errors`, so the definition is readable without tracing the SQL

## 2. Rename in the rendered output

- [x] 2.1 Change the agent-aggregate line in `src/agentlens/reporting/rendering.py` from `{n} failures` to `{n} had errors`
- [x] 2.2 Change the parent-lens line the same way, keeping its existing `hit denials` phrasing consistent alongside it

## 3. Tests

- [x] 3.1 Update `tests/unit/test_reporting.py` for the renamed dataclass fields, the renamed JSON keys, and the new rendered wording
- [x] 3.2 Add a test asserting no key named `n_failures` appears anywhere in the `--json` payload, so a partial rename cannot pass

## 4. Quality gate

- [x] 4.1 `uv run pytest` green
- [x] 4.2 `uv run ruff check` and `uv run mypy` green
- [x] 4.3 `openspec validate rename-failures-to-spawns-with-errors --strict` passes
- [x] 4.4 Run `uv run agentlens report --since 30d | head -12` and confirm the output reads `had errors`, with no occurrence of `failures`
