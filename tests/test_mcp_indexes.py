"""MCP index context stays opt-in, local, bounded, and CLI-compatible."""

import json
import sqlite3
import sys
from contextlib import closing
from dataclasses import asdict, replace
from pathlib import Path

import anyio
import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from mcp.types import CallToolResult, TextContent
from pytest import MonkeyPatch

import graphit.mcp_server as mcp_server
from graphit.mcp_server import create_server
from graphit.queries import list_table_indexes
from graphit.scanners.protocol import ColumnMetadata, SchemaMetadata, TableMetadata
from graphit.snapshots import persist_snapshot
from tests.test_index_queries import _project
from tests.test_snapshots import _index, _metadata


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _seed(root: Path) -> None:
    source = _project(root)
    persist_snapshot(
        root,
        source,
        replace(_metadata(), indexes=(_index(), replace(_index(), name="other_lookup"))),
    )


def _seed_ambiguous(root: Path) -> None:
    source = _project(root)
    metadata = _metadata()
    persist_snapshot(
        root,
        source,
        replace(
            metadata,
            schemas=metadata.schemas + (SchemaMetadata("other"),),
            tables=metadata.tables + (TableMetadata("other", "Order", False),),
            columns=metadata.columns
            + (ColumnMetadata("other", "Order", "ID", 1, "integer", False),),
        ),
    )


def _payload(result: CallToolResult) -> dict[str, object]:
    assert result.is_error is False
    assert result.structured_content is None
    assert len(result.content) == 1
    item = result.content[0]
    assert isinstance(item, TextContent)
    value = json.loads(item.text)
    assert isinstance(value, dict)
    return value


def _error(result: CallToolResult, code: str) -> bool:
    return result.is_error is True and any(
        isinstance(item, TextContent) and code in item.text for item in result.content
    )


@pytest.mark.anyio
async def test_mcp_index_parity_pagination_and_no_source_connection(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    _seed(tmp_path)

    def no_connection(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("MCP index lookup must not connect to the source")

    monkeypatch.setattr("psycopg.connect", no_connection)
    async with Client(create_server(tmp_path)) as client:
        listing = await client.list_tools()
        tool = next(tool for tool in listing.tools if tool.name == "get_index_context")
        assert tool.annotations and tool.annotations.read_only_hint
        first = await client.call_tool(
            "get_index_context", {"source": "erp", "name": '"public"."Order"', "limit": 1}
        )
        second = await client.call_tool(
            "get_index_context",
            {"source": "erp", "name": '"public"."Order"', "limit": 1, "offset": 1},
        )
        table = await client.call_tool("get_table", {"source": "erp", "name": '"public"."Order"'})
    for offset, result in enumerate((first, second)):
        expected = asdict(list_table_indexes(tmp_path, "erp", '"public"."Order"', 1, offset))
        assert _payload(result) == json.loads(json.dumps(expected))
    assert _payload(first)["truncated"] is True
    assert _payload(second)["truncated"] is False
    assert "indexes" not in _payload(table)


@pytest.mark.anyio
async def test_mcp_index_errors_and_corrupt_store(tmp_path: Path) -> None:
    _seed(tmp_path)
    async with Client(create_server(tmp_path)) as client:
        cases = (
            ({"source": "erp", "name": "missing"}, "INDEX_TARGET_NOT_FOUND"),
            ({"source": "erp", "name": "public..bad"}, "INVALID_INDEX_TARGET"),
            ({"source": "erp", "name": '"Order"', "limit": 21}, "INVALID_LIMIT"),
            ({"source": "erp", "name": '"Order"', "offset": -1}, "INVALID_OFFSET"),
            ({"source": "erp", "name": '"Order"', "offset": 100001}, "INVALID_OFFSET"),
            ({"source": "unknown", "name": '"Order"'}, "SOURCE_NOT_FOUND"),
        )
        for arguments, code in cases:
            assert _error(await client.call_tool("get_index_context", arguments), code)
    with closing(sqlite3.connect(tmp_path / ".graphit" / "graphit.db")) as connection:
        with connection:
            connection.execute(
                "UPDATE edges SET metadata_json = '{\"position\": 9}' WHERE edge_type = 'INDEX_KEY'"
            )
    async with Client(create_server(tmp_path)) as client:
        corrupt = await client.call_tool("get_index_context", {"source": "erp", "name": '"Order"'})
    assert _error(corrupt, "STORE_READ_FAILED")


@pytest.mark.anyio
async def test_mcp_index_ambiguous_relation(tmp_path: Path) -> None:
    _seed_ambiguous(tmp_path)
    async with Client(create_server(tmp_path)) as client:
        result = await client.call_tool("get_index_context", {"source": "erp", "name": '"Order"'})
    assert _error(result, "AMBIGUOUS_INDEX_TARGET")


@pytest.mark.anyio
async def test_mcp_index_response_ceiling(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    _seed(tmp_path)
    monkeypatch.setattr(mcp_server, "MAX_MCP_RESPONSE_CHARS", 40)
    async with Client(create_server(tmp_path)) as client:
        result = await client.call_tool("get_index_context", {"source": "erp", "name": '"Order"'})
    assert _error(result, "CONTEXT_TOO_LARGE")


@pytest.mark.anyio
async def test_mcp_index_real_stdio(tmp_path: Path) -> None:
    _seed(tmp_path)
    process = StdioServerParameters(
        command=sys.executable,
        args=["-m", "graphit", "mcp", "serve", "--project", str(tmp_path)],
    )
    with anyio.fail_after(15):
        async with Client(process) as client:
            result = await client.call_tool(
                "get_index_context", {"source": "erp", "name": '"Order"', "limit": 1}
            )
    payload = _payload(result)
    assert payload["source_name"] == "erp"
    assert payload["index_count"] == 2
    assert payload["truncated"] is True
