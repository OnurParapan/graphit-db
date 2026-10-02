"""Validated database source configuration in the local knowledge store."""

import json
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from graphit.project import CONFIG_NAME, STATE_NAME, STORE_NAME
from graphit.store import StoreError, initialize_store

_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z")
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_ENGINES = frozenset({"postgresql", "mssql", "oracle"})
_SSL_MODES = {
    "postgresql": frozenset({"disable", "prefer", "require", "verify-ca", "verify-full"}),
    "mssql": frozenset({"disable", "require"}),
    "oracle": frozenset({"disable", "require"}),
}
_CREDENTIAL_KINDS = frozenset({"password_env", "url_env", "url_dotenv"})
_DOTENV_NAMES = frozenset(
    {
        ".env",
        ".env.local",
        ".env.development",
        ".env.development.local",
        ".env.test",
        ".env.test.local",
        ".env.production",
        ".env.production.local",
    }
)


class SourceError(Exception):
    """Source configuration cannot be validated or persisted."""


class ProjectNotInitialized(SourceError):
    """The selected directory has not been initialized with Graphit."""


class SourceNotFound(SourceError):
    """No source has the requested name."""


class SourceAlreadyExists(SourceError):
    """A source already has the requested name."""


@dataclass(frozen=True)
class SourceConfig:
    """Non-secret connection details; credential_env is a variable name only."""

    name: str
    host: str
    port: int
    database_name: str
    username: str
    credential_env: str
    schemas: tuple[str, ...]
    ssl_mode: str = "prefer"
    engine: str = "postgresql"
    credential_kind: str = "password_env"
    credential_file: str | None = None


def logical_namespace(engine: str) -> str:
    """Return the stable graph-key namespace, preserving PostgreSQL v1 keys."""

    if engine == "postgresql":
        return "postgres"
    if engine in {"mssql", "oracle"}:
        return engine
    raise SourceError("Engine must be postgresql, mssql, or oracle.")


def validate_source(source: SourceConfig) -> None:
    """Reject malformed or unsafe source metadata before any SQLite write."""

    if not _NAME.fullmatch(source.name):
        raise SourceError("Source name must start with a letter and use letters, digits, _ or -.")
    if source.engine not in _ENGINES:
        raise SourceError("Engine must be postgresql, mssql, or oracle.")
    if (
        not source.host
        or source.host != source.host.strip()
        or any(char.isspace() or ord(char) < 32 or char in "@/\\,;{}" for char in source.host)
    ):
        raise SourceError("Host must be a non-empty hostname or IP address without whitespace.")
    if not 1 <= source.port <= 65535:
        raise SourceError("Port must be between 1 and 65535.")
    for label, value in (("Database", source.database_name), ("Username", source.username)):
        if not value or value != value.strip() or any(ord(char) < 32 for char in value):
            raise SourceError(f"{label} must be non-empty and contain no control characters.")
    if not _ENV_NAME.fullmatch(source.credential_env):
        raise SourceError("Credential environment variable name is invalid.")
    if source.credential_kind not in _CREDENTIAL_KINDS:
        raise SourceError("Credential kind is invalid.")
    if source.credential_kind == "url_dotenv":
        if source.credential_file not in _DOTENV_NAMES:
            raise SourceError("Credential dotenv file is not in the safe project allowlist.")
    elif source.credential_file is not None:
        raise SourceError("Credential file is valid only for a dotenv URL reference.")
    if (
        not source.schemas
        or len(source.schemas) > 100
        or len(set(source.schemas)) != len(source.schemas)
    ):
        raise SourceError("Specify 1 to 100 distinct schemas.")
    if any(
        not schema or schema != schema.strip() or any(ord(char) < 32 for char in schema)
        for schema in source.schemas
    ):
        raise SourceError("Schema names must be non-empty and contain no control characters.")
    if source.ssl_mode not in _SSL_MODES[source.engine]:
        allowed = ", ".join(sorted(_SSL_MODES[source.engine]))
        raise SourceError(f"SSL mode for {source.engine} must be one of: {allowed}.")


def require_store_path(root: Path) -> Path:
    """Return an existing, validated project store without creating one."""

    root = root.resolve()
    config = root / CONFIG_NAME
    state = root / STATE_NAME
    path = state / STORE_NAME
    if (
        not config.is_file()
        or config.is_symlink()
        or not state.is_dir()
        or state.is_symlink()
        or not path.is_file()
        or path.is_symlink()
    ):
        raise ProjectNotInitialized(f"Graphit is not initialized in {root}; run graphit init.")
    try:
        initialize_store(path)
    except StoreError as error:
        raise SourceError(f"Cannot open Graphit store: {error}") from error
    return path


def _record(row: sqlite3.Row) -> SourceConfig:
    return SourceConfig(
        name=str(row["name"]),
        engine=str(row["engine"]),
        host=str(row["host"]),
        port=int(row["port"]),
        database_name=str(row["database_name"]),
        username=str(row["username"]),
        credential_env=str(row["credential_env"]),
        schemas=tuple(json.loads(row["selected_schemas_json"])),
        ssl_mode=str(row["ssl_mode"]),
        credential_kind=str(row["credential_kind"]),
        credential_file=str(row["credential_file"]) if row["credential_file"] is not None else None,
    )


def add_source(root: Path, source: SourceConfig) -> None:
    """Persist a source, rejecting duplicate names without replacing anything."""

    validate_source(source)
    path = require_store_path(root)
    try:
        with closing(sqlite3.connect(path, timeout=5)) as connection, connection:
            connection.execute(
                """INSERT INTO sources
                (name, engine, host, port, database_name, username,
                 credential_env, ssl_mode, selected_schemas_json,
                 credential_kind, credential_file)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    source.name,
                    source.engine,
                    source.host,
                    source.port,
                    source.database_name,
                    source.username,
                    source.credential_env,
                    source.ssl_mode,
                    json.dumps(source.schemas),
                    source.credential_kind,
                    source.credential_file,
                ),
            )
    except sqlite3.IntegrityError as error:
        raise SourceAlreadyExists(f"Source '{source.name}' already exists.") from error
    except sqlite3.Error as error:
        raise SourceError(f"Cannot save source '{source.name}': {error}") from error


def list_sources(root: Path) -> tuple[SourceConfig, ...]:
    """Return sources in deterministic name order."""

    path = require_store_path(root)
    try:
        with closing(sqlite3.connect(path, timeout=5)) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute("SELECT * FROM sources ORDER BY name").fetchall()
            return tuple(_record(row) for row in rows)
    except (sqlite3.Error, ValueError, TypeError) as error:
        raise SourceError(f"Cannot read sources: {error}") from error


def show_source(root: Path, name: str) -> SourceConfig:
    """Read one source by exact name without exposing any password value."""

    path = require_store_path(root)
    try:
        with closing(sqlite3.connect(path, timeout=5)) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute("SELECT * FROM sources WHERE name = ?", (name,)).fetchone()
            if row is None:
                raise SourceNotFound(f"Source '{name}' was not found.")
            return _record(row)
    except (sqlite3.Error, ValueError, TypeError) as error:
        raise SourceError(f"Cannot read source '{name}': {error}") from error
