"""Exact column-level FK impact preserves composite position and provenance."""

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
from typer.testing import CliRunner

import graphit.mcp_server as mcp_server
from graphit.cli import app
from graphit.mcp_server import create_server
from graphit.queries import QueryError, column_impact
from graphit.scanners.protocol import ColumnMetadata, ForeignKeyMetadata
from graphit.snapshots import persist_snapshot
from graphit.sources import add_source
from tests.test_impact import _metadata
from tests.test_snapshots import _project


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _seed(root: Path) -> None:
    source = _project(root)
    baseline = _metadata()
    expanded = replace(
        baseline,
        columns=(
            *baseline.columns,
            ColumnMetadata("public", "parent", "Case.ID", 3, "text", True),
            ColumnMetadata("public", "parent", "region", 4, "text", False),
            ColumnMetadata("public", "child", "parent_region", 5, "text", False),
        ),
        foreign_keys=(
            *baseline.foreign_keys,
            ForeignKeyMetadata(
                "public",
                "child",
                "child_parent_region_fk",
                ("parent_id", "parent_region"),
                "public",
                "parent",
                ("id", "region"),
                True,
                True,
                False,
            ),
        ),
    )
    persist_snapshot(root, source, baseline)
    persist_snapshot(root, source, expanded)
    other = replace(source, name="other")
    add_source(root, other)
    persist_snapshot(root, other, replace(expanded, source_name="other", foreign_keys=()))
    add_source(root, replace(source, name="unscanned"))


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


def test_column_impact_matches_only_exact_target_pairs_and_preserves_position(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    _seed(tmp_path)

    def no_source_connection(*args: object, **kwargs: object) -> None:
        raise AssertionError("Column impact must read only local snapshots")

    monkeypatch.setattr("psycopg.connect", no_source_connection)
    result = column_impact(tmp_path, "erp", "public.parent.region")
    assert result.snapshot_version == 2
    assert result.scope == "DECLARED_FK_COLUMN_DIRECT"
    assert result.qualified_name == '"public"."parent"."region"'
    assert result.reference_count == 1 and not result.truncated
    pair = result.references[0]
    assert pair.foreign_key == "child_parent_region_fk"
    assert pair.source_column == '"public"."child"."parent_region"'
    assert pair.target_column == result.qualified_name
    assert (pair.pair_position, pair.pair_count) == (2, 2)
    assert (pair.origin, pair.status) == ("DATABASE", "CONFIRMED")
    assert pair.validated and not pair.inherited
    assert column_impact(tmp_path, "erp", 'public.parent."Case.ID"').reference_count == 0
    assert column_impact(tmp_path, "other", "public.parent.region").reference_count == 0
    first = column_impact(tmp_path, "erp", "public.parent.id", limit=2)
    assert first.reference_count == 4 and len(first.references) == 2 and first.truncated
    assert any(
        item.source_table == '"public"."parent"'
        for item in column_impact(tmp_path, "erp", "public.parent.id").references
    )


def test_column_impact_errors_and_work_budget(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    _seed(tmp_path)
    for reference, code in (
        ("parent.id", "AMBIGUOUS_TABLE"),
        ("public.parent.unknown", "COLUMN_NOT_FOUND"),
        ("external.vendor.id", "COLUMN_NOT_FOUND"),
        ("public.parent.Case.ID", "INVALID_COLUMN"),
        ("id", "INVALID_COLUMN"),
    ):
        with pytest.raises(QueryError) as error:
            column_impact(tmp_path, "erp", reference)
        assert error.value.code == code
    for limit in (0, 101):
        with pytest.raises(QueryError) as invalid:
            column_impact(tmp_path, "erp", "public.parent.id", limit)
        assert invalid.value.code == "INVALID_LIMIT"
    with pytest.raises(QueryError) as unscanned:
        column_impact(tmp_path, "unscanned", "public.parent.id")
    assert unscanned.value.code == "NO_SNAPSHOT"
    monkeypatch.setattr("graphit.queries.MAX_COLUMN_IMPACT_FKS", 1)
    with pytest.raises(QueryError) as budget:
        column_impact(tmp_path, "erp", "public.parent.id")
    assert budget.value.code == "IMPACT_BUDGET_EXCEEDED"


@pytest.mark.anyio
async def test_column_impact_cli_mcp_parity_and_ceiling(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    _seed(tmp_path)
    args = ["impact-column", "public.parent.region", "--source", "erp", "--project", str(tmp_path)]
    machine = CliRunner().invoke(app, [*args, "--json"])
    human = CliRunner().invoke(app, args)
    expected = json.loads(
        json.dumps(asdict(column_impact(tmp_path, "erp", "public.parent.region")))
    )
    assert machine.exit_code == 0 and json.loads(machine.stdout) == expected
    assert human.exit_code == 0
    assert "pair 2/2" in human.stdout
    bad_cli = CliRunner().invoke(
        app,
        ["impact-column", "public.parent.missing", "--source", "erp", "--project", str(tmp_path)],
    )
    assert bad_cli.exit_code == 6 and "COLUMN_NOT_FOUND" in bad_cli.stderr

    async with Client(create_server(tmp_path)) as client:
        tools = await client.list_tools()
        tool = next(item for item in tools.tools if item.name == "get_column_impact")
        assert tool.annotations and tool.annotations.read_only_hint
        result = await client.call_tool(
            "get_column_impact", {"source": "erp", "name": "public.parent.region"}
        )
        invalid = await client.call_tool(
            "get_column_impact", {"source": "erp", "name": "public.parent.region", "limit": 101}
        )
        missing = await client.call_tool(
            "get_column_impact", {"source": "erp", "name": "public.parent.missing"}
        )
    assert _payload(result) == expected
    assert _error(invalid, "INVALID_LIMIT")
    assert _error(missing, "COLUMN_NOT_FOUND")
    monkeypatch.setattr(mcp_server, "MAX_MCP_RESPONSE_CHARS", 40)
    async with Client(create_server(tmp_path)) as client:
        oversized = await client.call_tool(
            "get_column_impact", {"source": "erp", "name": "public.parent.id"}
        )
    assert _error(oversized, "CONTEXT_TOO_LARGE")


@pytest.mark.anyio
async def test_column_impact_real_stdio(tmp_path: Path) -> None:
    _seed(tmp_path)
    process = StdioServerParameters(
        command=sys.executable,
        args=["-m", "graphit", "mcp", "serve", "--project", str(tmp_path)],
    )
    with anyio.fail_after(15):
        async with Client(process) as client:
            result = await client.call_tool(
                "get_column_impact",
                {"source": "erp", "name": "public.parent.id", "limit": 2},
            )
    payload = _payload(result)
    assert payload["reference_count"] == 4
    assert payload["truncated"] is True
