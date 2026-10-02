"""Exact, bounded saved-view detail across domain, CLI, and MCP."""

import json
from pathlib import Path

import pytest
from mcp import Client
from mcp.types import TextContent
from pytest import MonkeyPatch
from typer.testing import CliRunner

import graphit.mcp_server as mcp_server
from graphit.cli import app
from graphit.mcp_server import MAX_MCP_RESPONSE_CHARS, create_server
from graphit.project import initialize_project
from graphit.queries import QueryError, get_relevant_context, show_view
from graphit.scanners.protocol import (
    ColumnMetadata,
    MetadataSnapshot,
    SchemaMetadata,
    TableMetadata,
)
from graphit.snapshots import persist_snapshot
from graphit.sources import SourceConfig, add_source


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _source(name: str) -> SourceConfig:
    return SourceConfig(
        name=name,
        host="localhost",
        port=5432,
        database_name="erp",
        username="reader",
        credential_env="ERP_PASSWORD",
        schemas=("sales", "archive"),
    )


def _snapshot(name: str, *, views: bool = True) -> MetadataSnapshot:
    relations: tuple[TableMetadata, ...] = (TableMetadata("sales", "orders", False),)
    columns: tuple[ColumnMetadata, ...] = (
        ColumnMetadata("sales", "orders", "id", 1, "integer", False),
    )
    if views:
        relations += (
            TableMetadata("sales", "Sales.View", False, "VIEW"),
            TableMetadata("archive", "Sales.View", False, "VIEW"),
            TableMetadata("sales", "Daily Cache", False, "MATERIALIZED_VIEW"),
        )
        columns += (
            ColumnMetadata("sales", "Sales.View", "customer_id", 1, "integer", True),
            ColumnMetadata("sales", "Sales.View", "amount", 2, "numeric", False),
            ColumnMetadata("archive", "Sales.View", "old_id", 1, "integer", True),
            ColumnMetadata("sales", "Daily Cache", "total", 1, "numeric", True),
        )
    return MetadataSnapshot(
        source_name=name,
        schemas=(SchemaMetadata("sales"), SchemaMetadata("archive")),
        tables=relations,
        columns=columns,
    )


def _project(root: Path) -> None:
    initialize_project(root)
    for name, views in (("erp", True), ("other", False)):
        source = _source(name)
        add_source(root, source)
        persist_snapshot(root, source, _snapshot(name, views=views))


