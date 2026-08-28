"""SQL over ``dim_agent``, the catalog of versioned agent definitions."""

import sqlite3

from agentlens.models.agent_definitions import AgentDefinition
from agentlens.store.rows import agent_definition_to_row, row_to_agent_definition
from agentlens.store.schema import DIM_AGENT_COLUMN_NAMES

_DIM_AGENT_CONFLICT_TARGET = "agent_definition_id"

_DIM_AGENT_COLUMN_LIST = ", ".join(DIM_AGENT_COLUMN_NAMES)
_DIM_AGENT_PLACEHOLDERS = ", ".join(["?"] * len(DIM_AGENT_COLUMN_NAMES))

_UPSERT_AGENT_DEFINITION_SQL = f"""
INSERT INTO dim_agent (
    {_DIM_AGENT_COLUMN_LIST}
) VALUES ({_DIM_AGENT_PLACEHOLDERS})
ON CONFLICT({_DIM_AGENT_CONFLICT_TARGET}) DO NOTHING
"""  # noqa: S608

_SELECT_AGENT_DEFINITION_SQL = f"""
SELECT
    {_DIM_AGENT_COLUMN_LIST}
FROM dim_agent
WHERE agent_definition_id = ?
"""  # noqa: S608


def catalog_definition(connection: sqlite3.Connection, definition: AgentDefinition) -> None:
    """Write ``definition`` without opening a transaction of its own.

    Separate from :func:`upsert_agent_definition` so a caller batching
    definitions and sessions together can enrol this write in its own
    all-or-nothing transaction rather than having it commit independently.
    """
    connection.execute(_UPSERT_AGENT_DEFINITION_SQL, agent_definition_to_row(definition))


def upsert_agent_definition(connection: sqlite3.Connection, definition: AgentDefinition) -> None:
    """Insert ``definition`` into ``dim_agent`` if its identity is not already stored.

    ``agent_definition_id`` is content-addressed, so a conflicting row is
    always identical to ``definition``; a repeat catalog scan is therefore a
    no-op rather than a second, staleness-checked write.
    """
    with connection:
        catalog_definition(connection, definition)


def read_agent_definition(
    connection: sqlite3.Connection, agent_definition_id: str
) -> AgentDefinition | None:
    """Return the cataloged definition identified by ``agent_definition_id``, or ``None``."""
    row = connection.execute(_SELECT_AGENT_DEFINITION_SQL, (agent_definition_id,)).fetchone()
    if row is None:
        return None
    return row_to_agent_definition(row)
