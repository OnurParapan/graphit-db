"""Atomic local persistence for successful structural metadata scans."""

import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from graphit.scanners.protocol import (
    DatabaseScanner,
    MetadataScanError,
    MetadataSnapshot,
    ScanScope,
    quote_identifier,
)
from graphit.scanners.registry import scanner_for
from graphit.sources import SourceConfig, logical_namespace, require_store_path, show_source


class SnapshotError(Exception):
    """A sanitized local snapshot write failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class StoredSnapshot:
    source_name: str
    snapshot_id: int
    version: int
    object_count: int
    edge_count: int
    fingerprint: str


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _identity(engine: str, kind: str, *parts: str) -> tuple[str, str]:
    qualified = ".".join(quote_identifier(part) for part in parts)
    return f"{logical_namespace(engine)}:{kind.lower()}:{qualified}", qualified


def _add_object(
    connection: sqlite3.Connection,
    snapshot_id: int,
    engine: str,
    kind: str,
    parts: tuple[str, ...],
    parent_id: int | None,
    metadata: dict[str, Any] | None = None,
) -> int:
    logical_key, qualified_name = _identity(engine, kind, *parts)
    cursor = connection.execute(
        """INSERT INTO objects
        (snapshot_id, parent_id, logical_key, object_type, schema_name,
         object_name, qualified_name, normalized_name, metadata_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            snapshot_id,
            parent_id,
            logical_key,
            kind,
            parts[0],
            parts[-1],
            qualified_name,
            parts[-1].casefold(),
            _json(metadata or {}),
        ),
    )
    if cursor.lastrowid is None:
        raise SnapshotError("STORE_WRITE_FAILED", "Could not identify a new graph object.")
    return cursor.lastrowid


