"""MCP snapshot diff exposes saved structural drift through the shared service."""

import json
import sys
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
from graphit.snapshot_diff import compare_snapshots
from graphit.snapshots import persist_snapshot
from graphit.sources import add_source
from tests.test_snapshot_diff import _changed_metadata
from tests.test_snapshots import _metadata, _project


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _seed_versions(root: Path) -> None:
    source = _project(root)
    persist_snapshot(root, source, _metadata())
    persist_snapshot(root, source, _changed_metadata())
    other = replace(source, name="other")
    add_source(root, other)
    persist_snapshot(root, other, replace(_metadata(), source_name="other"))


def _payload(result: CallToolResult) -> dict[str, object]:
    assert result.is_error is False
    assert result.structured_content is None
    assert len(result.content) == 1
    item = result.content[0]
    assert isinstance(item, TextContent)
    payload = json.loads(item.text)
    assert isinstance(payload, dict)
    return payload


def _has_error(result: CallToolResult, code: str) -> bool:
    return result.is_error is True and any(
        isinstance(item, TextContent) and code in item.text for item in result.content
    )


@pytest.mark.anyio
async def test_mcp_diff_reuses_local_service_and_preserves_bounds(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    _seed_versions(tmp_path)

    def no_source_connection(*args: object, **kwargs: object) -> None:
        raise AssertionError("MCP diff must not connect to the target database")

    monkeypatch.setattr("psycopg.connect", no_source_connection)
    async with Client(create_server(tmp_path)) as client:
        tools = await client.list_tools()
        tool = next(tool for tool in tools.tools if tool.name == "compare_snapshots")
        assert tool.annotations and tool.annotations.read_only_hint
        result = await client.call_tool(
            "compare_snapshots",
            {"source": "erp", "from_version": 1, "to_version": 2, "limit": 2},
        )
        later = await client.call_tool(
            "compare_snapshots",
            {"source": "erp", "from_version": 1, "to_version": 2, "limit": 2, "offset": 2},
        )

    payload = _payload(result)
    expected = json.loads(json.dumps(asdict(compare_snapshots(tmp_path, "erp", 1, 2, 2))))
    assert payload == expected
    assert payload["scope"] == "SCHEMA_RELATION_COLUMN_KEY_FK"
    assert payload["change_count"] == 10
    assert payload["truncated"] is True
    changes = payload["changes"]
    assert isinstance(changes, list) and len(changes) == 2
    later_payload = _payload(later)
    later_expected = json.loads(json.dumps(asdict(compare_snapshots(tmp_path, "erp", 1, 2, 2, 2))))
    assert later_payload == later_expected
    assert later_payload["offset"] == 2
    later_changes = later_payload["changes"]
    assert isinstance(later_changes, list)
    assert not {item["qualified_name"] for item in changes} & {
        item["qualified_name"] for item in later_changes
    }


@pytest.mark.anyio
async def test_mcp_diff_rejects_invalid_missing_and_cross_source_versions(tmp_path: Path) -> None:
    _seed_versions(tmp_path)
    async with Client(create_server(tmp_path)) as client:
        invalid = await client.call_tool(
            "compare_snapshots", {"source": "erp", "from_version": 2, "to_version": 1}
        )
        missing = await client.call_tool(
            "compare_snapshots", {"source": "erp", "from_version": 1, "to_version": 3}
        )
        cross_source = await client.call_tool(
            "compare_snapshots", {"source": "other", "from_version": 1, "to_version": 2}
        )
        unknown_source = await client.call_tool(
            "compare_snapshots", {"source": "unknown", "from_version": 1, "to_version": 2}
        )
        invalid_limit = await client.call_tool(
            "compare_snapshots",
            {"source": "erp", "from_version": 1, "to_version": 2, "limit": 501},
        )
        invalid_offset = await client.call_tool(
            "compare_snapshots",
            {"source": "erp", "from_version": 1, "to_version": 2, "offset": -1},
        )

    assert _has_error(invalid, "INVALID_VERSION")
    assert _has_error(missing, "SNAPSHOT_NOT_FOUND")
    assert _has_error(cross_source, "SNAPSHOT_NOT_FOUND")
    assert _has_error(unknown_source, "SOURCE_NOT_FOUND")
    assert _has_error(invalid_limit, "INVALID_LIMIT")
    assert _has_error(invalid_offset, "INVALID_OFFSET")


@pytest.mark.anyio
async def test_mcp_diff_rejects_oversized_response(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    _seed_versions(tmp_path)
    monkeypatch.setattr(mcp_server, "MAX_MCP_RESPONSE_CHARS", 40)
    async with Client(create_server(tmp_path)) as client:
        result = await client.call_tool(
            "compare_snapshots", {"source": "erp", "from_version": 1, "to_version": 2}
        )
    assert _has_error(result, "CONTEXT_TOO_LARGE")


@pytest.mark.anyio
async def test_mcp_diff_real_stdio_protocol(tmp_path: Path) -> None:
    _seed_versions(tmp_path)
    process = StdioServerParameters(
        command=sys.executable,
        args=["-m", "graphit", "mcp", "serve", "--project", str(tmp_path)],
    )
    with anyio.fail_after(15):
        async with Client(process) as client:
            result = await client.call_tool(
                "compare_snapshots",
                {"source": "erp", "from_version": 1, "to_version": 2, "limit": 1, "offset": 1},
            )
    payload = _payload(result)
    assert payload["source_name"] == "erp"
    assert payload["change_count"] == 10
    assert payload["truncated"] is True
    assert payload["offset"] == 1
