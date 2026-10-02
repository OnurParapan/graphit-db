"""Exact, bounded drift comparison over immutable local snapshots."""

import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest
from pytest import MonkeyPatch
from typer.testing import CliRunner

from graphit.cli import app
from graphit.queries import QueryError
from graphit.scanners.protocol import (
    ColumnMetadata,
    ForeignKeyMetadata,
    KeyConstraintMetadata,
    MetadataSnapshot,
    SchemaMetadata,
    TableMetadata,
)
from graphit.snapshot_diff import MAX_DIFF_OFFSET, compare_snapshots
from graphit.snapshots import persist_snapshot
from graphit.sources import add_source
from tests.test_snapshots import _metadata, _project

runner = CliRunner()


def _changed_metadata() -> MetadataSnapshot:
    base = _metadata()
    columns = tuple(
        replace(column, data_type="bigint")
        if column.table_name == "Order" and column.name == "ID"
        else replace(column, nullable=True)
        if column.table_name == "Order" and column.name == "customer_id"
        else column
        for column in base.columns
    )
    return replace(
        base,
        schemas=(*base.schemas, SchemaMetadata("audit")),
        tables=(
            *base.tables,
            TableMetadata("audit", "Event", False),
            TableMetadata("public", "customer_view", False, "VIEW"),
        ),
        columns=(
            *columns,
            ColumnMetadata("public", "Order", "note", 3, "text", True),
            ColumnMetadata("audit", "Event", "id", 1, "integer", False),
            ColumnMetadata("public", "customer_view", "ID", 1, "integer", False),
        ),
        keys=tuple(key for key in base.keys if key.name != "order_customer_unique"),
        foreign_keys=base.foreign_keys[:1],
    )


