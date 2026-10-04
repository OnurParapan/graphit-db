"""Oracle adapter safety and catalog mapping without a live server."""

from types import SimpleNamespace
from typing import Any

import pytest
from pytest import MonkeyPatch

from graphit.scanners.oracle import OracleScanner, verify_connection
from graphit.scanners.protocol import ConnectionTestError, MetadataScanError, ScanScope
from graphit.sources import SourceConfig


def _source(**changes: object) -> SourceConfig:
    values = {
        "name": "erp",
        "engine": "oracle",
        "host": "ora.example.test",
        "port": 1521,
        "database_name": "ERPPRD",
        "username": "reader",
        "credential_env": "ERP_PASSWORD",
        "schemas": ("CRM", "SALES"),
        "ssl_mode": "disable",
    }
    values.update(changes)
    return SourceConfig(**values)  # type: ignore[arg-type]


class FakeCursor:
    def __init__(self, result_map: dict[str, list[tuple[Any, ...]]], queries: list[str]) -> None:
        self.result_map = result_map
        self.queries = queries
        self.rows: list[tuple[Any, ...]] = []

    def execute(self, query: str, _bindings: Any = None) -> "FakeCursor":
        self.queries.append(query)
        normalized = query.upper()
        if normalized.startswith("SET TRANSACTION"):
            key = "transaction"
        elif "FROM ALL_USERS" in normalized:
            key = "schemas"
        elif "SYS_CONTEXT" in normalized:
            key = "identity"
        elif "FROM ALL_OBJECTS" in normalized:
            key = "tables"
        elif "FROM ALL_TAB_COLUMNS" in normalized:
            key = "columns"
        elif "FROM ALL_INDEXES I" in normalized:
            key = "indexes"
        elif "FROM ALL_CONSTRAINTS C" in normalized:
            key = "constraints"
        else:
            raise AssertionError(f"Unexpected Oracle query: {query}")
        self.rows = self.result_map.get(key, [])
        return self

    def fetchone(self) -> tuple[Any, ...] | None:
        return self.rows[0] if self.rows else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.rows

    def close(self) -> None:
        return None


class FakeConnection:
    version = "19.22.0"

    def __init__(self, result_map: dict[str, list[tuple[Any, ...]]]) -> None:
        self.queries: list[str] = []
        self.result_map = result_map
        self.call_timeout = 0
        self.rolled_back = False
        self.closed = False

    def cursor(self) -> FakeCursor:
        return FakeCursor(self.result_map, self.queries)

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        self.closed = True


def _install_driver(
    monkeypatch: MonkeyPatch, result_map: dict[str, list[tuple[Any, ...]]]
) -> tuple[FakeConnection, dict[str, Any]]:
    connection = FakeConnection(result_map)
    call: dict[str, Any] = {}

    class Params:
        def __init__(self, **kwargs: Any) -> None:
            call["params"] = kwargs

        def get_connect_string(self) -> str:
            return "(DESCRIPTION=hidden-safe-dsn)"

    def connect(**kwargs: Any) -> FakeConnection:
        call["connect"] = kwargs
        return connection

    module = SimpleNamespace(ConnectParams=Params, connect=connect)
    monkeypatch.setattr("graphit.scanners.oracle._driver", lambda: module)
    return connection, call


