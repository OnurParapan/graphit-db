"""Opt-in end-to-end scan against an explicitly disposable Oracle database."""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from urllib.parse import unquote, urlsplit

import oracledb
import pytest

from graphit.project import initialize_project
from graphit.queries import show_table, table_relationships
from graphit.scanners.oracle import OracleScanner, verify_connection
from graphit.scanners.protocol import ScanScope
from graphit.snapshots import scan_source
from graphit.sources import SourceConfig, add_source

pytestmark = pytest.mark.skipif(
    os.environ.get("GRAPHIT_TEST_ORACLE_DISPOSABLE") != "1"
    or not os.environ.get("GRAPHIT_TEST_ORACLE_ADMIN_URL"),
    reason="requires an explicitly disposable loopback Oracle database",
)


def _admin_details() -> tuple[str, int, str, str, str]:
    parsed = urlsplit(os.environ["GRAPHIT_TEST_ORACLE_ADMIN_URL"])
    if parsed.scheme != "oracle" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        pytest.fail("Integration tests require an explicit loopback oracle:// admin URL")
    if parsed.username is None or parsed.password is None or parsed.port is None:
        pytest.fail("The disposable Oracle URL must include user, password, and port")
    service = parsed.path.removeprefix("/")
    if not service:
        pytest.fail("The disposable Oracle URL must include a service name")
    return (
        parsed.hostname,
        parsed.port,
        unquote(parsed.username),
        unquote(parsed.password),
        service,
    )


def _connection(username: str, password: str) -> oracledb.Connection:
    host, port, _admin_user, _admin_password, service = _admin_details()
    return oracledb.connect(
        user=username,
        password=password,
        host=host,
        port=port,
        service_name=service,
        tcp_connect_timeout=10,
    )


def _admin_connection() -> oracledb.Connection:
    _host, _port, username, password, _service = _admin_details()
    return _connection(username, password)


def test_real_oracle_catalog_scan_and_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    suffix = uuid.uuid4().hex[:8].upper()
    crm = f"GCRM_{suffix}"
    sales = f"GSALES_{suffix}"
    reader = f"GREADER_{suffix}"
    owner_password = f"Owner{suffix}9"
    reader_password = f"Reader{suffix}9"

    with _admin_connection() as admin:
        cursor = admin.cursor()
        for username, password in (
            (crm, owner_password),
            (sales, owner_password),
            (reader, reader_password),
        ):
            cursor.execute(
                f'CREATE USER {username} IDENTIFIED BY "{password}" QUOTA UNLIMITED ON USERS'
            )
            cursor.execute(f"GRANT CREATE SESSION TO {username}")
        cursor.execute(f"GRANT CREATE TABLE TO {crm}")
        cursor.execute(f"GRANT CREATE TABLE, CREATE VIEW TO {sales}")

    try:
        with _connection(crm, owner_password) as owner:
            cursor = owner.cursor()
            cursor.execute(
                "CREATE TABLE CUSTOMER ("
                "ID NUMBER(10,0) NOT NULL, REGION VARCHAR2(10) NOT NULL, "
                "NAME VARCHAR2(100), CONSTRAINT PK_CUSTOMER PRIMARY KEY (ID, REGION))"
            )
            cursor.execute(f"GRANT REFERENCES ON CUSTOMER TO {sales}")
            cursor.execute(f"GRANT SELECT ON CUSTOMER TO {reader}")

        with _connection(sales, owner_password) as owner:
            cursor = owner.cursor()
            cursor.execute(
                "CREATE TABLE ORDERS ("
                "ID NUMBER(19,0) NOT NULL CONSTRAINT PK_ORDERS PRIMARY KEY, "
                "CUSTOMER_ID NUMBER(10,0) NOT NULL, REGION VARCHAR2(10) NOT NULL, "
                "STATUS VARCHAR2(20), CONSTRAINT FK_ORDER_CUSTOMER "
                f"FOREIGN KEY (CUSTOMER_ID, REGION) REFERENCES {crm}.CUSTOMER (ID, REGION))"
            )
            cursor.execute("CREATE INDEX IX_ORDER_CUSTOMER ON ORDERS (CUSTOMER_ID, REGION)")
            cursor.execute(
                "CREATE VIEW ORDER_SUMMARY AS SELECT CUSTOMER_ID, REGION, COUNT(*) ORDER_COUNT "
                "FROM ORDERS GROUP BY CUSTOMER_ID, REGION"
            )
            cursor.execute(f"GRANT SELECT ON ORDERS TO {reader}")
            cursor.execute(f"GRANT SELECT ON ORDER_SUMMARY TO {reader}")

        monkeypatch.setenv("GRAPHIT_TEST_ORACLE_READER_PASSWORD", reader_password)
        host, port, _admin_user, _admin_password, service = _admin_details()
        source = SourceConfig(
            name="live",
            engine="oracle",
            host=host,
            port=port,
            database_name=service,
            username=reader,
            credential_env="GRAPHIT_TEST_ORACLE_READER_PASSWORD",
            schemas=(crm, sales),
            ssl_mode="disable",
        )

        verified = verify_connection(source)
        assert verified.database.casefold() == service.casefold()
        assert {crm, sales}.issubset(verified.schemas)

        metadata = OracleScanner().scan_metadata(source, ScanScope(source.schemas))
        assert {(item.schema_name, item.name, item.kind) for item in metadata.tables} == {
            (crm, "CUSTOMER", "TABLE"),
            (sales, "ORDERS", "TABLE"),
            (sales, "ORDER_SUMMARY", "VIEW"),
        }
        foreign_key = next(
            item for item in metadata.foreign_keys if item.name == "FK_ORDER_CUSTOMER"
        )
        assert foreign_key.source_columns == ("CUSTOMER_ID", "REGION")
        assert foreign_key.target_columns == ("ID", "REGION")
        index = next(item for item in metadata.indexes if item.name == "IX_ORDER_CUSTOMER")
        assert index.key_columns == ("CUSTOMER_ID", "REGION")

        initialize_project(tmp_path)
        add_source(tmp_path, source)
        stored = scan_source(tmp_path, "live")
        assert stored.version == 1
        assert show_table(tmp_path, "live", f'"{sales}"."ORDERS"').columns
        relationships = table_relationships(tmp_path, "live", f'"{sales}"."ORDERS"')
        assert relationships.outgoing[0].name == "FK_ORDER_CUSTOMER"
    finally:
        with _admin_connection() as admin:
            cursor = admin.cursor()
            for username in (reader, sales, crm):
                cursor.execute(f"DROP USER {username} CASCADE")
