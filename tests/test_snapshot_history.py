"""Completed local snapshot versions are discoverable through CLI and MCP."""

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
from typer.testing import CliRunner

import graphit.mcp_server as mcp_server
from graphit.cli import app
from graphit.mcp_server import create_server
from graphit.queries import QueryError
from graphit.snapshot_history import list_snapshots
from graphit.snapshots import persist_snapshot
from graphit.sources import SourceNotFound, add_source
from tests.test_snapshots import _metadata, _project


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _seed(root: Path) -> None:
    source = _project(root)
    for _ in range(3):
        persist_snapshot(root, source, _metadata())
    other = replace(source, name="other")
    add_source(root, other)
    persist_snapshot(root, other, replace(_metadata(), source_name="other"))


def _payload(result: CallToolResult) -> dict[str, object]:
    assert result.is_error is False
    assert result.structured_content is None
    assert len(result.content) == 1
    content = result.content[0]
    assert isinstance(content, TextContent)
    payload = json.loads(content.text)
    assert isinstance(payload, dict)
    return payload


def _error(result: CallToolResult, code: str) -> bool:
    return result.is_error is True and any(
        isinstance(item, TextContent) and code in item.text for item in result.content
    )


def test_snapshot_history_is_newest_first_bounded_and_source_scoped(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    _seed(tmp_path)

    def no_source_connection(*args: object, **kwargs: object) -> None:
        raise AssertionError("Snapshot listing must not connect to PostgreSQL")

    monkeypatch.setattr("psycopg.connect", no_source_connection)
    history = list_snapshots(tmp_path, "erp", 2)
    assert history.source_name == "erp"
    assert history.snapshot_count == 3
    assert history.truncated is True
    assert [item.version for item in history.snapshots] == [3, 2]
    assert all(item.status == "COMPLETED" and item.completed_at for item in history.snapshots)
    assert [item.version for item in list_snapshots(tmp_path, "other").snapshots] == [1]
    assert [item.version for item in list_snapshots(tmp_path, "erp").snapshots] == [3, 2, 1]


def test_snapshot_history_empty_invalid_source_limit_and_noncompleted(tmp_path: Path) -> None:
    source = _project(tmp_path)
    empty = list_snapshots(tmp_path, source.name)
    assert empty.snapshot_count == 0 and empty.snapshots == () and not empty.truncated
    with pytest.raises(SourceNotFound):
        list_snapshots(tmp_path, "missing")
    for invalid in (0, 101):
        with pytest.raises(QueryError) as error:
            list_snapshots(tmp_path, source.name, invalid)
        assert error.value.code == "INVALID_LIMIT"

    persist_snapshot(tmp_path, source, _metadata())
    persist_snapshot(tmp_path, source, _metadata())
    with closing(sqlite3.connect(tmp_path / ".graphit" / "graphit.db")) as connection:
        with connection:
            connection.execute(
                """UPDATE scan_runs SET status = 'FAILED'
                WHERE id = (SELECT scan_run_id FROM snapshots WHERE version = 2)"""
            )
    history = list_snapshots(tmp_path, "erp")
    assert history.snapshot_count == 1
    assert [item.version for item in history.snapshots] == [1]


@pytest.mark.anyio
async def test_snapshot_history_cli_mcp_parity_errors_and_ceiling(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    _seed(tmp_path)
    command = ["snapshots", "--source", "erp", "--limit", "2", "--json", "--project", str(tmp_path)]
    cli = CliRunner().invoke(app, command)
    assert cli.exit_code == 0
    expected = asdict(list_snapshots(tmp_path, "erp", 2))
    assert json.loads(cli.stdout) == json.loads(json.dumps(expected))

    human = CliRunner().invoke(
        app, ["snapshots", "--source", "erp", "--limit", "1", "--project", str(tmp_path)]
    )
    assert human.exit_code == 0
    assert "3 completed snapshots" in human.stdout
    assert "More snapshots exist" in human.stdout

    async with Client(create_server(tmp_path)) as client:
        tools = await client.list_tools()
        tool = next(item for item in tools.tools if item.name == "list_snapshots")
        assert tool.annotations and tool.annotations.read_only_hint
        result = await client.call_tool("list_snapshots", {"source": "erp", "limit": 2})
        invalid = await client.call_tool("list_snapshots", {"source": "erp", "limit": 101})
        unknown = await client.call_tool("list_snapshots", {"source": "missing"})
    assert _payload(result) == json.loads(cli.stdout)
    assert _error(invalid, "INVALID_LIMIT")
    assert _error(unknown, "SOURCE_NOT_FOUND")

    monkeypatch.setattr(mcp_server, "MAX_MCP_RESPONSE_CHARS", 40)
    async with Client(create_server(tmp_path)) as client:
        oversized = await client.call_tool("list_snapshots", {"source": "erp"})
    assert _error(oversized, "CONTEXT_TOO_LARGE")


@pytest.mark.anyio
async def test_snapshot_history_real_stdio(tmp_path: Path) -> None:
    _seed(tmp_path)
    process = StdioServerParameters(
        command=sys.executable,
        args=["-m", "graphit", "mcp", "serve", "--project", str(tmp_path)],
    )
    with anyio.fail_after(15):
        async with Client(process) as client:
            result = await client.call_tool("list_snapshots", {"source": "erp", "limit": 1})
    payload = _payload(result)
    assert payload["snapshot_count"] == 3
    assert payload["truncated"] is True
    snapshots = payload["snapshots"]
    assert isinstance(snapshots, list) and snapshots[0]["version"] == 3
