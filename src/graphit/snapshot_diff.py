"""Bounded, read-only comparison of two completed local schema snapshots."""

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from graphit.queries import QueryError
from graphit.sources import require_store_path, show_source

DEFAULT_DIFF_LIMIT = 100
MAX_DIFF_LIMIT = 500
MAX_DIFF_OBJECTS = 100_000
MAX_DIFF_FK_EDGES = 100_000
MAX_DIFF_OFFSET = 2 * MAX_DIFF_OBJECTS

ChangeType = Literal["ADDED", "REMOVED", "CHANGED"]


@dataclass(frozen=True)
class SnapshotChange:
    change: ChangeType
    kind: str
    qualified_name: str
    before: dict[str, object] | None
    after: dict[str, object] | None


@dataclass(frozen=True)
class SnapshotDiff:
    source_name: str
    from_version: int
    to_version: int
    changes: tuple[SnapshotChange, ...]
    change_count: int
    truncated: bool
    scope: str = "SCHEMA_RELATION_COLUMN_KEY_FK"
    offset: int = 0


@dataclass(frozen=True)
class _Fact:
    kind: str
    qualified_name: str
    details: dict[str, object]


def _snapshot_id(connection: sqlite3.Connection, source_name: str, version: int) -> int:
    row = connection.execute(
        """SELECT snapshots.id FROM snapshots
        JOIN sources ON sources.id = snapshots.source_id
        JOIN scan_runs ON scan_runs.id = snapshots.scan_run_id
        WHERE sources.name = ? AND snapshots.version = ?
          AND scan_runs.status = 'COMPLETED'""",
        (source_name, version),
    ).fetchone()
    if row is None:
        raise QueryError(
            "SNAPSHOT_NOT_FOUND", f"Completed snapshot version {version} was not found."
        )
    return int(row[0])


def _metadata(raw: str) -> dict[str, object]:
    value: object = json.loads(raw)
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError("Invalid saved metadata")
    return value


def _foreign_key_facts(
    connection: sqlite3.Connection,
    snapshot_id: int,
    constraints: dict[tuple[int, str], tuple[str, str, bool, bool]],
    facts: dict[str, _Fact],
) -> None:
    rows = connection.execute(
        """SELECT e.source_object_id, source.qualified_name,
            target.qualified_name, target.metadata_json,
            e.origin, e.status, e.metadata_json
        FROM edges AS e
        JOIN objects AS source ON source.id = e.source_object_id
          AND source.snapshot_id = e.snapshot_id AND source.object_type = 'TABLE'
        JOIN objects AS target ON target.id = e.target_object_id
          AND target.snapshot_id = e.snapshot_id AND target.object_type = 'TABLE'
        WHERE e.snapshot_id = ? AND e.edge_type = 'REFERENCES'
        ORDER BY e.id LIMIT ?""",
        (snapshot_id, MAX_DIFF_FK_EDGES + 1),
    ).fetchall()
    if len(rows) > MAX_DIFF_FK_EDGES:
        raise QueryError(
            "DIFF_BUDGET_EXCEEDED",
            f"Snapshot has more than {MAX_DIFF_FK_EDGES} table FK edges; diff is not partial.",
        )
    seen: set[tuple[int, str]] = set()
    for row in rows:
        edge = _metadata(str(row[6]))
        name = edge.get("constraint")
        if (
            row[4] != "DATABASE"
            or row[5] != "CONFIRMED"
            or edge.get("level") != "TABLE"
            or not isinstance(name, str)
            or not name
        ):
            raise ValueError("Invalid saved table FK edge")
        identity = (int(row[0]), name)
        if identity not in constraints or identity in seen:
            raise ValueError("Table FK edge has no unique matching constraint")
        seen.add(identity)
        key, qualified_name, validated, inherited = constraints[identity]
        target = _metadata(str(row[3]))
        target_in_scope = target.get("in_scope", True)
        pairs = edge.get("column_pairs")
        if (
            not isinstance(target_in_scope, bool)
            or not isinstance(pairs, list)
            or not pairs
            or not all(
                isinstance(pair, list)
                and len(pair) == 2
                and all(isinstance(column, str) and column for column in pair)
                for pair in pairs
            )
            or edge.get("target_in_scope") is not target_in_scope
            or edge.get("validated") is not validated
            or edge.get("inherited") is not inherited
        ):
            raise ValueError("Invalid saved table FK definition")
        facts[key] = _Fact(
            "FOREIGN_KEY",
            qualified_name,
            {
                "source_table": str(row[1]),
                "target_table": str(row[2]),
                "column_pairs": pairs,
                "target_in_scope": target_in_scope,
                "validated": validated,
                "inherited": inherited,
            },
        )
    if seen != constraints.keys():
        raise ValueError("Saved table FK constraint has no matching edge")


