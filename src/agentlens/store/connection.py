import os
import sqlite3
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from types import TracebackType

from agentlens.errors import StoreError
from agentlens.models.agent_definitions import AgentDefinition
from agentlens.models.facts import FactSession, FactVerdict
from agentlens.models.report_aggregates import AgentRollup
from agentlens.models.session_facts import SessionFacts
from agentlens.models.skill_signals import SessionSkillSignal
from agentlens.models.windows import DEFAULT_MIN_SESSIONS_FOR_TREND
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

    def upsert_batch(
        self, *, definitions: Sequence[AgentDefinition], facts: Sequence[SessionFacts]
    ) -> tuple[UpsertOutcome, ...]:
        """Apply every definition and session as one all-or-nothing transaction.

        Raises:
            ~agentlens.errors.StoreError: The batch could not be written. No
                row from any definition or session in the batch is left
                partially written; the store is exactly as it was before this
                call.
        """
        connection = self._require_connection()
        try:
            return operations.upsert_batch(connection, definitions=definitions, facts=facts)
        except sqlite3.Error as exc:
            raise StoreError(f"could not write batch of {len(facts)} session(s)") from exc

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

    def upsert_verdict(self, verdict: FactVerdict) -> None:
        """Write ``verdict``, replacing any row already stored under its natural key.

        Raises:
            ~agentlens.errors.StoreError: The write failed.
        """
        connection = self._require_connection()
        try:
            operations.upsert_verdict(connection, verdict)
        except sqlite3.Error as exc:
            raise StoreError(f"could not write verdict for session {verdict.session_id!r}") from exc

    def read_verdicts_for_session(self, session_id: str) -> tuple[FactVerdict, ...]:
        """Return every stored verdict for ``session_id``, or an empty tuple if it has none.

        Raises:
            ~agentlens.errors.StoreError: The read failed.
        """
        connection = self._require_connection()
        try:
            return operations.read_verdicts_for_session(connection, session_id)
        except sqlite3.Error as exc:
            raise StoreError(f"could not read verdicts for session {session_id!r}") from exc

    def read_spawns_in_window(
        self, start: datetime, end: datetime, agent_type: str | None
    ) -> tuple[FactSession, ...]:
        """Return every subagent spawn whose ``started_at`` falls in ``[start, end)``.

        ``agent_type`` narrows the result to one agent type; ``None`` returns
        every subagent spawn in the window. Main-session rows never qualify.

        Raises:
            ~agentlens.errors.StoreError: The read failed.
        """
        connection = self._require_connection()
        try:
            return operations.read_spawns_in_window(connection, start, end, agent_type)
        except sqlite3.Error as exc:
            raise StoreError(f"could not read spawns in window [{start}, {end})") from exc

    def read_skill_signals_for_sessions(
        self, session_ids: Sequence[str]
    ) -> Mapping[str, tuple[SessionSkillSignal, ...]]:
        """Return skill-bridge rows for every id in ``session_ids``, grouped by session id.

        One parameterized query covers the whole sequence; a session id
        with no skill-bridge rows is absent from the result rather than
        mapped to an empty tuple. Returns an empty mapping without
        querying when ``session_ids`` is empty.

        Raises:
            ~agentlens.errors.StoreError: The read failed.
        """
        connection = self._require_connection()
        try:
            return operations.read_skill_signals_for_sessions(connection, session_ids)
        except sqlite3.Error as exc:
            raise StoreError(
                f"could not read skill signals for {len(session_ids)} session(s)"
            ) from exc

    def read_agent_rollups(
        self,
        current_start: datetime,
        current_end: datetime,
        prior_start: datetime,
        prior_end: datetime,
        agent_type: str | None,
        *,
        min_sessions_for_trend: int = DEFAULT_MIN_SESSIONS_FOR_TREND,
    ) -> tuple[AgentRollup, ...]:
        """Return one rollup per agent type present in the current window.

        Each rollup carries the current window's totals and per-spawn
        averages, plus the prior window's comparison when both windows meet
        ``min_sessions_for_trend``. An agent type with zero current-window
        spawns never gets a rollup, even if it has prior-window spawns.

        Raises:
            ~agentlens.errors.StoreError: The read failed.
        """
        connection = self._require_connection()
        try:
            return operations.read_agent_rollups(
                connection,
                current_start,
                current_end,
                prior_start,
                prior_end,
                agent_type,
                min_sessions_for_trend=min_sessions_for_trend,
            )
        except sqlite3.Error as exc:
            raise StoreError(
                f"could not read agent rollups for window [{current_start}, {current_end})"
            ) from exc

    def _require_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise StoreError(f"store at {self._path} is not open")
        return self._connection


@contextmanager
def open_disposable_clone(source_path: Path) -> Iterator[Store]:
    """Open a disposable temporary clone of the store at ``source_path``.

    Clones every row from ``source_path`` with SQLite's own backup API when
    that file exists, or starts an empty schema when it does not, so a
    caller can apply speculative writes without touching the configured
    store or creating one where none exists. ``source_path`` is opened
    read-only for the clone, so cloning can never modify it. The temporary
    database is closed and removed when the context exits, whether it
    exits normally or through an exception.

    Raises:
        ~agentlens.errors.StoreError: The temporary database could not be
            created, the source could not be cloned, or the store could not
            be opened.
    """
    try:
        descriptor, temp_name = tempfile.mkstemp(suffix=".sqlite3", prefix="agentlens-clone-")
        os.close(descriptor)
    except OSError as exc:
        raise StoreError("could not create a temporary store clone") from exc

    temp_path = Path(temp_name)
    try:
        if source_path.exists():
            _clone_into(source_path, temp_path)
        with Store(temp_path) as store:
            yield store
    finally:
        temp_path.unlink(missing_ok=True)


def _clone_into(source_path: Path, destination_path: Path) -> None:
    try:
        source_connection = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
        try:
            destination_connection = sqlite3.connect(destination_path)
            try:
                source_connection.backup(destination_connection)
            finally:
                destination_connection.close()
        finally:
            source_connection.close()
    except sqlite3.Error as exc:
        raise StoreError(f"could not clone store at {source_path}") from exc
