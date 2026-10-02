"""SQL Server adapter safety and catalog mapping without a live server."""

from types import SimpleNamespace
from typing import Any

import pytest
from pytest import MonkeyPatch

from graphit.scanners.mssql import MSSQLScanner, verify_connection
from graphit.scanners.protocol import ConnectionTestError, MetadataScanError, ScanScope
from graphit.sources import SourceConfig


def _source(**changes: object) -> SourceConfig:
    values = {
        "name": "erp",
        "engine": "mssql",
        "host": "sql.example.test",
        "port": 1433,
        "database_name": "erp",
        "username": "reader",
        "credential_env": "ERP_PASSWORD",
        "schemas": ("dbo", "sales"),
        "ssl_mode": "require",
    }
    values.update(changes)
    return SourceConfig(**values)  # type: ignore[arg-type]


class FakeCursor:
    def __init__(self, results: list[list[tuple[Any, ...]]]) -> None:
        self.results = results
        self.rows: list[tuple[Any, ...]] = []
        self.queries: list[tuple[str, tuple[Any, ...]]] = []
        self.timeout = 0

    def execute(self, query: str, *parameters: Any) -> "FakeCursor":
        self.queries.append((query, parameters))
        self.rows = self.results.pop(0)
        return self

    def fetchone(self) -> tuple[Any, ...] | None:
        return self.rows[0] if self.rows else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.rows

    def close(self) -> None:
        return None


class FakeConnection:
    def __init__(self, results: list[list[tuple[Any, ...]]]) -> None:
        self.cursor_object = FakeCursor(results)
        self.timeout = 0
        self.rolled_back = False
        self.closed = False

    def cursor(self) -> FakeCursor:
        return self.cursor_object

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        self.closed = True


def _install_driver(
    monkeypatch: MonkeyPatch, results: list[list[tuple[Any, ...]]]
) -> tuple[FakeConnection, dict[str, Any]]:
    connection = FakeConnection(results)
    call: dict[str, Any] = {}

    def connect(connection_string: str, **kwargs: Any) -> FakeConnection:
        call["connection_string"] = connection_string
        call.update(kwargs)
        return connection

    module = SimpleNamespace(
        drivers=lambda: ["ODBC Driver 18 for SQL Server"],
        connect=connect,
    )
    monkeypatch.setattr("graphit.scanners.mssql._driver", lambda: module)
    return connection, call


def test_mssql_verification_uses_readonly_odbc_and_rejects_write_principal(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("ERP_PASSWORD", "private;value}")
    connection, call = _install_driver(
        monkeypatch,
        [[("erp", "reader", "16.0.1000")], [(0,)], [("dbo",), ("sales",)]],
    )

    result = verify_connection(_source())

    assert result.schemas == ("dbo", "sales")
    assert call["readonly"] is True
    assert call["autocommit"] is False
    assert call["timeout"] == 5
    assert "Server=tcp:sql.example.test,1433" in call["connection_string"]
    assert "Encrypt=yes" in call["connection_string"]
    assert "TrustServerCertificate=no" in call["connection_string"]
    assert "private;value}" in call["connection_string"]
    assert "private;value}" not in repr(result)
    assert connection.timeout == 5
    assert connection.rolled_back and connection.closed

    _install_driver(monkeypatch, [[("erp", "writer", "16.0")], [(1,)]])
    with pytest.raises(ConnectionTestError) as raised:
        verify_connection(_source())
    assert raised.value.code == "READ_ONLY_NOT_ENFORCED"


def test_mssql_scanner_maps_tables_columns_keys_fks_and_indexes(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("ERP_PASSWORD", "secret")
    results: list[list[tuple[Any, ...]]] = [
        [("erp", "reader", "16.0")],
        [(0,)],
        [("dbo",), ("sales",)],
        [("dbo", "Customer", "U ", 0), ("sales", "Order", "U ", 1)],
        [
            ("dbo", "Customer", "Id", 1, "int", False),
            ("sales", "Order", "Id", 1, "bigint", False),
            ("sales", "Order", "CustomerId", 2, "int", False),
        ],
        [
            ("dbo", "Customer", "PK_Customer", "PK", 1, 1, "Id", None, None, None, 0, 0),
            ("sales", "Order", "PK_Order", "PK", 1, 1, "Id", None, None, None, 0, 0),
            (
                "sales",
                "Order",
                "FK_Order_Customer",
                "F",
                1,
                1,
                "CustomerId",
                "dbo",
                "Customer",
                "Id",
                0,
                0,
            ),
        ],
        [
            ("dbo", "Customer", "PK_Customer", "CLUSTERED", 1, 1, 1, 1, 0, 1, 0, "Id"),
            (
                "sales",
                "Order",
                "IX_Order_Customer",
                "NONCLUSTERED",
                0,
                0,
                1,
                1,
                0,
                1,
                0,
                "CustomerId",
            ),
            ("sales", "Order", "IX_Order_Customer", "NONCLUSTERED", 0, 0, 1, 1, 0, 0, 1, "Id"),
        ],
    ]
    connection, _ = _install_driver(monkeypatch, results)

    metadata = MSSQLScanner().scan_metadata(_source(), ScanScope(("dbo", "sales")))

    assert [(item.schema_name, item.name, item.partitioned) for item in metadata.tables] == [
        ("dbo", "Customer", False),
        ("sales", "Order", True),
    ]
    assert [item.kind for item in metadata.keys] == ["PRIMARY_KEY", "PRIMARY_KEY"]
    assert metadata.foreign_keys[0].source_columns == ("CustomerId",)
    assert metadata.foreign_keys[0].target_columns == ("Id",)
    assert metadata.foreign_keys[0].target_in_scope is True
    assert metadata.indexes[1].key_columns == ("CustomerId",)
    assert metadata.indexes[1].included_columns == ("Id",)
    assert all(query.lstrip().startswith("SELECT") for query, _ in connection.cursor_object.queries)
    assert not results


def test_mssql_missing_python_or_system_driver_is_actionable(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("ERP_PASSWORD", "secret")
    with monkeypatch.context() as missing_context:
        missing_context.setattr(
            "graphit.scanners.mssql.importlib.import_module",
            lambda _name: (_ for _ in ()).throw(ImportError()),
        )
        with pytest.raises(ConnectionTestError) as missing_python:
            verify_connection(_source())
    assert missing_python.value.code == "DRIVER_NOT_INSTALLED"

    monkeypatch.setattr(
        "graphit.scanners.mssql._driver",
        lambda: SimpleNamespace(drivers=lambda: [], connect=lambda *_args, **_kwargs: None),
    )
    with pytest.raises(ConnectionTestError) as missing_odbc:
        verify_connection(_source())
    assert missing_odbc.value.code == "ODBC_DRIVER_NOT_FOUND"


def test_mssql_invalid_scope_fails_before_loading_a_driver(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(
        "graphit.scanners.mssql._driver",
        lambda: pytest.fail("invalid scope must fail before driver loading"),
    )

    with pytest.raises(MetadataScanError) as raised:
        MSSQLScanner().scan_metadata(_source(), ScanScope(("dbo",), max_tables=0))
    assert raised.value.code == "INVALID_SCOPE"