def _facts(connection: sqlite3.Connection, snapshot_id: int) -> dict[str, _Fact]:
    budget = connection.execute(
        "SELECT 1 FROM objects WHERE snapshot_id = ? LIMIT ?",
        (snapshot_id, MAX_DIFF_OBJECTS + 1),
    ).fetchall()
    if len(budget) > MAX_DIFF_OBJECTS:
        raise QueryError(
            "DIFF_BUDGET_EXCEEDED",
            f"Snapshot has more than {MAX_DIFF_OBJECTS} objects; diff is not partial.",
        )
    rows = connection.execute(
        """SELECT o.logical_key, o.object_type, o.qualified_name,
            o.metadata_json, c.ordinal_position, c.data_type,
            c.nullable, c.primary_key, c.unique_value,
            o.id, o.parent_id, o.object_name
        FROM objects AS o LEFT JOIN columns AS c ON c.object_id = o.id
        WHERE o.snapshot_id = ? AND o.object_type IN
          ('SCHEMA', 'TABLE', 'VIEW', 'MATERIALIZED_VIEW', 'COLUMN', 'CONSTRAINT')
        ORDER BY o.logical_key""",
        (snapshot_id,),
    ).fetchall()
    facts: dict[str, _Fact] = {}
    fk_constraints: dict[tuple[int, str], tuple[str, str, bool, bool]] = {}
    for row in rows:
        key, kind, name = str(row[0]), str(row[1]), str(row[2])
        saved = _metadata(str(row[3]))
        in_scope = saved.get("in_scope", True)
        if not isinstance(in_scope, bool):
            raise ValueError("Invalid saved scope")
        if not in_scope:
            continue  # An external FK target is a stub, not a scanned object.
        details: dict[str, object] = {}
        if kind == "CONSTRAINT":
            constraint_kind = saved.get("kind")
            inherited = saved.get("inherited")
            if not isinstance(inherited, bool):
                raise ValueError("Invalid saved constraint inheritance")
            if constraint_kind in ("PRIMARY_KEY", "UNIQUE"):
                columns = saved.get("columns")
                if (
                    not isinstance(columns, list)
                    or not columns
                    or not all(isinstance(column, str) and column for column in columns)
                ):
                    raise ValueError("Invalid saved key columns")
                details = {
                    "kind": constraint_kind,
                    "columns": columns,
                    "inherited": inherited,
                }
                kind = "KEY"
            elif constraint_kind == "FOREIGN_KEY":
                validated = saved.get("validated")
                if (
                    not isinstance(validated, bool)
                    or not isinstance(row[10], int)
                    or not isinstance(row[11], str)
                    or not row[11]
                ):
                    raise ValueError("Invalid saved FK constraint")
                identity = (row[10], row[11])
                if identity in fk_constraints:
                    raise ValueError("Duplicate saved FK constraint")
                fk_constraints[identity] = (key, name, validated, inherited)
                continue
            else:
                raise ValueError("Invalid saved constraint kind")
        elif kind == "TABLE":
            partitioned = saved.get("partitioned", False)
            if not isinstance(partitioned, bool):
                raise ValueError("Invalid saved partition flag")
            details["partitioned"] = partitioned
        elif kind == "COLUMN":
            if (
                not isinstance(row[4], int)
                or row[4] < 1
                or not isinstance(row[5], str)
                or not row[5]
                or any(value not in (0, 1) for value in row[6:9])
            ):
                raise ValueError("Invalid saved column")
            details = {
                "ordinal_position": row[4],
                "data_type": row[5],
                "nullable": bool(row[6]),
                "primary_key": bool(row[7]),
                "unique_value": bool(row[8]),
            }
        if key in facts:
            raise ValueError("Duplicate saved identity")
        facts[key] = _Fact(kind, name, details)
    _foreign_key_facts(connection, snapshot_id, fk_constraints, facts)
    return facts


def compare_snapshots(
    root: Path,
    source_name: str,
    from_version: int,
    to_version: int,
    limit: int = DEFAULT_DIFF_LIMIT,
    offset: int = 0,
) -> SnapshotDiff:
    """Compare exact saved identities; never infer renames or contact the source."""

    if from_version < 1 or to_version <= from_version:
        raise QueryError("INVALID_VERSION", "Use positive versions with from_version < to_version.")
    if not 1 <= limit <= MAX_DIFF_LIMIT:
        raise QueryError("INVALID_LIMIT", f"Limit must be between 1 and {MAX_DIFF_LIMIT}.")
    if not 0 <= offset <= MAX_DIFF_OFFSET:
        raise QueryError("INVALID_OFFSET", f"Offset must be between 0 and {MAX_DIFF_OFFSET}.")
    source = show_source(root, source_name)
    path = require_store_path(root)
    try:
        with closing(sqlite3.connect(path, timeout=5)) as connection:
            connection.execute("PRAGMA query_only = ON")
            connection.execute("BEGIN")
            before_id = _snapshot_id(connection, source.name, from_version)
            after_id = _snapshot_id(connection, source.name, to_version)
            before = _facts(connection, before_id)
            after = _facts(connection, after_id)
    except sqlite3.Error:
        raise QueryError("STORE_READ_FAILED", "Could not read the local graph store.") from None
    except (TypeError, ValueError, AttributeError):
        raise QueryError("STORE_READ_FAILED", "Local graph data is invalid.") from None

    changes: list[SnapshotChange] = []
    for key in before.keys() | after.keys():
        old, new = before.get(key), after.get(key)
        if old == new:
            continue
        fact = old if new is None else new
        assert fact is not None
        change: ChangeType = "REMOVED" if new is None else "ADDED" if old is None else "CHANGED"
        changes.append(
            SnapshotChange(
                change,
                fact.kind,
                fact.qualified_name,
                old.details if old is not None else None,
                new.details if new is not None else None,
            )
        )
    changes.sort(key=lambda item: (item.qualified_name, item.kind, item.change))
    return SnapshotDiff(
        source.name,
        from_version,
        to_version,
        tuple(changes[offset : offset + limit]),
        len(changes),
        len(changes) > offset + limit,
        offset=offset,
    )
