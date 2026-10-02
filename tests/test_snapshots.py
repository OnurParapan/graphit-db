"""Immutable SQLite snapshot persistence and rollback tests."""

import json
import sqlite3
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import pytest

from graphit.project import initialize_project
from graphit.queries import QueryError, get_relevant_context, search_objects, show_table
from graphit.scanners.postgresql import MetadataScanError
from graphit.scanners.protocol import (
    ColumnMetadata,
    ForeignKeyMetadata,
    IndexMetadata,
    KeyConstraintMetadata,
    MetadataSnapshot,
    ScanScope,
    SchemaMetadata,
    TableMetadata,
)
from graphit.snapshots import SnapshotError, persist_snapshot, scan_source
from graphit.sources import SourceConfig, add_source


def _source() -> SourceConfig:
    return SourceConfig(
        name="erp",
        host="localhost",
        port=5432,
        database_name="erp",
        username="reader",
        credential_env="ERP_DB_PASSWORD",
        schemas=("public",),
    )


def _metadata() -> MetadataSnapshot:
    return MetadataSnapshot(
        source_name="erp",
        schemas=(SchemaMetadata("public"),),
        tables=(
            TableMetadata("public", "Customer", False),
            TableMetadata("public", "Order", False),
        ),
        columns=(
            ColumnMetadata("public", "Customer", "ID", 1, "integer", False),
            ColumnMetadata("public", "Order", "ID", 1, "integer", False),
            ColumnMetadata("public", "Order", "customer_id", 2, "integer", False),
        ),
        keys=(
            KeyConstraintMetadata(
                "public", "Customer", "customer_pkey", "PRIMARY_KEY", ("ID",), False
            ),
            KeyConstraintMetadata("public", "Order", "order_pkey", "PRIMARY_KEY", ("ID",), False),
            KeyConstraintMetadata(
                "public", "Order", "order_customer_unique", "UNIQUE", ("customer_id",), False
            ),
        ),
        foreign_keys=(
            ForeignKeyMetadata(
                "public",
                "Order",
                "order_customer_fk",
                ("customer_id",),
                "public",
                "Customer",
                ("ID",),
                True,
                True,
                False,
            ),
            ForeignKeyMetadata(
                "public",
                "Order",
                "order_external_fk",
                ("customer_id",),
                "external",
                "Customer",
                ("ID",),
                False,
                False,
                False,
            ),
        ),
    )


def _project(tmp_path: Path) -> SourceConfig:
    initialize_project(tmp_path)
    source = _source()
    add_source(tmp_path, source)
    return source


def _index() -> IndexMetadata:
    return IndexMetadata(
        schema_name="public",
        table_name="Order",
        name="order_customer_lookup",
        access_method="btree",
        key_columns=("customer_id", None),
        included_columns=("ID",),
        unique=False,
        primary=False,
        valid=True,
        ready=True,
        partial=True,
    )


@pytest.mark.parametrize(
    ("engine", "port", "ssl_mode"),
    [("mssql", 1433, "require"), ("oracle", 1521, "disable")],
)
def test_snapshot_logical_keys_use_the_source_engine_namespace(
    tmp_path: Path, engine: str, port: int, ssl_mode: str
) -> None:
    initialize_project(tmp_path)
    source = replace(_source(), engine=engine, port=port, ssl_mode=ssl_mode)
    add_source(tmp_path, source)

    persist_snapshot(tmp_path, source, _metadata())

    with sqlite3.connect(tmp_path / ".graphit" / "graphit.db") as connection:
        keys = [
            str(row[0])
            for row in connection.execute(
                "SELECT logical_key FROM objects ORDER BY logical_key"
            ).fetchall()
        ]
    assert keys
    assert all(key.startswith(f"{engine}:") for key in keys)