def test_saved_view_detail_bounds_identity_and_source_isolation(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    _project(tmp_path)

    def no_source_connection(*args: object, **kwargs: object) -> None:
        raise AssertionError("View detail must use only saved metadata")

    monkeypatch.setattr("psycopg.connect", no_source_connection)
    result = show_view(tmp_path, "erp", '"sales"."Sales.View"', limit=1)
    assert (result.kind, result.qualified_name, result.snapshot_version) == (
        "VIEW",
        '"sales"."Sales.View"',
        1,
    )
    assert [
        (column.name, column.data_type, column.not_null_declared) for column in result.columns
    ] == [("customer_id", "integer", False)]
    assert result.columns_truncated is True
    full = show_view(tmp_path, "erp", '"sales"."Sales.View"', limit=2)
    assert [column.not_null_declared for column in full.columns] == [False, True]
    assert full.columns_truncated is False
    assert show_view(tmp_path, "erp", '"sales"."Daily Cache"').kind == "MATERIALIZED_VIEW"
    assert any(
        call.tool == "get_view" and call.arguments["name"] == '"sales"."Daily Cache"'
        for call in get_relevant_context(tmp_path, "erp", "Daily Cache").suggested_followups
    )

    for reference, code in (
        ('"Sales.View"', "AMBIGUOUS_VIEW"),
        ("sales.orders", "VIEW_NOT_FOUND"),
        ('"sales"."missing"', "VIEW_NOT_FOUND"),
        ("sales..bad", "INVALID_VIEW"),
    ):
        with pytest.raises(QueryError) as raised:
            show_view(tmp_path, "erp", reference)
        assert raised.value.code == code
    with pytest.raises(QueryError) as raised:
        show_view(tmp_path, "other", '"sales"."Sales.View"')
    assert raised.value.code == "VIEW_NOT_FOUND"
    with pytest.raises(QueryError) as raised:
        show_view(tmp_path, "erp", '"sales"."Sales.View"', limit=101)
    assert raised.value.code == "INVALID_LIMIT"

    source = _source("erp")
    persist_snapshot(tmp_path, source, _snapshot("erp", views=False))
    with pytest.raises(QueryError) as raised:
        show_view(tmp_path, "erp", '"sales"."Sales.View"')
    assert raised.value.code == "VIEW_NOT_FOUND"


@pytest.mark.anyio
async def test_view_cli_and_mcp_share_bounded_local_contract(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    _project(tmp_path)

    def no_source_connection(*args: object, **kwargs: object) -> None:
        raise AssertionError("View tools must not contact PostgreSQL")

    monkeypatch.setattr("psycopg.connect", no_source_connection)
    args = [
        "show-view",
        '"sales"."Sales.View"',
        "--source",
        "erp",
        "--project",
        str(tmp_path),
        "--limit",
        "1",
    ]
    runner = CliRunner()
    human = runner.invoke(app, args)
    assert human.exit_code == 0, human.output
    assert 'VIEW "sales"."Sales.View"' in human.stdout
    assert "nullability not declared" in human.stdout
    assert "More columns exist" in human.stdout
    machine = runner.invoke(app, [*args, "--json"])
    assert machine.exit_code == 0, machine.output
    cli_payload = json.loads(machine.stdout)
    assert cli_payload["kind"] == "VIEW"
    assert cli_payload["columns_truncated"] is True
    assert "foreign_keys" not in cli_payload and "lineage" not in cli_payload
    missing = runner.invoke(
        app,
        ["show-view", "sales.orders", "--source", "erp", "--project", str(tmp_path)],
    )
    assert missing.exit_code == 6 and "VIEW_NOT_FOUND" in missing.output

    async with Client(create_server(tmp_path)) as client:
        result = await client.call_tool(
            "get_view", {"source": "erp", "name": '"sales"."Sales.View"', "limit": 1}
        )
        assert result.is_error is False and result.structured_content is None
        assert len(result.content) == 1 and isinstance(result.content[0], TextContent)
        assert len(result.content[0].text) <= MAX_MCP_RESPONSE_CHARS
        assert json.loads(result.content[0].text) == cli_payload
        selected = await client.call_tool(
            "get_relevant_context", {"source": "erp", "task": "Daily Cache"}
        )
        assert selected.is_error is False
        assert len(selected.content) == 1 and isinstance(selected.content[0], TextContent)
        followups = json.loads(selected.content[0].text)["suggested_followups"]
        assert {
            "tool": "get_view",
            "arguments": {"source": "erp", "name": '"sales"."Daily Cache"'},
        } in followups
        isolated = await client.call_tool(
            "get_view", {"source": "other", "name": '"sales"."Sales.View"'}
        )
        assert isolated.is_error is True
        assert "VIEW_NOT_FOUND" in str(isolated.content)
        invalid = await client.call_tool(
            "get_view", {"source": "erp", "name": '"sales"."Sales.View"', "limit": 0}
        )
        assert invalid.is_error is True
        assert "INVALID_LIMIT" in str(invalid.content)
        with monkeypatch.context() as smaller:
            smaller.setattr(mcp_server, "MAX_MCP_RESPONSE_CHARS", 80)
            too_large = await client.call_tool(
                "get_view", {"source": "erp", "name": '"sales"."Sales.View"'}
            )
            assert too_large.is_error is True
            assert "CONTEXT_TOO_LARGE" in str(too_large.content)
