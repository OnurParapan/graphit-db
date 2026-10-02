"""Engine dispatch stays centralized for CLI verification and snapshot scans."""

from pathlib import Path

import pytest
from pytest import MonkeyPatch

from graphit.scanners.mssql import MSSQLScanner
from graphit.scanners.oracle import OracleScanner
from graphit.scanners.postgresql import PostgreSQLScanner
from graphit.scanners.protocol import ConnectionTestResult
from graphit.scanners.registry import scanner_for, verify_connection
from graphit.sources import SourceConfig


def _source(engine: str) -> SourceConfig:
    return SourceConfig(
        name="erp",
        engine=engine,
        host="localhost",
        port={"postgresql": 5432, "mssql": 1433, "oracle": 1521}.get(engine, 1234),
        database_name="erp",
        username="reader",
        credential_env="ERP_PASSWORD",
        schemas=("public",),
        ssl_mode={"postgresql": "prefer", "mssql": "require", "oracle": "disable"}.get(
            engine, "disable"
        ),
    )


def test_scanner_registry_selects_every_supported_engine(tmp_path: Path) -> None:
    assert isinstance(scanner_for(_source("postgresql"), tmp_path), PostgreSQLScanner)
    assert isinstance(scanner_for(_source("mssql"), tmp_path), MSSQLScanner)
    assert isinstance(scanner_for(_source("oracle"), tmp_path), OracleScanner)

    with pytest.raises(ValueError, match="Unsupported source engine"):
        scanner_for(_source("mysql"), tmp_path)


@pytest.mark.parametrize("engine", ["postgresql", "mssql", "oracle"])
def test_verification_registry_dispatches_engine_without_changing_result(
    tmp_path: Path, monkeypatch: MonkeyPatch, engine: str
) -> None:
    expected = ConnectionTestResult("erp", "reader", "test", ("app",))
    calls: list[tuple[SourceConfig, Path | None]] = []

    def fake(source: SourceConfig, *, project_root: Path | None = None) -> ConnectionTestResult:
        calls.append((source, project_root))
        return expected

    monkeypatch.setattr(
        f"graphit.scanners.{engine if engine != 'mssql' else 'mssql'}.verify_connection", fake
    )

    assert verify_connection(_source(engine), project_root=tmp_path) == expected
    assert calls == [(_source(engine), tmp_path)]