def test_oracle_verification_starts_read_only_transaction_and_hides_secret(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("ERP_PASSWORD", "oracle-private")
    connection, call = _install_driver(
        monkeypatch,
        {
            "identity": [("ERPPRD", "READER")],
            "schemas": [("CRM",), ("SALES",)],
        },
    )

    result = verify_connection(_source())

    assert result.schemas == ("CRM", "SALES")
    assert connection.queries[0] == "SET TRANSACTION READ ONLY"
    assert "FROM all_users" in connection.queries[2]
    assert "oracle_maintained = 'N'" in connection.queries[2]
    assert connection.call_timeout == 5000
    assert call["params"]["tcp_connect_timeout"] == 5
    assert call["connect"]["password"] == "oracle-private"
    assert "oracle-private" not in repr(result)
    assert connection.rolled_back and connection.closed


def test_oracle_sys_is_rejected_even_after_read_only_statement(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("ERP_PASSWORD", "secret")
    _install_driver(
        monkeypatch,
        {"identity": [("ERPPRD", "SYS")], "schemas": [("SALES",)]},
    )

    with pytest.raises(ConnectionTestError) as raised:
        verify_connection(_source(username="SYS"))
    assert raised.value.code == "READ_ONLY_NOT_ENFORCED"


def test_oracle_scanner_maps_tables_columns_composite_fk_and_expression_index(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("ERP_PASSWORD", "secret")
    result_map: dict[str, list[tuple[Any, ...]]] = {
        "identity": [("ERPPRD", "READER")],
        "schemas": [("CRM",), ("SALES",)],
        "tables": [
            ("CRM", "CUSTOMER", "TABLE"),
            ("SALES", "ORDERS", "TABLE"),
            ("SALES", "ORDER_TOTALS", "MATERIALIZED VIEW"),
        ],
        "columns": [
            ("CRM", "CUSTOMER", "ID", 1, "NUMBER", 22, 10, 0, 0, "N"),
            ("CRM", "CUSTOMER", "REGION", 2, "VARCHAR2", 20, None, None, 20, "N"),
            ("SALES", "ORDERS", "CUSTOMER_ID", 1, "NUMBER", 22, 10, 0, 0, "N"),
            ("SALES", "ORDERS", "REGION", 2, "VARCHAR2", 20, None, None, 20, "N"),
        ],
        "constraints": [
            (
                "CRM",
                "CUSTOMER",
                "PK_CUSTOMER",
                "P",
                "ENABLED",
                "VALIDATED",
                1,
                "ID",
                None,
                None,
                None,
            ),
            (
                "CRM",
                "CUSTOMER",
                "PK_CUSTOMER",
                "P",
                "ENABLED",
                "VALIDATED",
                2,
                "REGION",
                None,
                None,
                None,
            ),
            (
                "SALES",
                "ORDERS",
                "FK_ORDER_CUSTOMER",
                "R",
                "ENABLED",
                "VALIDATED",
                1,
                "CUSTOMER_ID",
                "CRM",
                "CUSTOMER",
                "ID",
            ),
            (
                "SALES",
                "ORDERS",
                "FK_ORDER_CUSTOMER",
                "R",
                "ENABLED",
                "VALIDATED",
                2,
                "REGION",
                "CRM",
                "CUSTOMER",
                "REGION",
            ),
        ],
        "indexes": [
            ("CRM", "CUSTOMER", "PK_CUSTOMER", "NORMAL", "UNIQUE", "VALID", 1, "ID", 1),
            ("CRM", "CUSTOMER", "PK_CUSTOMER", "NORMAL", "UNIQUE", "VALID", 2, "REGION", 1),
            (
                "SALES",
                "ORDERS",
                "IX_ORDER_EXPR",
                "FUNCTION-BASED NORMAL",
                "NONUNIQUE",
                "VALID",
                1,
                "SYS_NC00003$",
                0,
            ),
        ],
    }
    connection, _ = _install_driver(monkeypatch, result_map)

    metadata = OracleScanner().scan_metadata(_source(), ScanScope(("CRM", "SALES")))

    assert [item.kind for item in metadata.tables] == ["TABLE", "TABLE", "MATERIALIZED_VIEW"]
    assert metadata.columns[0].data_type == "NUMBER(10,0)"
    assert metadata.columns[1].data_type == "VARCHAR2(20)"
    assert metadata.keys[0].columns == ("ID", "REGION")
    assert metadata.foreign_keys[0].source_columns == ("CUSTOMER_ID", "REGION")
    assert metadata.foreign_keys[0].target_columns == ("ID", "REGION")
    assert metadata.indexes[1].key_columns == (None,)
    assert connection.queries[0] == "SET TRANSACTION READ ONLY"


def test_oracle_missing_driver_is_actionable(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("ERP_PASSWORD", "secret")
    monkeypatch.setattr(
        "graphit.scanners.oracle.importlib.import_module",
        lambda _name: (_ for _ in ()).throw(ImportError()),
    )
    with pytest.raises(ConnectionTestError) as raised:
        verify_connection(_source())
    assert raised.value.code == "DRIVER_NOT_INSTALLED"


def test_oracle_invalid_scope_fails_before_loading_a_driver(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(
        "graphit.scanners.oracle._driver",
        lambda: pytest.fail("invalid scope must fail before driver loading"),
    )

    with pytest.raises(MetadataScanError) as raised:
        OracleScanner().scan_metadata(_source(), ScanScope(("CRM",), max_columns=0))
    assert raised.value.code == "INVALID_SCOPE"
