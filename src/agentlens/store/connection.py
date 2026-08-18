import sqlite3
from pathlib import Path
from types import TracebackType

from agentlens.errors import StoreError
from agentlens.models.agent_definitions import AgentDefinition
from agentlens.models.session_facts import SessionFacts
from agentlens.store import operations
from agentlens.store.outcomes import UpsertOutcome
from agentlens.store.schema import ensure_schema


class Store:
    """A SQLite-backed store at a caller-supplied path.

    Opening the connection, creating the schema if it is absent, and closing
    the connection are all handled by the context-manager protocol, so a
    caller cannot open the store and forget to close it.

    Args:
        path: Where the database file lives. The store does not choose a
            default location; the caller decides.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._connection: sqlite3.Connection | None = None

    def __enter__(self) -> "Store":
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self._path)
            connection.row_factory = sqlite3.Row
            ensure_schema(connection)
        except (OSError, sqlite3.Error) as exc:
            raise StoreError(f"could not open store at {self._path}") from exc
        self._connection = connection
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        connection = self._connection
        self._connection = None
        if connection is None:
            return
        try:
            connection.close()
        except sqlite3.Error as close_exc:
            raise StoreError(f"could not close store at {self._path}") from close_exc

    def upsert_session(self, facts: SessionFacts) -> UpsertOutcome:
        """Replace the stored rows for ``facts``'s session, subject to staleness.

        Raises:
            ~agentlens.errors.StoreError: The write failed, including when the
                snapshot is internally inconsistent (for example, duplicate
                tool-invocation ordinals within one session). No rows are left
                partially written in that case.
        """
        connection = self._require_connection()
        try:
            return operations.upsert_session(connection, facts)
        except sqlite3.Error as exc:
            raise StoreError(
                f"could not write session {facts.session.identity.session_id!r}"
            ) from exc

    def read_session(self, session_id: str) -> SessionFacts | None:
        """Return the stored session identified by ``session_id``, or ``None``."""
        connection = self._require_connection()
        try:
            return operations.read_session(connection, session_id)
        except sqlite3.Error as exc:
            raise StoreError(f"could not read session {session_id!r}") from exc

    def upsert_agent_definition(self, definition: AgentDefinition) -> None:
        """Catalog ``definition`` if its content-addressed identity is not already stored.

        Raises:
            ~agentlens.errors.StoreError: The write failed.
        """
        connection = self._require_connection()
        try:
            operations.upsert_agent_definition(connection, definition)
        except sqlite3.Error as exc:
            raise StoreError(
                f"could not write agent definition {definition.agent_definition_id!r}"
            ) from exc

    def read_agent_definition(self, agent_definition_id: str) -> AgentDefinition | None:
        """Return the cataloged definition identified by ``agent_definition_id``, or ``None``."""
        connection = self._require_connection()
        try:
            return operations.read_agent_definition(connection, agent_definition_id)
        except sqlite3.Error as exc:
            raise StoreError(f"could not read agent definition {agent_definition_id!r}") from exc

    def _require_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise StoreError(f"store at {self._path} is not open")
        return self._connection
