"""PostgreSQL catalog scanner scope, safety, and identifier tests."""

from typing import Any

import psycopg
import pytest
from pytest import MonkeyPatch

from graphit.scanners.postgresql import MetadataScanError, PostgreSQLScanner
from graphit.scanners.protocol import DatabaseScanner, ScanScope
from graphit.sources import SourceConfig


def _source() -> SourceConfig:
    return SourceConfig(
        name="erp",
        host="db.example.test",
        port=5432,
        database_name="erp",
        username="reader",
        credential_env="ERP_DB_PASSWORD",
        schemas=("sales", 'Other"Schema'),
    )


class ScriptedCursor:
    def __init__(self, results: dict[str, list[tuple[Any, ...]]]) -> None:
        self.results = results
        self.current: list[tuple[Any, ...]] = []
        self.queries: list[tuple[str, tuple[Any, ...] | None]] = []

    def __enter__(self) -> "ScriptedCursor":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, query: str, params: tuple[Any, ...] | None = None) -> None:
        self.queries.append((query, params))
        if "transaction_read_only" in query:
            self.current = self.results.get("read_only", [("on",)])
        elif "pg_catalog.pg_index" in query:
            self.current = self.results["indexes"]
        elif "pg_catalog.pg_constraint" in query:
            self.current = self.results["constraints"]
        elif "pg_catalog.pg_attribute" in query:
            self.current = self.results["columns"]
        elif "pg_catalog.pg_class" in query:
            self.current = self.results["tables"]
        else:
            self.current = self.results["schemas"]
        if params is not None:
            self.current = [row for row in self.current if row[0] in params[0]]

    def fetchone(self) -> tuple[Any, ...] | None:
        return self.current[0] if self.current else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.current


class ScriptedConnection:
    def __init__(self, results: dict[str, list[tuple[Any, ...]]]) -> None:
        self.read_only = False
        self.isolation_level: psycopg.IsolationLevel | None = None
        self.closed = False
        self.script = ScriptedCursor(results)

    def __enter__(self) -> "ScriptedConnection":
        return self

    def __exit__(self, *_args: object) -> None:
        self.closed = True

    def cursor(self) -> ScriptedCursor:
        assert self.read_only is True
        return self.script


def _results() -> dict[str, list[tuple[Any, ...]]]:
    return {
        "schemas": [("sales", True), ('Other"Schema', True)],
        "tables": [("sales", "Order", "r"), ('Other"Schema', "Line.Item", "p")],
        "columns": [
            ("sales", "Order", "Customer ID", 1, "integer", False),
            ('Other"Schema', "Line.Item", 'a"b', 2, "text", True),
        ],
        "constraints": [],
        "indexes": [],
    }