def test_persist_complete_graph_with_external_target(tmp_path: Path) -> None:
    source = _project(tmp_path)

    result = persist_snapshot(tmp_path, source, _metadata())

    assert result.version == 1
    assert result.object_count > 0
    assert result.edge_count > 0
    path = tmp_path / ".graphit" / "graphit.db"
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("SELECT status FROM scan_runs").fetchone() == ("COMPLETED",)
        assert connection.execute("SELECT version FROM snapshots").fetchone() == (1,)
        assert (
            connection.execute("SELECT COUNT(*) FROM objects").fetchone()[0] == result.object_count
        )
        assert connection.execute("SELECT COUNT(*) FROM edges").fetchone()[0] == result.edge_count
        flags = connection.execute(
            """SELECT primary_key, unique_value FROM columns
            JOIN objects ON objects.id = columns.object_id
            WHERE objects.qualified_name = '"public"."Order"."customer_id"'"""
        ).fetchone()
        assert flags == (0, 1)
        customer_pk_flags = connection.execute(
            """SELECT primary_key, unique_value FROM columns
            JOIN objects ON objects.id = columns.object_id
            WHERE objects.qualified_name = '"public"."Customer"."ID"'"""
        ).fetchone()
        assert customer_pk_flags == (1, 1)
        rows = connection.execute(
            """SELECT edge_type, metadata_json FROM edges
            WHERE edge_type = 'REFERENCES'"""
        ).fetchall()
        assert len(rows) == 3  # two table FKs plus one in-scope column FK
        edge_details = [json.loads(row[1]) for row in rows]
        assert any(item.get("target_in_scope") is False for item in edge_details)
        assert any(item.get("level") == "COLUMN" for item in edge_details)
        external = connection.execute(
            """SELECT metadata_json FROM objects
            WHERE object_type = 'TABLE' AND qualified_name = '"external"."Customer"'"""
        ).fetchone()
        assert external is not None
        assert json.loads(external[0])["in_scope"] is False
        assert (
            connection.execute(
                """SELECT COUNT(*) FROM objects
            WHERE qualified_name LIKE '%"external"."Customer".%'"""
            ).fetchone()[0]
            == 0
        )


def test_rescan_creates_new_version_without_changing_history(tmp_path: Path) -> None:
    source = _project(tmp_path)
    first = persist_snapshot(tmp_path, source, _metadata())
    second = persist_snapshot(tmp_path, source, _metadata())

    assert (first.version, second.version) == (1, 2)
    assert first.snapshot_id != second.snapshot_id
    assert first.fingerprint == second.fingerprint
    path = tmp_path / ".graphit" / "graphit.db"
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 2
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM objects WHERE snapshot_id = ?", (first.snapshot_id,)
            ).fetchone()[0]
            == first.object_count
        )
        assert (
            connection.execute(
                "SELECT MAX(version) FROM snapshots JOIN scan_runs "
                "ON scan_runs.id = snapshots.scan_run_id WHERE scan_runs.status = 'COMPLETED'"
            ).fetchone()[0]
            == 2
        )


def test_index_facts_are_immutable_and_do_not_enter_default_search(tmp_path: Path) -> None:
    source = _project(tmp_path)
    first = persist_snapshot(tmp_path, source, _metadata())
    second = persist_snapshot(tmp_path, source, replace(_metadata(), indexes=(_index(),)))
    path = tmp_path / ".graphit" / "graphit.db"

    assert first.fingerprint != second.fingerprint
    with closing(sqlite3.connect(path)) as connection:
        rows = connection.execute(
            """SELECT snapshot_id, id, parent_id, qualified_name, metadata_json
            FROM objects WHERE object_type = 'INDEX'"""
        ).fetchall()
        assert len(rows) == 1
        snapshot_id, index_id, parent_id, qualified, payload = rows[0]
        assert snapshot_id == second.snapshot_id
        assert qualified == '"public"."Order"."order_customer_lookup"'
        assert json.loads(payload) == {
            "access_method": "btree",
            "key_columns": ["customer_id", None],
            "included_columns": ["ID"],
            "unique": False,
            "primary": False,
            "valid": True,
            "ready": True,
            "partial": True,
        }
        assert connection.execute(
            "SELECT object_type, qualified_name FROM objects WHERE id = ?", (parent_id,)
        ).fetchone() == ("TABLE", '"public"."Order"')
        edges = connection.execute(
            """SELECT e.edge_type, target.qualified_name, e.metadata_json
            FROM edges AS e JOIN objects AS target ON target.id = e.target_object_id
            WHERE e.source_object_id = ? ORDER BY e.edge_type""",
            (index_id,),
        ).fetchall()
        assert [(kind, name, json.loads(value)) for kind, name, value in edges] == [
            ("INDEX_INCLUDE", '"public"."Order"."ID"', {"position": 1}),
            ("INDEX_KEY", '"public"."Order"."customer_id"', {"position": 1}),
        ]
        assert connection.execute(
            """SELECT COUNT(*) FROM edges WHERE snapshot_id = ? AND edge_type = 'CONTAINS'
            AND source_object_id = ? AND target_object_id = ?""",
            (second.snapshot_id, parent_id, index_id),
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT COUNT(*) FROM objects WHERE snapshot_id = ? AND object_type = 'INDEX'",
            (first.snapshot_id,),
        ).fetchone() == (0,)
    assert search_objects(tmp_path, "erp", "order_customer_lookup").matches == ()


