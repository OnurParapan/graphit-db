"""Opt-in catalog integration against an explicitly disposable PostgreSQL server."""

import json
import os
import secrets
import sqlite3
from contextlib import closing
from dataclasses import asdict, replace
from pathlib import Path
from uuid import uuid4

import anyio
import psycopg
import pytest
from mcp import Client
from mcp.types import TextContent
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from pytest import MonkeyPatch
from typer.testing import CliRunner

from graphit.cli import app
from graphit.mcp_server import create_server
from graphit.project import initialize_project
from graphit.queries import (
    list_table_indexes,
    search_objects,
    show_table,
    show_view,
    table_relationships,
)
from graphit.scanners.postgresql import MetadataScanError, PostgreSQLScanner, verify_connection
from graphit.scanners.protocol import ScanScope
from graphit.snapshots import scan_source
from graphit.sources import SourceConfig, add_source


def _disposable_admin_dsn() -> tuple[str, str, int, str]:
    dsn = os.environ.get("GRAPHIT_TEST_PG_ADMIN_DSN")
    if not dsn or os.environ.get("GRAPHIT_TEST_PG_DISPOSABLE") != "1":
        pytest.skip("Set GRAPHIT_TEST_PG_DISPOSABLE=1 and a disposable admin DSN")
    try:
        settings = conninfo_to_dict(dsn)
    except psycopg.Error:
        pytest.fail("Integration admin DSN is invalid", pytrace=False)
    host = settings.get("host")
    if host not in ("localhost", "127.0.0.1", "::1"):
        pytest.fail("Integration tests require an explicit loopback PostgreSQL host")
    database = settings.get("dbname")
    port = settings.get("port")
    if (
        not isinstance(host, str)
        or not isinstance(database, str)
        or not isinstance(port, (str, int))
    ):
        pytest.fail("Integration admin DSN must specify database and port")
    return dsn, host, int(port), database


def _create_fixture(
    admin: psycopg.Connection[tuple[object, ...]],
    schema_a: str,
    schema_b: str,
    role: str,
) -> None:
    # Setup is test infrastructure only. Graphit itself never issues these statements.
    admin.execute(
        sql.SQL("CREATE TABLE {}.{} (id integer PRIMARY KEY, email text UNIQUE)").format(
            sql.Identifier(schema_a), sql.Identifier("Customer")
        )
    )
    admin.execute(
        sql.SQL(
            "CREATE TABLE {}.{} (tenant_id integer NOT NULL, sku text NOT NULL, "
            "PRIMARY KEY (tenant_id, sku))"
        ).format(sql.Identifier(schema_b), sql.Identifier("Catalog Key"))
    )
    admin.execute(
        sql.SQL(
            "CREATE TABLE {}.{} (order_id integer NOT NULL, line_no integer NOT NULL, "
            "tenant_id integer NOT NULL, sku text NOT NULL, customer_id integer NOT NULL, "
            "CONSTRAINT order_item_pkey PRIMARY KEY (order_id, line_no), "
            "CONSTRAINT order_item_catalog_fk FOREIGN KEY (tenant_id, sku) "
            "REFERENCES {}.{} (tenant_id, sku), "
            "CONSTRAINT order_item_customer_fk FOREIGN KEY (customer_id) "
            "REFERENCES {}.{} (id))"
        ).format(
            sql.Identifier(schema_a),
            sql.Identifier("Order Item"),
            sql.Identifier(schema_b),
            sql.Identifier("Catalog Key"),
            sql.Identifier(schema_a),
            sql.Identifier("Customer"),
        )
    )
    admin.execute(
        sql.SQL(
            "CREATE TABLE {}.{} (id integer PRIMARY KEY, manager_id integer, "
            "CONSTRAINT employee_manager_fk FOREIGN KEY (manager_id) REFERENCES {}.{} (id))"
        ).format(
            sql.Identifier(schema_b),
            sql.Identifier("Employee"),
            sql.Identifier(schema_b),
            sql.Identifier("Employee"),
        )
    )
    admin.execute(
        sql.SQL(
            "CREATE INDEX {} ON {}.{} USING btree (customer_id, lower(sku)) "
            "INCLUDE (tenant_id) WHERE line_no > 0"
        ).format(
            sql.Identifier("Order Lookup"),
            sql.Identifier(schema_a),
            sql.Identifier("Order Item"),
        )
    )
    admin.execute(
        sql.SQL("CREATE VIEW {}.{} AS SELECT order_id, line_no, customer_id FROM {}.{}").format(
            sql.Identifier(schema_a),
            sql.Identifier("Order Summary"),
            sql.Identifier(schema_a),
            sql.Identifier("Order Item"),
        )
    )
    admin.execute(
        sql.SQL(
            "CREATE MATERIALIZED VIEW {}.{} AS SELECT id, email FROM {}.{} WITH NO DATA"
        ).format(
            sql.Identifier(schema_b),
            sql.Identifier("Customer Cache"),
            sql.Identifier(schema_a),
            sql.Identifier("Customer"),
        )
    )
    for schema in (schema_a, schema_b):
        admin.execute(
            sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                sql.Identifier(schema), sql.Identifier(role)
            )
        )
        admin.execute(
            sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA {} TO {}").format(
                sql.Identifier(schema), sql.Identifier(role)
            )
        )