def test_scans_multiple_schemas_and_preserves_quoted_names(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("ERP_DB_PASSWORD", "secret-do-not-print")
    fake = ScriptedConnection(_results())
    connect_args: dict[str, Any] = {}

    def connect(**kwargs: Any) -> ScriptedConnection:
        connect_args.update(kwargs)
        return fake

    monkeypatch.setattr("graphit.scanners.postgresql.psycopg.connect", connect)
    scanner: DatabaseScanner = PostgreSQLScanner()

    result = scanner.scan_metadata(
        _source(), ScanScope(schemas=("sales", 'Other"Schema'), max_tables=5, max_columns=5)
    )

    assert result.source_name == "erp"
    assert [schema.name for schema in result.schemas] == ["sales", 'Other"Schema']
    assert result.tables[0].qualified_name == '"sales"."Order"'
    assert result.tables[1].qualified_name == '"Other""Schema"."Line.Item"'
    assert result.tables[1].partitioned is True
    assert result.columns[1].qualified_name == '"Other""Schema"."Line.Item"."a""b"'
    assert result.columns[1].nullable is True
    assert fake.closed is True
    assert fake.isolation_level == psycopg.IsolationLevel.REPEATABLE_READ
    assert "default_transaction_read_only=on" in connect_args["options"]
    assert connect_args["password"] == "secret-do-not-print"

    for query, params in fake.script.queries:
        assert query.lstrip().startswith("SELECT")
        assert 'Other"Schema' not in query
        if params is not None:
            assert params[0] == ["sales", 'Other"Schema']
    assert fake.script.queries[2][1] == (["sales", 'Other"Schema'], 6)
    assert fake.script.queries[3][1] == (["sales", 'Other"Schema'], 6)
    assert fake.script.queries[4][1] == (["sales", 'Other"Schema'], 100001)
    assert fake.script.queries[5][1] == (["sales", 'Other"Schema'], 100001)


def test_views_and_materialized_views_keep_kind_columns_and_shared_limit(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("ERP_DB_PASSWORD", "secret-do-not-print")
    rows = _results()
    rows["tables"] = [
        ("sales", "Order", "r"),
        ("sales", "Sales View", "v"),
        ('Other"Schema', "Cache", "m"),
    ]
    rows["columns"] = [
        ("sales", "Order", "id", 1, "integer", False),
        ("sales", "Sales View", "customer_id", 1, "integer", True),
        ('Other"Schema', "Cache", "total", 1, "numeric", True),
    ]
    fake = ScriptedConnection(rows)
    monkeypatch.setattr("graphit.scanners.postgresql.psycopg.connect", lambda **_: fake)

    result = PostgreSQLScanner().scan_metadata(
        _source(), ScanScope(("sales", 'Other"Schema'), max_tables=3, max_columns=3)
    )

    assert [(item.kind, item.partitioned) for item in result.tables] == [
        ("TABLE", False),
        ("VIEW", False),
        ("MATERIALIZED_VIEW", False),
    ]
    assert result.tables[2].qualified_name == '"Other""Schema"."Cache"'
    assert [(item.table_name, item.name) for item in result.columns] == [
        ("Order", "id"),
        ("Sales View", "customer_id"),
        ("Cache", "total"),
    ]
    assert all(query.lstrip().startswith("SELECT") for query, _ in fake.script.queries)
    assert "('r', 'p', 'v', 'm')" in fake.script.queries[2][0]
    assert "('r', 'p', 'v', 'm')" in fake.script.queries[3][0]
    assert "('r', 'p')" in fake.script.queries[4][0]

    with pytest.raises(MetadataScanError) as raised:
        PostgreSQLScanner().scan_metadata(
            _source(), ScanScope(("sales", 'Other"Schema'), max_tables=2)
        )
    assert raised.value.code == "TABLE_LIMIT_EXCEEDED"

    with pytest.raises(MetadataScanError) as raised:
        PostgreSQLScanner().scan_metadata(
            _source(), ScanScope(("sales", 'Other"Schema'), max_tables=3, max_columns=2)
        )
    assert raised.value.code == "COLUMN_LIMIT_EXCEEDED"


def test_unsupported_relation_kind_fails_closed(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("ERP_DB_PASSWORD", "secret-do-not-print")
    rows = _results()
    rows["tables"] = [("sales", "Unexpected", "x")]
    fake = ScriptedConnection(rows)
    monkeypatch.setattr("graphit.scanners.postgresql.psycopg.connect", lambda **_: fake)

    with pytest.raises(MetadataScanError) as raised:
        PostgreSQLScanner().scan_metadata(_source(), ScanScope(("sales",)))
    assert raised.value.code == "INVALID_METADATA"


@pytest.mark.parametrize(
    ("changes", "expected_code"),
    [
        ({"schemas": ()}, "INVALID_SCOPE"),
        ({"schemas": ("missing",)}, "INVALID_SCOPE"),
        ({"max_tables": 0}, "INVALID_SCOPE"),
        ({"max_columns": 100001}, "INVALID_SCOPE"),
        ({"max_constraints": 0}, "INVALID_SCOPE"),
        ({"max_indexes": 0}, "INVALID_SCOPE"),
        ({"max_indexes": 100001}, "INVALID_SCOPE"),
    ],
)
def test_invalid_scope_fails_before_connection(
    monkeypatch: MonkeyPatch, changes: dict[str, Any], expected_code: str
) -> None:
    def must_not_connect(**_kwargs: Any) -> None:
        pytest.fail("invalid scope must not connect")

    monkeypatch.setattr("graphit.scanners.postgresql.psycopg.connect", must_not_connect)
    values: dict[str, Any] = {"schemas": ("sales",)}
    values.update(changes)

    with pytest.raises(MetadataScanError) as raised:
        PostgreSQLScanner().scan_metadata(_source(), ScanScope(**values))
    assert raised.value.code == expected_code


@pytest.mark.parametrize(
    ("results", "expected_code"),
    [
        ({"schemas": [("sales", True)]}, "SCHEMA_NOT_FOUND"),
        ({"schemas": [("sales", True), ('Other"Schema', False)]}, "SCHEMA_PERMISSION_DENIED"),
        ({"tables": [("sales", "one", "r")] * 3}, "TABLE_LIMIT_EXCEEDED"),
        ({"columns": [("sales", "one", "a", 1, "int", True)] * 3}, "COLUMN_LIMIT_EXCEEDED"),
        (
            {
                "constraints": [
                    (
                        "sales",
                        "Order",
                        "limit_key",
                        "p",
                        True,
                        [1],
                        ["Customer ID"],
                        None,
                        None,
                        None,
                        [],
                        False,
                    )
                ]
                * 3
            },
            "CONSTRAINT_LIMIT_EXCEEDED",
        ),
    ],
)
def test_missing_permission_and_limits_fail_closed(
    monkeypatch: MonkeyPatch, results: dict[str, list[tuple[Any, ...]]], expected_code: str
) -> None:
    monkeypatch.setenv("ERP_DB_PASSWORD", "secret-do-not-print")
    rows = _results()
    rows.update(results)
    fake = ScriptedConnection(rows)
    monkeypatch.setattr("graphit.scanners.postgresql.psycopg.connect", lambda **_: fake)

    with pytest.raises(MetadataScanError) as raised:
        PostgreSQLScanner().scan_metadata(
            _source(),
            ScanScope(("sales", 'Other"Schema'), max_tables=2, max_columns=2, max_constraints=2),
        )

    assert raised.value.code == expected_code
    assert fake.closed is True


def test_driver_permission_failure_is_sanitized(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("ERP_DB_PASSWORD", "secret-do-not-print")

    def fail(**_kwargs: Any) -> None:
        raise psycopg.errors.InsufficientPrivilege("permission denied; secret-do-not-print")

    monkeypatch.setattr("graphit.scanners.postgresql.psycopg.connect", fail)

    with pytest.raises(MetadataScanError) as raised:
        PostgreSQLScanner().scan_metadata(_source(), ScanScope(("sales",)))
    assert raised.value.code == "PERMISSION_DENIED"
    assert "secret-do-not-print" not in str(raised.value)


def test_read_only_must_be_confirmed_before_catalog_reads(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("ERP_DB_PASSWORD", "secret-do-not-print")
    rows = _results()
    rows["read_only"] = [("off",)]
    fake = ScriptedConnection(rows)
    monkeypatch.setattr("graphit.scanners.postgresql.psycopg.connect", lambda **_: fake)

    with pytest.raises(MetadataScanError) as raised:
        PostgreSQLScanner().scan_metadata(_source(), ScanScope(("sales",)))

    assert raised.value.code == "READ_ONLY_NOT_ENFORCED"
    assert len(fake.script.queries) == 1
    assert fake.closed is True


def test_composite_keys_and_cross_schema_foreign_key_preserve_order(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("ERP_DB_PASSWORD", "secret-do-not-print")
    rows = _results()
    rows["constraints"] = [
        (
            "sales",
            "Order",
            "Order PK",
            "p",
            True,
            [1, 2],
            ["Customer ID", "Order No"],
            None,
            None,
            None,
            [],
            False,
        ),
        (
            "sales",
            "Order",
            "Order unique",
            "u",
            True,
            [2],
            ["Order No"],
            None,
            None,
            None,
            [],
            False,
        ),
        (
            "sales",
            "Order",
            "Order → Line",
            "f",
            True,
            [1, 2],
            ["Customer ID", "Order No"],
            'Other"Schema',
            "Line.Item",
            [1, 2],
            ['a"b', "Line No"],
            False,
        ),
    ]
    fake = ScriptedConnection(rows)
    monkeypatch.setattr("graphit.scanners.postgresql.psycopg.connect", lambda **_: fake)

    result = PostgreSQLScanner().scan_metadata(
        _source(), ScanScope(("sales", 'Other"Schema'), max_constraints=3)
    )

    assert [(key.kind, key.columns) for key in result.keys] == [
        ("PRIMARY_KEY", ("Customer ID", "Order No")),
        ("UNIQUE", ("Order No",)),
    ]
    relation = result.foreign_keys[0]
    assert relation.name == "Order → Line"
    assert relation.source_columns == ("Customer ID", "Order No")
    assert relation.target_columns == ('a"b', "Line No")
    assert list(zip(relation.source_columns, relation.target_columns, strict=True)) == [
        ("Customer ID", 'a"b'),
        ("Order No", "Line No"),
    ]
    assert relation.target_schema == 'Other"Schema'
    assert relation.target_in_scope is True
    assert relation.validated is True
    assert relation.inherited is False
    constraint_sql, constraint_params = fake.script.queries[4]
    assert constraint_sql.count("WITH ORDINALITY") == 2
    assert "ORDER BY cols.position" in constraint_sql
    assert "LIMIT %s" in constraint_sql
    assert 'Other"Schema' not in constraint_sql
    assert constraint_params == (["sales", 'Other"Schema'], 4)


def test_self_fk_and_out_of_scope_target_are_explicit(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("ERP_DB_PASSWORD", "secret-do-not-print")
    rows = _results()
    rows["constraints"] = [
        (
            "sales",
            "Order",
            "self_fk",
            "f",
            False,
            [2],
            ["Parent ID"],
            "sales",
            "Order",
            [1],
            ["Customer ID"],
            True,
        ),
        (
            "sales",
            "Order",
            "external_fk",
            "f",
            True,
            [1],
            ["Customer ID"],
            "external",
            "Customer",
            [1],
            ["ID"],
            False,
        ),
    ]
    fake = ScriptedConnection(rows)
    monkeypatch.setattr("graphit.scanners.postgresql.psycopg.connect", lambda **_: fake)

    result = PostgreSQLScanner().scan_metadata(_source(), ScanScope(("sales",), max_constraints=2))

    self_fk, external_fk = result.foreign_keys
    assert self_fk.source_table == self_fk.target_table == "Order"
    assert self_fk.validated is False
    assert self_fk.inherited is True
    assert self_fk.target_in_scope is True
    assert external_fk.target_schema == "external"
    assert external_fk.target_columns == ("ID",)
    assert external_fk.target_in_scope is False
    assert all(table.schema_name != "external" for table in result.tables)


@pytest.mark.parametrize(
    "constraint",
    [
        ("sales", "Order", "broken_pk", "p", True, [1, 2], ["a"], None, None, None, [], False),
        ("sales", "Order", "broken_fk", "f", True, [1], ["a"], "sales", "Order", [1], [], False),
        ("sales", "Order", "missing_target", "f", True, [1], ["a"], None, None, [1], ["a"], False),
    ],
)
def test_incomplete_constraints_fail_closed(
    monkeypatch: MonkeyPatch, constraint: tuple[Any, ...]
) -> None:
    monkeypatch.setenv("ERP_DB_PASSWORD", "secret-do-not-print")
    rows = _results()
    rows["constraints"] = [constraint]
    fake = ScriptedConnection(rows)
    monkeypatch.setattr("graphit.scanners.postgresql.psycopg.connect", lambda **_: fake)

    with pytest.raises(MetadataScanError) as raised:
        PostgreSQLScanner().scan_metadata(_source(), ScanScope(("sales",)))
    assert raised.value.code == "INCOMPLETE_CONSTRAINT"


def _index_row() -> tuple[Any, ...]:
    return (
        "sales",
        "Order",
        'Order"lookup',
        "btree",
        True,
        False,
        True,
        True,
        True,
        2,
        3,
        ["Customer ID", None, "Order No"],
        [1, 0, 2],
    )


def test_index_catalog_preserves_key_order_and_only_safe_flags(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("ERP_DB_PASSWORD", "secret-do-not-print")
    rows = _results()
    rows["indexes"] = [_index_row()]
    fake = ScriptedConnection(rows)
    monkeypatch.setattr("graphit.scanners.postgresql.psycopg.connect", lambda **_: fake)

    metadata = PostgreSQLScanner().scan_metadata(
        _source(), ScanScope(("sales", 'Other"Schema'), max_indexes=1)
    )

    index = metadata.indexes[0]
    assert (index.schema_name, index.table_name, index.name) == (
        "sales",
        "Order",
        'Order"lookup',
    )
    assert index.key_columns == ("Customer ID", None)
    assert index.included_columns == ("Order No",)
    assert (index.access_method, index.unique, index.primary) == ("btree", True, False)
    assert (index.valid, index.ready, index.partial) == (True, True, True)
    query, params = fake.script.queries[5]
    assert query.lstrip().startswith("SELECT")
    assert "pg_catalog.pg_index" in query and "WITH ORDINALITY" in query
    assert "idx.indpred IS NOT NULL" in query
    assert "pg_get_expr" not in query and "pg_get_indexdef" not in query
    assert 'Other"Schema' not in query
    assert params == (["sales", 'Other"Schema'], 2)


@pytest.mark.parametrize(
    "invalid_row",
    [
        (*_index_row()[:9], 2, 3, ["Customer ID", None, None], [1, 0, 2]),
        (*_index_row()[:9], 4, 3, ["Customer ID", None, "Order No"], [1, 0, 2]),
        ("sales", "Not Scanned", *_index_row()[2:]),
        (*_index_row()[:4], False, True, *_index_row()[6:]),
        (*_index_row()[:11], ["Customer ID", None, "Order No"], [1, 9, 2]),
    ],
)
def test_incomplete_index_metadata_fails_closed(
    monkeypatch: MonkeyPatch, invalid_row: tuple[Any, ...]
) -> None:
    monkeypatch.setenv("ERP_DB_PASSWORD", "secret-do-not-print")
    rows = _results()
    rows["indexes"] = [invalid_row]
    fake = ScriptedConnection(rows)
    monkeypatch.setattr("graphit.scanners.postgresql.psycopg.connect", lambda **_: fake)

    with pytest.raises(MetadataScanError) as raised:
        PostgreSQLScanner().scan_metadata(_source(), ScanScope(("sales", 'Other"Schema')))
    assert raised.value.code == "INCOMPLETE_INDEX"


def test_index_limit_fails_closed(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("ERP_DB_PASSWORD", "secret-do-not-print")
    rows = _results()
    rows["indexes"] = [_index_row(), _index_row()]
    fake = ScriptedConnection(rows)
    monkeypatch.setattr("graphit.scanners.postgresql.psycopg.connect", lambda **_: fake)

    with pytest.raises(MetadataScanError) as raised:
        PostgreSQLScanner().scan_metadata(_source(), ScanScope(("sales",), max_indexes=1))
    assert raised.value.code == "INDEX_LIMIT_EXCEEDED"
