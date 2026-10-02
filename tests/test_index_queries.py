"""Explicit, bounded lookup of saved index facts without source reconnection."""

import json
import sqlite3
from contextlib import closing
from dataclasses import asdict, replace
from pathlib import Path

import pytest
from pytest import MonkeyPatch
from typer.testing import CliRunner

from graphit.cli import app
from graphit.project import initialize_project
from graphit.queries import QueryError, list_table_indexes, search_objects
from graphit.scanners.protocol import ColumnMetadata, SchemaMetadata, TableMetadata
from graphit.snapshots import persist_snapshot
from graphit.sources import SourceConfig, add_source
from tests.test_snapshots import _index, _metadata, _source

runner = CliRunner()


def _project(root: Path) -> SourceConfig:
    initialize_project(root)
    source = _source()
    add_source(root, source)
    return source


def test_index_lookup_pages_saved_facts_and_never_connects(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    source = _project(tmp_path)
    first = persist_snapshot(tmp_path, source, _metadata())
    indexes = (
        replace(_index(), name="z_lookup", valid=False, ready=False),
        replace(_index(), name="a_lookup"),
    )
    second = persist_snapshot(tmp_path, source, replace(_metadata(), indexes=indexes))

    def no_source_connection(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Index lookup must stay local")

    monkeypatch.setattr("psycopg.connect", no_source_connection)
    first_page = list_table_indexes(tmp_path, "erp", '"public"."Order"', limit=1)
    second_page = list_table_indexes(tmp_path, "erp", '"Order"', limit=1, offset=1)
    last_page = list_table_indexes(tmp_path, "erp", '"Order"', limit=1, offset=2)

    assert first.version == 1 and second.version == 2
    assert first_page.snapshot_version == second.version
    assert first_page.relation_kind == "TABLE"
    assert (first_page.index_count, first_page.offset, first_page.truncated) == (2, 0, True)
    assert [item.name for item in first_page.indexes] == ["a_lookup"]
    assert [item.name for item in second_page.indexes] == ["z_lookup"]
    assert second_page.truncated is False
    assert second_page.indexes[0].valid is False
    assert second_page.indexes[0].ready is False
    assert last_page.indexes == () and last_page.index_count == 2
    assert [(part.position, part.column_name) for part in first_page.indexes[0].key_columns] == [
        (1, "customer_id"),
        (2, None),
    ]
    assert [
        (part.position, part.column_name) for part in first_page.indexes[0].included_columns
    ] == [(1, "ID")]
    assert search_objects(tmp_path, "erp", "a_lookup").matches == ()


def test_old_snapshot_and_rescan_without_indexes_return_empty(tmp_path: Path) -> None:
    source = _project(tmp_path)
    persist_snapshot(tmp_path, source, _metadata())
    empty = list_table_indexes(tmp_path, "erp", '"public"."Order"')
    assert empty.index_count == 0 and empty.indexes == ()
    persist_snapshot(tmp_path, source, replace(_metadata(), indexes=(_index(),)))
    persist_snapshot(tmp_path, source, _metadata())
    current = list_table_indexes(tmp_path, "erp", '"public"."Order"')
    assert current.snapshot_version == 3 and current.index_count == 0
    with closing(sqlite3.connect(tmp_path / ".graphit" / "graphit.db")) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM objects WHERE object_type = 'INDEX'"
        ).fetchone() == (1,)


def test_index_lookup_relation_kind_ambiguity_and_source_isolation(tmp_path: Path) -> None:
    source = _project(tmp_path)
    metadata = replace(
        _metadata(),
        schemas=(SchemaMetadata("public"), SchemaMetadata("other")),
        tables=_metadata().tables
        + (
            TableMetadata("other", "Order", False),
            TableMetadata("public", "Cache", False, "MATERIALIZED_VIEW"),
            TableMetadata("public", "Plain", False, "VIEW"),
        ),
        columns=_metadata().columns
        + (
            ColumnMetadata("other", "Order", "ID", 1, "integer", False),
            ColumnMetadata("public", "Cache", "ID", 1, "integer", False),
            ColumnMetadata("public", "Plain", "ID", 1, "integer", False),
        ),
        indexes=(
            _index(),
            replace(_index(), table_name="Cache", key_columns=("ID",), included_columns=()),
        ),
    )
    persist_snapshot(tmp_path, source, metadata)
    materialized = list_table_indexes(tmp_path, "erp", '"public"."Cache"')
    assert materialized.relation_kind == "MATERIALIZED_VIEW"
    assert materialized.index_count == 1
    for reference, code in (
        ('"Order"', "AMBIGUOUS_INDEX_TARGET"),
        ('"public"."Plain"', "INDEX_TARGET_NOT_FOUND"),
        ('"public"."Absent"', "INDEX_TARGET_NOT_FOUND"),
        ("public..bad", "INVALID_INDEX_TARGET"),
    ):
        with pytest.raises(QueryError) as raised:
            list_table_indexes(tmp_path, "erp", reference)
        assert raised.value.code == code

    other = replace(source, name="other_source")
    add_source(tmp_path, other)
    persist_snapshot(tmp_path, other, replace(_metadata(), source_name="other_source"))
    assert list_table_indexes(tmp_path, "other_source", '"public"."Order"').indexes == ()


def test_index_lookup_rejects_invalid_bounds_and_corrupt_edges(tmp_path: Path) -> None:
    source = _project(tmp_path)
    persist_snapshot(tmp_path, source, replace(_metadata(), indexes=(_index(),)))
    for limit, offset, code in (
        (0, 0, "INVALID_LIMIT"),
        (101, 0, "INVALID_LIMIT"),
        (1, -1, "INVALID_OFFSET"),
        (1, 100001, "INVALID_OFFSET"),
    ):
        with pytest.raises(QueryError) as raised:
            list_table_indexes(tmp_path, "erp", '"public"."Order"', limit, offset)
        assert raised.value.code == code

    path = tmp_path / ".graphit" / "graphit.db"
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(
            """UPDATE edges SET metadata_json = '{"position": 9}'
            WHERE edge_type = 'INDEX_KEY'"""
        )
    with pytest.raises(QueryError) as raised:
        list_table_indexes(tmp_path, "erp", '"public"."Order"')
    assert raised.value.code == "STORE_READ_FAILED"


def test_indexes_cli_json_human_and_errors(tmp_path: Path) -> None:
    source = _project(tmp_path)
    persist_snapshot(
        tmp_path,
        source,
        replace(
            _metadata(),
            indexes=(_index(), replace(_index(), name="other_lookup")),
        ),
    )
    args = ["indexes", '"public"."Order"', "--source", "erp", "--project", str(tmp_path)]
    json_result = runner.invoke(app, [*args, "--limit", "1", "--json"])
    human = runner.invoke(app, [*args, "--limit", "1"])
    invalid = runner.invoke(app, [*args, "--offset", "-1"])
    missing = runner.invoke(
        app, ["indexes", "missing", "--source", "erp", "--project", str(tmp_path)]
    )

    assert json_result.exit_code == 0 and human.exit_code == 0
    expected = list_table_indexes(tmp_path, "erp", '"public"."Order"', limit=1)
    assert json.loads(json_result.stdout) == json.loads(json.dumps(asdict(expected)))
    assert "1:customer_id, 2:<expression>" in human.stdout
    assert "INCLUDE (1:ID)" in human.stdout
    assert "More indexes exist; use --offset 1" in human.stdout
    assert invalid.exit_code == 2 and "INVALID_OFFSET" in invalid.stderr
    assert missing.exit_code == 6 and "INDEX_TARGET_NOT_FOUND" in missing.stderr