def _add_edge(
    connection: sqlite3.Connection,
    snapshot_id: int,
    source_id: int,
    target_id: int,
    kind: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    connection.execute(
        """INSERT INTO edges
        (snapshot_id, source_object_id, target_object_id, edge_type,
         origin, status, confidence, metadata_json)
        VALUES (?, ?, ?, ?, 'DATABASE', 'CONFIRMED', 1.0, ?)""",
        (snapshot_id, source_id, target_id, kind, _json(metadata or {})),
    )


def _required(mapping: dict[tuple[str, ...], int], key: tuple[str, ...]) -> int:
    try:
        return mapping[key]
    except KeyError:
        raise SnapshotError(
            "INVALID_METADATA", "A scanned object references a missing parent or column."
        ) from None


def _write_graph(
    connection: sqlite3.Connection,
    snapshot_id: int,
    engine: str,
    metadata: MetadataSnapshot,
) -> None:
    schemas: dict[tuple[str, ...], int] = {}
    tables: dict[tuple[str, ...], int] = {}
    columns: dict[tuple[str, ...], int] = {}

    for schema in metadata.schemas:
        schema_key = (schema.name,)
        schemas[schema_key] = _add_object(
            connection, snapshot_id, engine, "SCHEMA", schema_key, None
        )

    for table in metadata.tables:
        if table.kind not in ("TABLE", "VIEW", "MATERIALIZED_VIEW") or (
            table.partitioned and table.kind != "TABLE"
        ):
            raise SnapshotError("INVALID_METADATA", "A scanned relation has an invalid kind.")
        table_key = (table.schema_name, table.name)
        parent = _required(schemas, (table.schema_name,))
        tables[table_key] = _add_object(
            connection,
            snapshot_id,
            engine,
            table.kind,
            table_key,
            parent,
            {"partitioned": table.partitioned, "in_scope": True}
            if table.kind == "TABLE"
            else {"in_scope": True},
        )
        _add_edge(connection, snapshot_id, parent, tables[table_key], "CONTAINS")

    primary_columns = {
        (constraint.schema_name, constraint.table_name, column)
        for constraint in metadata.keys
        if constraint.kind == "PRIMARY_KEY"
        for column in constraint.columns
    }
    single_column_unique = {
        (constraint.schema_name, constraint.table_name, column)
        for constraint in metadata.keys
        if len(constraint.columns) == 1
        for column in constraint.columns
    }
    for column in metadata.columns:
        column_key = (column.schema_name, column.table_name, column.name)
        parent = _required(tables, (column.schema_name, column.table_name))
        columns[column_key] = _add_object(
            connection, snapshot_id, engine, "COLUMN", column_key, parent
        )
        connection.execute(
            """INSERT INTO columns
            (object_id, table_object_id, ordinal_position, data_type,
             type_family, nullable, primary_key, unique_value)
            VALUES (?, ?, ?, ?, 'UNKNOWN', ?, ?, ?)""",
            (
                columns[column_key],
                parent,
                column.ordinal_position,
                column.data_type,
                int(column.nullable),
                int(column_key in primary_columns),
                int(column_key in single_column_unique),
            ),
        )
        _add_edge(connection, snapshot_id, parent, columns[column_key], "HAS_COLUMN")

    for constraint in metadata.keys:
        table_id = _required(tables, (constraint.schema_name, constraint.table_name))
        constraint_id = _add_object(
            connection,
            snapshot_id,
            engine,
            "CONSTRAINT",
            (constraint.schema_name, constraint.table_name, constraint.name),
            table_id,
            {
                "kind": constraint.kind,
                "columns": constraint.columns,
                "inherited": constraint.inherited,
            },
        )
        _add_edge(connection, snapshot_id, table_id, constraint_id, "CONTAINS")
        for name in constraint.columns:
            column_id = _required(columns, (constraint.schema_name, constraint.table_name, name))
            edge_kind = "UNIQUE_KEY" if constraint.kind == "UNIQUE" else constraint.kind
            _add_edge(connection, snapshot_id, constraint_id, column_id, edge_kind)

    indexable_relations = {
        (table.schema_name, table.name)
        for table in metadata.tables
        if table.kind in ("TABLE", "MATERIALIZED_VIEW")
    }
    if len(metadata.indexes) > 100000:
        raise SnapshotError("INVALID_METADATA", "A scan contains too many indexes.")
    for index in metadata.indexes:
        table_key = (index.schema_name, index.table_name)
        if (
            table_key not in indexable_relations
            or not index.name
            or not index.access_method
            or not index.key_columns
            or any(name is not None and not name for name in index.key_columns)
            or any(not name for name in index.included_columns)
            or any(
                not isinstance(value, bool)
                for value in (
                    index.unique,
                    index.primary,
                    index.valid,
                    index.ready,
                    index.partial,
                )
            )
            or index.primary
            and not index.unique
        ):
            raise SnapshotError("INVALID_METADATA", "A scanned index has invalid metadata.")
        table_id = _required(tables, table_key)
        index_id = _add_object(
            connection,
            snapshot_id,
            engine,
            "INDEX",
            (*table_key, index.name),
            table_id,
            {
                "access_method": index.access_method,
                "key_columns": index.key_columns,
                "included_columns": index.included_columns,
                "unique": index.unique,
                "primary": index.primary,
                "valid": index.valid,
                "ready": index.ready,
                "partial": index.partial,
            },
        )
        _add_edge(connection, snapshot_id, table_id, index_id, "CONTAINS")
        for position, key_name in enumerate(index.key_columns, start=1):
            if key_name is not None:
                column_id = _required(columns, (*table_key, key_name))
                _add_edge(
                    connection,
                    snapshot_id,
                    index_id,
                    column_id,
                    "INDEX_KEY",
                    {"position": position},
                )
        for position, included_name in enumerate(index.included_columns, start=1):
            column_id = _required(columns, (*table_key, included_name))
            _add_edge(
                connection,
                snapshot_id,
                index_id,
                column_id,
                "INDEX_INCLUDE",
                {"position": position},
            )

    for foreign_key in metadata.foreign_keys:
        source_table_key = (foreign_key.source_schema, foreign_key.source_table)
        source_table_id = _required(tables, source_table_key)
        target_table_key = (foreign_key.target_schema, foreign_key.target_table)
        if foreign_key.target_in_scope:
            target_table_id = _required(tables, target_table_key)
        else:
            if target_table_key in tables:
                raise SnapshotError("INVALID_METADATA", "Foreign-key scope information conflicts.")
            schema_key = (foreign_key.target_schema,)
            if schema_key not in schemas:
                schemas[schema_key] = _add_object(
                    connection,
                    snapshot_id,
                    engine,
                    "SCHEMA",
                    schema_key,
                    None,
                    {"in_scope": False},
                )
            target_table_id = tables.get(target_table_key, 0)
            if not target_table_id:
                target_table_id = _add_object(
                    connection,
                    snapshot_id,
                    engine,
                    "TABLE",
                    target_table_key,
                    schemas[schema_key],
                    {"in_scope": False},
                )
                tables[target_table_key] = target_table_id
                _add_edge(connection, snapshot_id, schemas[schema_key], target_table_id, "CONTAINS")

        constraint_id = _add_object(
            connection,
            snapshot_id,
            engine,
            "CONSTRAINT",
            (*source_table_key, foreign_key.name),
            source_table_id,
            {
                "kind": "FOREIGN_KEY",
                "validated": foreign_key.validated,
                "inherited": foreign_key.inherited,
            },
        )
        _add_edge(connection, snapshot_id, source_table_id, constraint_id, "CONTAINS")
        pairs = list(zip(foreign_key.source_columns, foreign_key.target_columns, strict=True))
        _add_edge(
            connection,
            snapshot_id,
            source_table_id,
            target_table_id,
            "REFERENCES",
            {
                "constraint": foreign_key.name,
                "column_pairs": pairs,
                "target_in_scope": foreign_key.target_in_scope,
                "validated": foreign_key.validated,
                "inherited": foreign_key.inherited,
                "level": "TABLE",
            },
        )
        for source_name, target_name in pairs:
            source_column_id = _required(columns, (*source_table_key, source_name))
            if foreign_key.target_in_scope:
                target_column_id = _required(columns, (*target_table_key, target_name))
                _add_edge(
                    connection,
                    snapshot_id,
                    source_column_id,
                    target_column_id,
                    "REFERENCES",
                    {"constraint": foreign_key.name, "level": "COLUMN"},
                )


def persist_snapshot(
    root: Path, source: SourceConfig, metadata: MetadataSnapshot
) -> StoredSnapshot:
    """Commit a complete new snapshot, or leave all earlier snapshots intact."""

    if metadata.source_name != source.name:
        raise SnapshotError("INVALID_METADATA", "Scan result belongs to a different source.")
    path = require_store_path(root)
    fingerprint = hashlib.sha256(_json(asdict(metadata)).encode("utf-8")).hexdigest()
    try:
        with closing(sqlite3.connect(path, timeout=5)) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 5000")
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT id FROM sources WHERE name = ?", (source.name,)
                ).fetchone()
                if row is None:
                    raise SnapshotError("SOURCE_NOT_FOUND", "Configured source was not found.")
                source_id = int(row[0])
                version = int(
                    connection.execute(
                        "SELECT COALESCE(MAX(version), 0) + 1 FROM snapshots WHERE source_id = ?",
                        (source_id,),
                    ).fetchone()[0]
                )
                run_id = connection.execute(
                    "INSERT INTO scan_runs(source_id, status) VALUES (?, 'WRITING_SNAPSHOT')",
                    (source_id,),
                ).lastrowid
                snapshot_id = connection.execute(
                    """INSERT INTO snapshots
                    (source_id, scan_run_id, version, schema_fingerprint)
                    VALUES (?, ?, ?, ?)""",
                    (source_id, run_id, version, fingerprint),
                ).lastrowid
                if snapshot_id is None:
                    raise SnapshotError("STORE_WRITE_FAILED", "Could not create a snapshot.")
                _write_graph(connection, snapshot_id, source.engine, metadata)
                object_count = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM objects WHERE snapshot_id = ?", (snapshot_id,)
                    ).fetchone()[0]
                )
                edge_count = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM edges WHERE snapshot_id = ?", (snapshot_id,)
                    ).fetchone()[0]
                )
                connection.execute(
                    """UPDATE scan_runs SET status = 'COMPLETED',
                    completed_at = CURRENT_TIMESTAMP, object_count = ?, edge_count = ?
                    WHERE id = ?""",
                    (object_count, edge_count, run_id),
                )
                connection.commit()
                return StoredSnapshot(
                    source.name, snapshot_id, version, object_count, edge_count, fingerprint
                )
            except (sqlite3.Error, SnapshotError, ValueError):
                connection.rollback()
                raise
    except sqlite3.Error:
        raise SnapshotError(
            "STORE_WRITE_FAILED", "Could not persist the snapshot; prior data is unchanged."
        ) from None
    except ValueError:
        raise SnapshotError("INVALID_METADATA", "A scanned relationship is incomplete.") from None


def _record_failed_scan(root: Path, name: str, error: MetadataScanError) -> None:
    path = require_store_path(root)
    try:
        with closing(sqlite3.connect(path, timeout=5)) as connection, connection:
            connection.execute(
                """INSERT INTO scan_runs
                (source_id, status, completed_at, error_code, error_message)
                SELECT id, 'FAILED', CURRENT_TIMESTAMP, ?, ?
                FROM sources WHERE name = ?""",
                (error.code, str(error), name),
            )
    except sqlite3.Error:
        raise SnapshotError("STORE_WRITE_FAILED", "Could not record the failed scan.") from None


def scan_source(root: Path, name: str, *, scanner: DatabaseScanner | None = None) -> StoredSnapshot:
    """Scan one configured source and persist only a complete successful result."""

    source = show_source(root, name)
    adapter = scanner if scanner is not None else scanner_for(source, root)
    try:
        metadata = adapter.scan_metadata(source, ScanScope(source.schemas))
    except MetadataScanError as error:
        _record_failed_scan(root, name, error)
        raise
    return persist_snapshot(root, source, metadata)