def test_materialized_view_index_is_saved_but_view_index_is_rejected(tmp_path: Path) -> None:
    source = _project(tmp_path)
    materialized = replace(
        _metadata(),
        tables=_metadata().tables + (TableMetadata("public", "Cache", False, "MATERIALIZED_VIEW"),),
        columns=_metadata().columns
        + (ColumnMetadata("public", "Cache", "ID", 1, "integer", False),),
        indexes=(replace(_index(), table_name="Cache", key_columns=("ID",), included_columns=()),),
    )
    saved = persist_snapshot(tmp_path, source, materialized)
    with closing(sqlite3.connect(tmp_path / ".graphit" / "graphit.db")) as connection:
        assert connection.execute(
            """SELECT parent.object_type FROM objects AS idx
            JOIN objects AS parent ON parent.id = idx.parent_id
            WHERE idx.snapshot_id = ? AND idx.object_type = 'INDEX'""",
            (saved.snapshot_id,),
        ).fetchone() == ("MATERIALIZED_VIEW",)

    invalid = replace(
        materialized,
        tables=materialized.tables + (TableMetadata("public", "Report", False, "VIEW"),),
        columns=materialized.columns
        + (ColumnMetadata("public", "Report", "ID", 1, "integer", False),),
        indexes=(replace(_index(), table_name="Report", key_columns=("ID",)),),
    )
    with pytest.raises(SnapshotError) as raised:
        persist_snapshot(tmp_path, source, invalid)
    assert raised.value.code == "INVALID_METADATA"
    with closing(sqlite3.connect(tmp_path / ".graphit" / "graphit.db")) as connection:
        assert connection.execute("SELECT COUNT(*) FROM snapshots").fetchone() == (1,)


def test_invalid_index_reference_rolls_back_entire_new_snapshot(tmp_path: Path) -> None:
    source = _project(tmp_path)
    first = persist_snapshot(tmp_path, source, replace(_metadata(), indexes=(_index(),)))
    broken = replace(
        _metadata(),
        indexes=(replace(_index(), included_columns=("missing_column",)),),
    )

    with pytest.raises(SnapshotError) as raised:
        persist_snapshot(tmp_path, source, broken)
    assert raised.value.code == "INVALID_METADATA"
    with closing(sqlite3.connect(tmp_path / ".graphit" / "graphit.db")) as connection:
        assert connection.execute("SELECT COUNT(*) FROM snapshots").fetchone() == (1,)
        assert connection.execute("SELECT COUNT(*) FROM scan_runs").fetchone() == (1,)
        assert connection.execute(
            "SELECT COUNT(*) FROM objects WHERE object_type = 'INDEX'"
        ).fetchone() == (1,)
        assert connection.execute("SELECT COUNT(*) FROM edges").fetchone() == (first.edge_count,)