def test_real_postgresql_catalog_scan_and_snapshot(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    admin_dsn, host, port, database = _disposable_admin_dsn()
    suffix = uuid4().hex[:10]
    schema_a = f"graphit_int_{suffix}"
    schema_b = f"Graphit Int {suffix}"
    hidden_schema = f"graphit_hidden_{suffix}"
    role = f"graphit_reader_{suffix}"
    password = secrets.token_urlsafe(24)
    created_schemas: list[str] = []
    role_created = False
    with psycopg.connect(admin_dsn, autocommit=True) as admin:
        try:
            admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema_a)))
            created_schemas.append(schema_a)
            admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema_b)))
            created_schemas.append(schema_b)
            admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(hidden_schema)))
            created_schemas.append(hidden_schema)
            admin.execute(
                sql.SQL("REVOKE USAGE ON SCHEMA {} FROM PUBLIC").format(
                    sql.Identifier(hidden_schema)
                )
            )
            admin.execute(
                sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                    sql.Identifier(role), sql.Literal(password)
                )
            )
            role_created = True
            _create_fixture(admin, schema_a, schema_b, role)
            monkeypatch.setenv("GRAPHIT_TEST_READER_PASSWORD", password)
            source = SourceConfig(
                name="live",
                host=host,
                port=port,
                database_name=database,
                username=role,
                credential_env="GRAPHIT_TEST_READER_PASSWORD",
                schemas=(schema_a, schema_b),
            )
            verified = verify_connection(source)
            assert (verified.database, verified.username) == (database, role)
            assert schema_a in verified.schemas
            assert schema_b in verified.schemas
            assert hidden_schema not in verified.schemas
            with psycopg.connect(
                host=host, port=port, dbname=database, user=role, password=password
            ) as reader:
                privileges = reader.execute(
                    "SELECT pg_catalog.has_schema_privilege(%s, 'CREATE'), "
                    "pg_catalog.has_table_privilege(%s, 'INSERT'), "
                    "pg_catalog.has_schema_privilege(%s, 'USAGE')",
                    (schema_a, f'{schema_a}."Customer"', hidden_schema),
                ).fetchone()
                assert privileges == (False, False, False)

            scanner = PostgreSQLScanner()
            restricted = replace(source, schemas=(*source.schemas, hidden_schema))
            with pytest.raises(MetadataScanError) as raised:
                scanner.scan_metadata(restricted, ScanScope(restricted.schemas))
            assert raised.value.code == "SCHEMA_PERMISSION_DENIED"
            metadata = scanner.scan_metadata(source, ScanScope(source.schemas))
            kinds = {(item.schema_name, item.name): item.kind for item in metadata.tables}
            assert len(kinds) == 6
            assert kinds[(schema_a, "Order Item")] == "TABLE"
            assert kinds[(schema_a, "Order Summary")] == "VIEW"
            assert kinds[(schema_b, "Customer Cache")] == "MATERIALIZED_VIEW"
            assert any(
                item.table_name == "Order Summary" and item.name == "customer_id"
                for item in metadata.columns
            )
            assert any(
                item.table_name == "Customer Cache" and item.name == "email"
                for item in metadata.columns
            )
            assert any(
                item.table_name == "Order Item"
                and item.kind == "PRIMARY_KEY"
                and item.columns == ("order_id", "line_no")
                for item in metadata.keys
            )
            lookup = next(item for item in metadata.indexes if item.name == "Order Lookup")
            assert (lookup.schema_name, lookup.table_name) == (schema_a, "Order Item")
            assert lookup.key_columns == ("customer_id", None)
            assert lookup.included_columns == ("tenant_id",)
            assert (lookup.unique, lookup.valid, lookup.ready, lookup.partial) == (
                False,
                True,
                True,
                True,
            )
            composite = next(
                item for item in metadata.foreign_keys if item.name == "order_item_catalog_fk"
            )
            assert composite.source_columns == composite.target_columns == ("tenant_id", "sku")
            assert composite.target_in_scope is True
            assert any(
                item.name == "employee_manager_fk" and item.source_table == item.target_table
                for item in metadata.foreign_keys
            )
            with pytest.raises(MetadataScanError) as raised:
                scanner.scan_metadata(source, ScanScope(source.schemas, max_tables=5))
            assert raised.value.code == "TABLE_LIMIT_EXCEEDED"
            with pytest.raises(MetadataScanError) as raised:
                scanner.scan_metadata(source, ScanScope(source.schemas, max_columns=1))
            assert raised.value.code == "COLUMN_LIMIT_EXCEEDED"

            initialize_project(tmp_path)
            add_source(tmp_path, source)
            stored = scan_source(tmp_path, "live")
            assert stored.version == 1 and stored.object_count > 0
            with closing(sqlite3.connect(tmp_path / ".graphit" / "graphit.db")) as local:
                assert local.execute(
                    """SELECT COUNT(*) FROM objects WHERE snapshot_id = ?
                    AND object_type = 'INDEX' AND object_name = 'Order Lookup'""",
                    (stored.snapshot_id,),
                ).fetchone() == (1,)
            assert show_view(tmp_path, "live", f'"{schema_a}"."Order Summary"').kind == "VIEW"
            assert (
                show_view(tmp_path, "live", f'"{schema_b}"."Customer Cache"').kind
                == "MATERIALIZED_VIEW"
            )
            assert show_table(tmp_path, "live", f'"{schema_a}"."Order Item"').columns
            assert any(
                item.name == "order_item_catalog_fk"
                for item in table_relationships(
                    tmp_path, "live", f'"{schema_a}"."Order Item"'
                ).outgoing
            )
            assert search_objects(tmp_path, "live", "Order Summary").matches[0].kind == "VIEW"
            relation = f'"{schema_a}"."Order Item"'
            saved_indexes = list_table_indexes(tmp_path, "live", relation)
            saved_lookup = next(
                item for item in saved_indexes.indexes if item.name == "Order Lookup"
            )
            assert [(part.position, part.column_name) for part in saved_lookup.key_columns] == [
                (1, "customer_id"),
                (2, None),
            ]
            assert [
                (part.position, part.column_name) for part in saved_lookup.included_columns
            ] == [(1, "tenant_id")]
            expected = json.loads(json.dumps(asdict(saved_indexes)))
            cli_result = CliRunner().invoke(
                app,
                ["indexes", relation, "--source", "live", "--project", str(tmp_path), "--json"],
            )
            assert cli_result.exit_code == 0
            assert json.loads(cli_result.stdout) == expected

            async def read_mcp_indexes() -> dict[str, object]:
                async with Client(create_server(tmp_path)) as client:
                    result = await client.call_tool(
                        "get_index_context", {"source": "live", "name": relation}
                    )
                assert result.is_error is False and result.structured_content is None
                assert len(result.content) == 1
                content = result.content[0]
                assert isinstance(content, TextContent)
                payload = json.loads(content.text)
                assert isinstance(payload, dict)
                return payload

            assert anyio.run(read_mcp_indexes) == expected
        finally:
            # Only drop exact objects this test created in an explicitly disposable DB.
            for schema in created_schemas:
                admin.execute(
                    sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema))
                )
            if role_created:
                admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))
