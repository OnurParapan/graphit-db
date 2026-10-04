"""Opt-in end-to-end scan against an explicitly disposable SQL Server."""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pyodbc  # type: ignore[import-not-found]
import pytest

from graphit.project import initialize_project
from graphit.queries import show_table, table_relationships
from graphit.scanners.mssql import MSSQLScanner, verify_connection
from graphit.scanners.protocol import ScanScope
from graphit.snapshots import scan_source
from graphit.sources import SourceConfig, add_source

pytestmark = pytest.mark.skipif(
    os.environ.get("GRAPHIT_TEST_MSSQL_DISPOSABLE") != "1"
    or not os.environ.get("GRAPHIT_TEST_MSSQL_ADMIN_URL"),
    reason="requires an explicitly disposable loopback SQL Server",
)


def _admin_details() -> tuple[str, int, str, str]:
    parsed = urlsplit(os.environ["GRAPHIT_TEST_MSSQL_ADMIN_URL"])
    if parsed.scheme != "mssql" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        pytest.fail("Integration tests require an explicit loopback mssql:// admin URL")
    if parsed.username is None or parsed.password is None or parsed.port is None:
        pytest.fail("The disposable SQL Server URL must include user, password, and port")
    return parsed.hostname, parsed.port, unquote(parsed.username), unquote(parsed.password)


def _admin_connection(database: str = "master") -> pyodbc.Connection:
    host, port, username, password = _admin_details()
    return pyodbc.connect(
        "Driver={ODBC Driver 18 for SQL Server};"
        f"Server={host},{port};Database={database};UID={username};PWD={password};"
        "Encrypt=no;TrustServerCertificate=no;APP=GraphitIntegrationFixture;",
        autocommit=True,
        timeout=10,
    )


def test_real_mssql_catalog_scan_and_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    suffix = uuid.uuid4().hex[:10]
    database = f"graphit_{suffix}"
    login = f"graphit_reader_{suffix}"
    password = f"Graphit-{suffix}-Strong!9"
    quoted_database = f"[{database}]"
    quoted_login = f"[{login}]"

    with _admin_connection() as admin:
        admin.execute(f"CREATE DATABASE {quoted_database}")
        admin.execute(
            f"CREATE LOGIN {quoted_login} WITH PASSWORD = '{password}', CHECK_POLICY = OFF"
        )
    try:
        with _admin_connection(database) as admin:
            admin.execute("CREATE SCHEMA crm")
            admin.execute("CREATE SCHEMA sales")
            admin.execute(
                """CREATE TABLE crm.Customer (
                Id int NOT NULL,
                Region varchar(10) NOT NULL,
                Name nvarchar(100) NULL,
                CONSTRAINT PK_Customer PRIMARY KEY (Id, Region))"""
            )
            admin.execute(
                """CREATE TABLE sales.[Order] (
                Id bigint NOT NULL CONSTRAINT PK_Order PRIMARY KEY,
                CustomerId int NOT NULL,
                Region varchar(10) NOT NULL,
                Status varchar(20) NULL,
                CONSTRAINT FK_Order_Customer FOREIGN KEY (CustomerId, Region)
                  REFERENCES crm.Customer (Id, Region))"""
            )
            admin.execute(
                "CREATE INDEX IX_Order_Customer ON sales.[Order] (CustomerId, Region) "
                "INCLUDE (Status) WHERE Status IS NOT NULL"
            )
            admin.execute(
                "CREATE VIEW sales.OrderSummary AS "
                "SELECT CustomerId, Region, COUNT_BIG(*) AS OrderCount "
                "FROM sales.[Order] GROUP BY CustomerId, Region"
            )
            admin.execute(f"CREATE USER {quoted_login} FOR LOGIN {quoted_login}")
            admin.execute(f"GRANT SELECT ON SCHEMA::crm TO {quoted_login}")
            admin.execute(f"GRANT SELECT ON SCHEMA::sales TO {quoted_login}")
            admin.execute(f"GRANT VIEW DEFINITION TO {quoted_login}")

        monkeypatch.setenv("GRAPHIT_TEST_MSSQL_READER_PASSWORD", password)
        source = SourceConfig(
            name="live",
            engine="mssql",
            host=_admin_details()[0],
            port=_admin_details()[1],
            database_name=database,
            username=login,
            credential_env="GRAPHIT_TEST_MSSQL_READER_PASSWORD",
            schemas=("crm", "sales"),
            ssl_mode="disable",
        )
        with pyodbc.connect(
            "Driver={ODBC Driver 18 for SQL Server};"
            f"Server=tcp:{source.host},{source.port};Database={{{database}}};"
            f"UID={{{login}}};PWD={{{password}}};"
            "Encrypt=no;TrustServerCertificate=no;APP=Graphit;",
            autocommit=False,
            timeout=5,
            readonly=True,
        ) as reader:
            reader.timeout = 5
            assert reader.execute("SELECT DB_NAME()").fetchone()[0] == database
        verified = verify_connection(source)
        assert verified.database == database
        assert set(verified.schemas) == {"crm", "sales"}

        metadata = MSSQLScanner().scan_metadata(source, ScanScope(source.schemas))
        assert {(item.schema_name, item.name, item.kind) for item in metadata.tables} == {
            ("crm", "Customer", "TABLE"),
            ("sales", "Order", "TABLE"),
            ("sales", "OrderSummary", "VIEW"),
        }
        foreign_key = next(
            item for item in metadata.foreign_keys if item.name == "FK_Order_Customer"
        )
        assert foreign_key.source_columns == ("CustomerId", "Region")
        assert foreign_key.target_columns == ("Id", "Region")
        index = next(item for item in metadata.indexes if item.name == "IX_Order_Customer")
        assert index.key_columns == ("CustomerId", "Region")
        assert index.included_columns == ("Status",)
        assert index.partial is True

        initialize_project(tmp_path)
        add_source(tmp_path, source)
        stored = scan_source(tmp_path, "live")
        assert stored.version == 1
        assert show_table(tmp_path, "live", '"sales"."Order"').columns
        relationships = table_relationships(tmp_path, "live", '"sales"."Order"')
        assert relationships.outgoing[0].name == "FK_Order_Customer"

        with _admin_connection(database) as admin:
            admin.execute(f"ALTER ROLE db_datawriter ADD MEMBER {quoted_login}")
        privileged = verify_connection(source)
        assert privileged.warnings
        assert privileged.warnings[0].startswith("PRIVILEGED_CREDENTIAL:")
    finally:
        with _admin_connection() as admin:
            admin.execute(
                f"ALTER DATABASE {quoted_database} SET SINGLE_USER WITH ROLLBACK IMMEDIATE"
            )
            admin.execute(f"DROP DATABASE {quoted_database}")
            admin.execute(f"DROP LOGIN {quoted_login}")