def test_view_kinds_and_columns_are_searchable_in_latest_immutable_snapshot(
    tmp_path: Path,
) -> None:
    source = _project(tmp_path)
    metadata = replace(
        _metadata(),
        tables=_metadata().tables
        + (
            TableMetadata("public", "Revenue_View", False, "VIEW"),
            TableMetadata("public", "Daily_Cache", False, "MATERIALIZED_VIEW"),
        ),
        columns=_metadata().columns
        + (
            ColumnMetadata("public", "Revenue_View", "revenue_total", 1, "numeric", True),
            ColumnMetadata("public", "Daily_Cache", "cached_total", 1, "numeric", True),
        ),
    )
    first = persist_snapshot(tmp_path, source, metadata)
    path = tmp_path / ".graphit" / "graphit.db"
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            """SELECT object_type, qualified_name FROM objects
            WHERE snapshot_id = ? AND object_type IN ('VIEW', 'MATERIALIZED_VIEW')
            ORDER BY qualified_name""",
            (first.snapshot_id,),
        ).fetchall()
        assert rows == [
            ("MATERIALIZED_VIEW", '"public"."Daily_Cache"'),
            ("VIEW", '"public"."Revenue_View"'),
        ]
        view_columns = connection.execute(
            """SELECT child.object_name FROM columns AS c
            JOIN objects AS child ON child.id = c.object_id
            JOIN objects AS parent ON parent.id = c.table_object_id
            WHERE parent.snapshot_id = ? AND parent.object_type = 'VIEW'""",
            (first.snapshot_id,),
        ).fetchall()
        assert view_columns == [("revenue_total",)]

    view_matches = search_objects(tmp_path, "erp", "Revenue_View").matches
    assert [(item.kind, item.qualified_name) for item in view_matches] == [
        ("VIEW", '"public"."Revenue_View"'),
        ("COLUMN", '"public"."Revenue_View"."revenue_total"'),
    ]
    assert search_objects(tmp_path, "erp", "Daily_Cache").matches[0].kind == "MATERIALIZED_VIEW"
    context = get_relevant_context(tmp_path, "erp", "Revenue_View revenue_total")
    assert any(item.kind == "VIEW" for item in context.objects)
    assert any(item.kind == "COLUMN" for item in context.objects)
    assert any(
        item.tool == "get_view" and item.arguments["name"] == '"public"."Revenue_View"'
        for item in context.suggested_followups
    )
    with pytest.raises(QueryError) as raised:
        show_table(tmp_path, "erp", "public.Revenue_View")
    assert raised.value.code == "TABLE_NOT_FOUND"

    second = persist_snapshot(tmp_path, source, _metadata())
    assert second.version == 2
    assert search_objects(tmp_path, "erp", "Revenue_View").matches == ()
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM objects WHERE snapshot_id = ? AND object_type = 'VIEW'",
            (first.snapshot_id,),
        ).fetchone() == (1,)


def test_invalid_view_partition_flag_rolls_back_new_snapshot(tmp_path: Path) -> None:
    source = _project(tmp_path)
    first = persist_snapshot(tmp_path, source, _metadata())
    broken = replace(
        _metadata(),
        tables=_metadata().tables + (TableMetadata("public", "bad_view", True, "VIEW"),),
    )

    with pytest.raises(SnapshotError) as raised:
        persist_snapshot(tmp_path, source, broken)
    assert raised.value.code == "INVALID_METADATA"
    with sqlite3.connect(tmp_path / ".graphit" / "graphit.db") as connection:
        assert connection.execute("SELECT MAX(version) FROM snapshots").fetchone() == (
            first.version,
        )