def test_exact_diff_additions_column_changes_and_external_stub_exclusion(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    source = _project(tmp_path)
    persist_snapshot(tmp_path, source, _metadata())
    persist_snapshot(tmp_path, source, _changed_metadata())

    def no_source_connection(*args: object, **kwargs: object) -> None:
        raise AssertionError("Diff must read only saved local snapshots")

    monkeypatch.setattr("psycopg.connect", no_source_connection)
    result = compare_snapshots(tmp_path, "erp", 1, 2)
    assert result.change_count == 10
    assert result.truncated is False
    assert result.scope == "SCHEMA_RELATION_COLUMN_KEY_FK"
    assert [(item.change, item.kind, item.qualified_name) for item in result.changes] == [
        ("ADDED", "SCHEMA", '"audit"'),
        ("ADDED", "TABLE", '"audit"."Event"'),
        ("ADDED", "COLUMN", '"audit"."Event"."id"'),
        ("CHANGED", "COLUMN", '"public"."Order"."ID"'),
        ("CHANGED", "COLUMN", '"public"."Order"."customer_id"'),
        ("ADDED", "COLUMN", '"public"."Order"."note"'),
        ("REMOVED", "KEY", '"public"."Order"."order_customer_unique"'),
        ("REMOVED", "FOREIGN_KEY", '"public"."Order"."order_external_fk"'),
        ("ADDED", "VIEW", '"public"."customer_view"'),
        ("ADDED", "COLUMN", '"public"."customer_view"."ID"'),
    ]
    changed = next(item for item in result.changes if item.qualified_name.endswith('"customer_id"'))
    assert changed.before == {
        "ordinal_position": 2,
        "data_type": "integer",
        "nullable": False,
        "primary_key": False,
        "unique_value": True,
    }
    assert changed.after == {**changed.before, "nullable": True, "unique_value": False}
    assert all(not item.qualified_name.startswith('"external"') for item in result.changes)


def test_diff_removals_versions_source_isolation_and_pagination(tmp_path: Path) -> None:
    source = _project(tmp_path)
    persist_snapshot(tmp_path, source, _metadata())
    changed = _changed_metadata()
    persist_snapshot(tmp_path, source, changed)
    empty = replace(changed, tables=(), columns=(), keys=(), foreign_keys=())
    persist_snapshot(tmp_path, source, empty)
    other = replace(source, name="other")
    add_source(tmp_path, other)
    persist_snapshot(tmp_path, other, replace(_metadata(), source_name="other"))

    page = compare_snapshots(tmp_path, "erp", 1, 2, limit=2)
    assert page.change_count == 10 and page.truncated is True
    assert len(page.changes) == 2
    removed = compare_snapshots(tmp_path, "erp", 2, 3)
    assert any(
        item.change == "REMOVED" and item.qualified_name == '"public"."Order"'
        for item in removed.changes
    )
    assert removed.from_version == 2 and removed.to_version == 3
    assert compare_snapshots(tmp_path, "erp", 1, 2).change_count == 10
    with pytest.raises(QueryError) as missing:
        compare_snapshots(tmp_path, "other", 1, 2)
    assert missing.value.code == "SNAPSHOT_NOT_FOUND"
    for versions, limit, code in (
        ((0, 2), 100, "INVALID_VERSION"),
        ((2, 2), 100, "INVALID_VERSION"),
        ((3, 2), 100, "INVALID_VERSION"),
        ((1, 2), 0, "INVALID_LIMIT"),
        ((1, 2), 501, "INVALID_LIMIT"),
        ((1, 4), 100, "SNAPSHOT_NOT_FOUND"),
    ):
        with pytest.raises(QueryError) as error:
            compare_snapshots(tmp_path, "erp", *versions, limit=limit)
        assert error.value.code == code


def test_diff_offset_pages_are_stable_and_reach_beyond_first_500(tmp_path: Path) -> None:
    source = _project(tmp_path)
    baseline = _metadata()
    persist_snapshot(tmp_path, source, baseline)
    extra_columns = tuple(
        ColumnMetadata("public", "Customer", f"extra_{index:04d}", index + 2, "integer", True)
        for index in range(520)
    )
    persist_snapshot(tmp_path, source, replace(baseline, columns=baseline.columns + extra_columns))

    first = compare_snapshots(tmp_path, "erp", 1, 2, limit=500)
    second = compare_snapshots(tmp_path, "erp", 1, 2, limit=50, offset=500)
    exhausted = compare_snapshots(tmp_path, "erp", 1, 2, limit=50, offset=520)
    assert first.offset == 0 and second.offset == 500 and exhausted.offset == 520
    assert first.change_count == second.change_count == exhausted.change_count == 520
    assert len(first.changes) == 500 and first.truncated
    assert len(second.changes) == 20 and not second.truncated
    assert exhausted.changes == () and not exhausted.truncated
    names = [item.qualified_name for item in (*first.changes, *second.changes)]
    assert len(names) == len(set(names)) == 520
    assert names == sorted(names)
    for offset in (-1, MAX_DIFF_OFFSET + 1):
        with pytest.raises(QueryError) as invalid:
            compare_snapshots(tmp_path, "erp", 1, 2, offset=offset)
        assert invalid.value.code == "INVALID_OFFSET"


def test_diff_budget_and_corrupt_saved_column_fail_closed(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    source = _project(tmp_path)
    persist_snapshot(tmp_path, source, _metadata())
    persist_snapshot(tmp_path, source, _metadata())
    monkeypatch.setattr("graphit.snapshot_diff.MAX_DIFF_OBJECTS", 1)
    with pytest.raises(QueryError) as capped:
        compare_snapshots(tmp_path, "erp", 1, 2)
    assert capped.value.code == "DIFF_BUDGET_EXCEEDED"
    monkeypatch.setattr("graphit.snapshot_diff.MAX_DIFF_OBJECTS", 100_000)
    monkeypatch.setattr("graphit.snapshot_diff.MAX_DIFF_FK_EDGES", 1)
    with pytest.raises(QueryError) as fk_capped:
        compare_snapshots(tmp_path, "erp", 1, 2)
    assert fk_capped.value.code == "DIFF_BUDGET_EXCEEDED"
    monkeypatch.setattr("graphit.snapshot_diff.MAX_DIFF_FK_EDGES", 100_000)
    path = tmp_path / ".graphit" / "graphit.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """UPDATE columns SET data_type = '' WHERE object_id = (
                SELECT objects.id FROM objects JOIN snapshots ON snapshots.id = objects.snapshot_id
                WHERE snapshots.version = 2 AND objects.object_type = 'COLUMN' LIMIT 1
            )"""
        )
    with pytest.raises(QueryError) as corrupt:
        compare_snapshots(tmp_path, "erp", 1, 2)
    assert corrupt.value.code == "STORE_READ_FAILED"


def test_identical_rescans_have_no_structural_diff(tmp_path: Path) -> None:
    source = _project(tmp_path)
    first = persist_snapshot(tmp_path, source, _metadata())
    second = persist_snapshot(tmp_path, source, _metadata())

    assert first.fingerprint == second.fingerprint
    result = compare_snapshots(tmp_path, "erp", 1, 2)
    assert result.change_count == 0
    assert result.changes == ()
    assert result.truncated is False


def test_relation_kind_change_is_addition_and_removal_not_rename(tmp_path: Path) -> None:
    source = _project(tmp_path)
    table = MetadataSnapshot(
        source_name="erp",
        schemas=(SchemaMetadata("public"),),
        tables=(TableMetadata("public", "report", False),),
        columns=(ColumnMetadata("public", "report", "id", 1, "integer", False),),
    )
    persist_snapshot(tmp_path, source, table)
    persist_snapshot(
        tmp_path, source, replace(table, tables=(replace(table.tables[0], kind="VIEW"),))
    )

    result = compare_snapshots(tmp_path, "erp", 1, 2)
    assert [(item.change, item.kind) for item in result.changes] == [
        ("REMOVED", "TABLE"),
        ("ADDED", "VIEW"),
    ]
    assert all(item.qualified_name == '"public"."report"' for item in result.changes)


def test_diff_tracks_ordered_keys_and_fk_target_scope_and_flags(tmp_path: Path) -> None:
    source = _project(tmp_path)
    base = _metadata()
    first = replace(
        base,
        columns=(
            *base.columns,
            ColumnMetadata("public", "Customer", "region_id", 2, "integer", False),
            ColumnMetadata("public", "Order", "region_id", 3, "integer", False),
        ),
        keys=(
            *base.keys,
            KeyConstraintMetadata(
                "public",
                "Order",
                "order_business_key",
                "UNIQUE",
                ("customer_id", "region_id"),
                False,
            ),
        ),
        foreign_keys=(
            ForeignKeyMetadata(
                "public",
                "Order",
                "order_customer_fk",
                ("customer_id", "region_id"),
                "public",
                "Customer",
                ("ID", "region_id"),
                True,
                True,
                False,
            ),
        ),
    )
    persist_snapshot(tmp_path, source, first)
    second = replace(
        first,
        keys=tuple(
            replace(key, columns=("region_id", "customer_id"), inherited=True)
            if key.name == "order_business_key"
            else key
            for key in first.keys
        ),
        foreign_keys=(
            replace(
                first.foreign_keys[0],
                source_columns=("region_id", "customer_id"),
                target_columns=("region_id", "ID"),
            ),
        ),
    )
    persist_snapshot(tmp_path, source, second)
    result = compare_snapshots(tmp_path, "erp", 1, 2)
    assert [(change.change, change.kind) for change in result.changes] == [
        ("CHANGED", "KEY"),
        ("CHANGED", "FOREIGN_KEY"),
    ]
    key_change, fk_change = result.changes
    assert key_change.before == {
        "kind": "UNIQUE",
        "columns": ["customer_id", "region_id"],
        "inherited": False,
    }
    assert key_change.after == {
        "kind": "UNIQUE",
        "columns": ["region_id", "customer_id"],
        "inherited": True,
    }
    assert fk_change.before is not None and fk_change.after is not None
    assert fk_change.before["column_pairs"] == [
        ["customer_id", "ID"],
        ["region_id", "region_id"],
    ]
    assert fk_change.after["column_pairs"] == [
        ["region_id", "region_id"],
        ["customer_id", "ID"],
    ]

    third = replace(
        second,
        foreign_keys=(
            replace(
                second.foreign_keys[0],
                target_schema="legacy",
                target_in_scope=False,
                validated=False,
                inherited=True,
            ),
        ),
    )
    persist_snapshot(tmp_path, source, third)
    target_change = compare_snapshots(tmp_path, "erp", 2, 3)
    assert target_change.change_count == 1
    assert target_change.changes[0].kind == "FOREIGN_KEY"
    assert target_change.changes[0].after == {
        "source_table": '"public"."Order"',
        "target_table": '"legacy"."Customer"',
        "column_pairs": [["region_id", "region_id"], ["customer_id", "ID"]],
        "target_in_scope": False,
        "validated": False,
        "inherited": True,
    }


def test_diff_reports_new_declared_keys_and_fks_without_stub_additions(tmp_path: Path) -> None:
    source = _project(tmp_path)
    base = _metadata()
    persist_snapshot(tmp_path, source, replace(base, keys=(), foreign_keys=()))
    persist_snapshot(tmp_path, source, base)

    result = compare_snapshots(tmp_path, "erp", 1, 2)
    declared = [change for change in result.changes if change.kind in ("KEY", "FOREIGN_KEY")]
    assert [(change.change, change.kind) for change in declared] == [
        ("ADDED", "KEY"),
        ("ADDED", "FOREIGN_KEY"),
        ("ADDED", "KEY"),
        ("ADDED", "FOREIGN_KEY"),
        ("ADDED", "KEY"),
    ]
    assert all(not change.qualified_name.startswith('"external"') for change in result.changes)


def test_diff_accepts_legacy_unique_edge_name(tmp_path: Path) -> None:
    source = _project(tmp_path)
    persist_snapshot(tmp_path, source, _metadata())
    persist_snapshot(tmp_path, source, _metadata())
    path = tmp_path / ".graphit" / "graphit.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """UPDATE edges SET edge_type = 'UNIQUE' WHERE snapshot_id = (
                SELECT id FROM snapshots WHERE version = 1
            ) AND edge_type = 'UNIQUE_KEY'"""
        )

    result = compare_snapshots(tmp_path, "erp", 1, 2)
    assert result.change_count == 0


@pytest.mark.parametrize("corruption", ["missing", "mismatch"])
def test_diff_rejects_missing_or_inconsistent_fk_edge(tmp_path: Path, corruption: str) -> None:
    source = _project(tmp_path)
    persist_snapshot(tmp_path, source, _metadata())
    persist_snapshot(tmp_path, source, _metadata())
    path = tmp_path / ".graphit" / "graphit.db"
    with sqlite3.connect(path) as connection:
        edge_id = connection.execute(
            """SELECT e.id FROM edges AS e JOIN snapshots AS s ON s.id = e.snapshot_id
            WHERE s.version = 2 AND e.edge_type = 'REFERENCES'
              AND json_extract(e.metadata_json, '$.level') = 'TABLE'
              AND json_extract(e.metadata_json, '$.constraint') = 'order_customer_fk'"""
        ).fetchone()[0]
        if corruption == "missing":
            connection.execute("DELETE FROM edges WHERE id = ?", (edge_id,))
        else:
            connection.execute(
                "UPDATE edges SET metadata_json = json_set(metadata_json, '$.validated', 0) "
                "WHERE id = ?",
                (edge_id,),
            )
    with pytest.raises(QueryError) as error:
        compare_snapshots(tmp_path, "erp", 1, 2)
    assert error.value.code == "STORE_READ_FAILED"


def test_diff_cli_human_json_and_errors(tmp_path: Path) -> None:
    source = _project(tmp_path)
    persist_snapshot(tmp_path, source, _metadata())
    persist_snapshot(tmp_path, source, _changed_metadata())
    args = [
        "diff",
        "--source",
        "erp",
        "--from-version",
        "1",
        "--to-version",
        "2",
        "--limit",
        "2",
        "--project",
        str(tmp_path),
    ]
    human = runner.invoke(app, args)
    machine = runner.invoke(app, [*args, "--json"])
    assert human.exit_code == 0
    assert "10 changes" in human.stdout
    assert "declared key/FK" in human.stdout
    assert "More changes exist" in human.stdout
    assert machine.exit_code == 0
    payload = json.loads(machine.stdout)
    assert payload["source_name"] == "erp"
    assert payload["scope"] == "SCHEMA_RELATION_COLUMN_KEY_FK"
    assert payload["from_version"] == 1 and payload["to_version"] == 2
    assert payload["change_count"] == 10 and payload["truncated"] is True
    assert payload["offset"] == 0
    assert len(payload["changes"]) == 2
    later = runner.invoke(app, [*args, "--offset", "2", "--json"])
    assert later.exit_code == 0
    later_payload = json.loads(later.stdout)
    assert later_payload["offset"] == 2
    assert later_payload["change_count"] == 10
    assert (
        later_payload["changes"][0]["qualified_name"]
        == compare_snapshots(tmp_path, "erp", 1, 2, limit=2, offset=2).changes[0].qualified_name
    )
    missing_args = args.copy()
    missing_args[missing_args.index("--to-version") + 1] = "4"
    missing = runner.invoke(app, missing_args)
    assert missing.exit_code == 6 and "SNAPSHOT_NOT_FOUND" in missing.stderr
    invalid = runner.invoke(app, [*args, "--limit", "0"])
    assert invalid.exit_code == 2 and "INVALID_LIMIT" in invalid.stderr
    invalid_offset = runner.invoke(app, [*args, "--offset", "-1"])
    assert invalid_offset.exit_code == 2 and "INVALID_OFFSET" in invalid_offset.stderr
