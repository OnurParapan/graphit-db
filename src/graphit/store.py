"""Versioned, project-local SQLite knowledge store."""

import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path


class StoreError(Exception):
    """The local knowledge store cannot be safely opened or migrated."""


@dataclass(frozen=True)
class Migration:
    version: int
    statements: tuple[str, ...]

    @property
    def checksum(self) -> str:
        payload = "\n-- statement --\n".join(self.statements).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


MIGRATIONS = (
    Migration(
        1,
        (
            """CREATE TABLE store_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                checksum TEXT NOT NULL
            )""",
            """CREATE TABLE sources (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                engine TEXT NOT NULL,
                database_name TEXT NOT NULL,
                host TEXT NOT NULL,
                port INTEGER NOT NULL CHECK (port BETWEEN 1 AND 65535),
                username TEXT NOT NULL,
                credential_env TEXT NOT NULL,
                ssl_mode TEXT NOT NULL,
                selected_schemas_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""",
            """CREATE TABLE scan_runs (
                id INTEGER PRIMARY KEY,
                source_id INTEGER NOT NULL REFERENCES sources(id),
                status TEXT NOT NULL,
                started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                completed_at TEXT,
                error_code TEXT,
                error_message TEXT,
                object_count INTEGER NOT NULL DEFAULT 0,
                edge_count INTEGER NOT NULL DEFAULT 0
            )""",
            """CREATE TABLE snapshots (
                id INTEGER PRIMARY KEY,
                source_id INTEGER NOT NULL REFERENCES sources(id),
                scan_run_id INTEGER NOT NULL UNIQUE REFERENCES scan_runs(id),
                version INTEGER NOT NULL,
                schema_fingerprint TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (source_id, version)
            )""",
            """CREATE TABLE objects (
                id INTEGER PRIMARY KEY,
                snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),
                parent_id INTEGER REFERENCES objects(id),
                logical_key TEXT NOT NULL,
                object_type TEXT NOT NULL,
                schema_name TEXT,
                object_name TEXT NOT NULL,
                qualified_name TEXT NOT NULL,
                normalized_name TEXT NOT NULL,
                source_identifier TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                UNIQUE (snapshot_id, logical_key)
            )""",
            """CREATE TABLE columns (
                object_id INTEGER PRIMARY KEY REFERENCES objects(id),
                table_object_id INTEGER NOT NULL REFERENCES objects(id),
                ordinal_position INTEGER NOT NULL,
                data_type TEXT NOT NULL,
                type_family TEXT NOT NULL,
                nullable INTEGER NOT NULL CHECK (nullable IN (0, 1)),
                default_expression TEXT,
                primary_key INTEGER NOT NULL CHECK (primary_key IN (0, 1)),
                unique_value INTEGER NOT NULL CHECK (unique_value IN (0, 1)),
                statistics_json TEXT
            )""",
            """CREATE TABLE edges (
                id INTEGER PRIMARY KEY,
                snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),
                source_object_id INTEGER NOT NULL REFERENCES objects(id),
                target_object_id INTEGER NOT NULL REFERENCES objects(id),
                edge_type TEXT NOT NULL,
                origin TEXT NOT NULL,
                status TEXT NOT NULL,
                confidence REAL NOT NULL CHECK (confidence BETWEEN 0.0 AND 1.0),
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""",
            """CREATE TABLE relationship_evidence (
                id INTEGER PRIMARY KEY,
                edge_id INTEGER NOT NULL REFERENCES edges(id),
                evidence_type TEXT NOT NULL,
                score REAL NOT NULL CHECK (score BETWEEN 0.0 AND 1.0),
                details_json TEXT NOT NULL DEFAULT '{}'
            )""",
            """CREATE TABLE review_decisions (
                id INTEGER PRIMARY KEY,
                source_logical_key TEXT NOT NULL,
                target_logical_key TEXT NOT NULL,
                relationship_kind TEXT NOT NULL,
                decision TEXT NOT NULL,
                comment TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""",
            "CREATE INDEX objects_by_type ON objects(snapshot_id, object_type)",
            "CREATE INDEX objects_by_name ON objects(snapshot_id, qualified_name)",
            "CREATE INDEX objects_by_normalized_name ON objects(snapshot_id, normalized_name)",
            "CREATE INDEX edges_by_source ON edges(snapshot_id, source_object_id)",
            "CREATE INDEX edges_by_target ON edges(snapshot_id, target_object_id)",
            "CREATE INDEX edges_by_type_status ON edges(snapshot_id, edge_type, status)",
        ),
    ),
    Migration(
        2,
        (
            "ALTER TABLE sources ADD COLUMN credential_kind TEXT NOT NULL DEFAULT 'password_env'",
            "ALTER TABLE sources ADD COLUMN credential_file TEXT",
        ),
    ),
)

SCHEMA_VERSION = MIGRATIONS[-1].version


def initialize_store(path: Path) -> bool:
    """Create or validate a store; apply pending migrations as one transaction.

    Return whether this call created the SQLite file. Existing data is never
    replaced. An unknown or tampered schema fails closed.
    """

    if path.is_symlink():
        raise StoreError(f"Refusing symlinked store: {path}")
    if not path.parent.is_dir():
        raise StoreError(f"Store directory does not exist: {path.parent}")
    if path.exists() and not path.is_file():
        raise StoreError(f"Store path is not a regular file: {path}")

    created = not path.exists()
    try:
        connection = sqlite3.connect(path, timeout=5)
    except sqlite3.Error as error:
        raise StoreError(f"Cannot open local store at {path}: {error}") from error

    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("BEGIN IMMEDIATE")
        current = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if current > SCHEMA_VERSION:
            raise StoreError(
                f"Store schema version {current} is newer than supported "
                f"version {SCHEMA_VERSION}; upgrade Graphit."
            )
        if current == 0:
            existing = connection.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view') "
                "AND name NOT LIKE 'sqlite_%' LIMIT 1"
            ).fetchone()
            if existing is not None:
                raise StoreError("Unversioned SQLite database is not a Graphit store.")
        else:
            recorded = dict(connection.execute("SELECT version, checksum FROM store_migrations"))
            for migration in MIGRATIONS:
                if (
                    migration.version <= current
                    and recorded.get(migration.version) != migration.checksum
                ):
                    raise StoreError(f"Store migration {migration.version} is missing or changed.")
            if len(recorded) != current:
                raise StoreError("Store migration history does not match its schema version.")

        for migration in MIGRATIONS:
            if migration.version <= current:
                continue
            if migration.version != current + 1:
                raise StoreError("Store migrations must be contiguous.")
            for statement in migration.statements:
                connection.execute(statement)
            connection.execute(
                "INSERT INTO store_migrations(version, checksum) VALUES (?, ?)",
                (migration.version, migration.checksum),
            )
            connection.execute(f"PRAGMA user_version = {migration.version}")
            current = migration.version
        connection.commit()
    except (sqlite3.Error, StoreError) as error:
        connection.rollback()
        if isinstance(error, StoreError):
            raise
        raise StoreError(f"Cannot initialize local store at {path}: {error}") from error
    finally:
        connection.close()
    return created