def test_invalid_graph_rolls_back_snapshot_and_preserves_latest(tmp_path: Path) -> None:
    source = _project(tmp_path)
    first = persist_snapshot(tmp_path, source, _metadata())
    broken = replace(
        _metadata(),
        keys=(
            KeyConstraintMetadata(
                "public", "Order", "missing_column_key", "UNIQUE", ("absent",), False
            ),
        ),
    )

    with pytest.raises(SnapshotError) as raised:
        persist_snapshot(tmp_path, source, broken)
    assert raised.value.code == "INVALID_METADATA"

    with sqlite3.connect(tmp_path / ".graphit" / "graphit.db") as connection:
        assert connection.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM scan_runs").fetchone()[0] == 1
        assert (
            connection.execute("SELECT COUNT(*) FROM objects").fetchone()[0] == first.object_count
        )
        assert connection.execute("SELECT COUNT(*) FROM edges").fetchone()[0] == first.edge_count


def test_sqlite_failure_mid_graph_rolls_back_every_new_row(tmp_path: Path) -> None:
    source = _project(tmp_path)
    first = persist_snapshot(tmp_path, source, _metadata())
    path = tmp_path / ".graphit" / "graphit.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """CREATE TRIGGER reject_references BEFORE INSERT ON edges
            WHEN NEW.edge_type = 'REFERENCES'
            BEGIN SELECT RAISE(ABORT, 'test failure'); END"""
        )

    with pytest.raises(SnapshotError) as raised:
        persist_snapshot(tmp_path, source, replace(_metadata(), indexes=(_index(),)))
    assert raised.value.code == "STORE_WRITE_FAILED"
    assert "test failure" not in str(raised.value)

    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM scan_runs").fetchone()[0] == 1
        assert (
            connection.execute("SELECT COUNT(*) FROM objects").fetchone()[0] == first.object_count
        )
        assert connection.execute("SELECT COUNT(*) FROM edges").fetchone()[0] == first.edge_count
        assert connection.execute(
            "SELECT COUNT(*) FROM objects WHERE object_type = 'INDEX'"
        ).fetchone() == (0,)


def test_source_mismatch_fails_without_writing(tmp_path: Path) -> None:
    source = _project(tmp_path)
    with pytest.raises(SnapshotError, match="different source"):
        persist_snapshot(tmp_path, source, replace(_metadata(), source_name="other"))

    with sqlite3.connect(tmp_path / ".graphit" / "graphit.db") as connection:
        assert connection.execute("SELECT COUNT(*) FROM scan_runs").fetchone()[0] == 0


def test_failed_source_scan_records_failure_but_keeps_latest(tmp_path: Path) -> None:
    _project(tmp_path)
    first = persist_snapshot(tmp_path, _source(), replace(_metadata(), indexes=(_index(),)))

    class FailingScanner:
        def scan_metadata(self, source: SourceConfig, scope: ScanScope) -> MetadataSnapshot:
            assert source.name == "erp"
            assert scope.schemas == ("public",)
            raise MetadataScanError("SCHEMA_PERMISSION_DENIED", "Access denied to selected schema.")

    with pytest.raises(MetadataScanError, match="Access denied"):
        scan_source(tmp_path, "erp", scanner=FailingScanner())

    with sqlite3.connect(tmp_path / ".graphit" / "graphit.db") as connection:
        statuses = connection.execute("SELECT status FROM scan_runs ORDER BY id").fetchall()
        assert statuses == [("COMPLETED",), ("FAILED",)]
        assert connection.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 1
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM objects WHERE snapshot_id = ?", (first.snapshot_id,)
            ).fetchone()[0]
            == first.object_count
        )
        assert connection.execute(
            "SELECT COUNT(*) FROM objects WHERE object_type = 'INDEX'"
        ).fetchone() == (1,)


def test_scan_source_success_uses_adapter_and_persists(tmp_path: Path) -> None:
    _project(tmp_path)

    class StaticScanner:
        def scan_metadata(self, source: SourceConfig, scope: ScanScope) -> MetadataSnapshot:
            assert source.name == "erp"
            assert scope.schemas == source.schemas
            return replace(_metadata(), indexes=(_index(),))

    result = scan_source(tmp_path, "erp", scanner=StaticScanner())

    assert result.version == 1
    assert result.source_name == "erp"
    with closing(sqlite3.connect(tmp_path / ".graphit" / "graphit.db")) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM objects WHERE object_type = 'INDEX'"
        ).fetchone() == (1,)
