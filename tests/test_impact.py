"""Direct structural impact stays bounded and clearly FK-only."""

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
from graphit.queries import QueryError, table_impact, transitive_table_impact
from graphit.scanners.protocol import (
    ColumnMetadata,
    ForeignKeyMetadata,
    MetadataSnapshot,
    SchemaMetadata,
    TableMetadata,
)
from graphit.snapshots import persist_snapshot
from graphit.sources import add_source
from tests.test_snapshots import _project


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _metadata() -> MetadataSnapshot:
    return MetadataSnapshot(
        source_name="erp",
        schemas=(SchemaMetadata("public"), SchemaMetadata("audit")),
        tables=tuple(
            TableMetadata(schema, table, False)
            for schema, table in (
                ("public", "parent"),
                ("public", "child"),
                ("public", "grandchild"),
                ("audit", "parent"),
            )
        ),
        columns=(
            ColumnMetadata("public", "parent", "id", 1, "integer", False),
            ColumnMetadata("public", "parent", "parent_id", 2, "integer", True),
            ColumnMetadata("public", "child", "id", 1, "integer", False),
            ColumnMetadata("public", "child", "parent_id", 2, "integer", False),
            ColumnMetadata("public", "child", "alt_parent_id", 3, "integer", True),
            ColumnMetadata("public", "child", "vendor_id", 4, "integer", True),
            ColumnMetadata("public", "grandchild", "child_id", 1, "integer", False),
            ColumnMetadata("audit", "parent", "id", 1, "integer", False),
        ),
        foreign_keys=(
            ForeignKeyMetadata(
                "public",
                "child",
                "child_parent_fk",
                ("parent_id",),
                "public",
                "parent",
                ("id",),
                True,
                True,
                False,
            ),
            ForeignKeyMetadata(
                "public",
                "child",
                "child_alt_parent_fk",
                ("alt_parent_id",),
                "public",
                "parent",
                ("id",),
                True,
                True,
                False,
            ),
            ForeignKeyMetadata(
                "public",
                "parent",
                "parent_self_fk",
                ("parent_id",),
                "public",
                "parent",
                ("id",),
                True,
                True,
                False,
            ),
            ForeignKeyMetadata(
                "public",
                "grandchild",
                "grandchild_child_fk",
                ("child_id",),
                "public",
                "child",
                ("id",),
                True,
                True,
                False,
            ),
            ForeignKeyMetadata(
                "public",
                "child",
                "child_vendor_fk",
                ("vendor_id",),
                "external",
                "vendor",
                ("id",),
                False,
                True,
                False,
            ),
        ),
    )


def _seed(root: Path) -> None:
    source = _project(root)
    persist_snapshot(root, source, _metadata())
    persist_snapshot(root, source, _metadata())
    other = replace(source, name="other")
    add_source(root, other)
    persist_snapshot(root, other, replace(_metadata(), source_name="other", foreign_keys=()))
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


@pytest.mark.anyio
async def test_direct_fk_impact_counts_unique_tables_and_excludes_transitive_links(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    _seed(tmp_path)
    with closing(sqlite3.connect(tmp_path / ".graphit" / "graphit.db")) as connection:
        with connection:
            snapshot_id = connection.execute(
                """SELECT s.id FROM snapshots AS s JOIN sources AS src ON src.id = s.source_id
                WHERE src.name = 'erp' AND s.version = 2"""
            ).fetchone()[0]
            source_id = connection.execute(
                "SELECT id FROM objects WHERE snapshot_id = ? AND qualified_name = ?",
                (snapshot_id, '"public"."grandchild"'),
            ).fetchone()[0]
            target_id = connection.execute(
                "SELECT id FROM objects WHERE snapshot_id = ? AND qualified_name = ?",
                (snapshot_id, '"public"."parent"'),
            ).fetchone()[0]
            connection.execute(
                """INSERT INTO edges
                (snapshot_id, source_object_id, target_object_id, edge_type, origin,
                 status, confidence, metadata_json)
                VALUES (?, ?, ?, 'REFERENCES', 'INFERRED', 'APPROVED', 0.8, '{}')""",
                (snapshot_id, source_id, target_id),
            )

    def no_source_connection(*args: object, **kwargs: object) -> None:
        raise AssertionError("Impact context must not connect to PostgreSQL")

    monkeypatch.setattr("psycopg.connect", no_source_connection)
    impact = table_impact(tmp_path, "erp", "public.parent")
    assert impact.snapshot_version == 2
    assert impact.scope == "DECLARED_FK_DIRECT"
    assert impact.foreign_key_count == 3
    assert impact.dependent_table_count == 2
    assert not impact.truncated
    assert {item.name for item in impact.dependents} == {
        "child_parent_fk",
        "child_alt_parent_fk",
        "parent_self_fk",
    }
    assert all(
        item.origin == "DATABASE" and item.status == "CONFIRMED" for item in impact.dependents
    )
    assert all(item.target_table == '"public"."parent"' for item in impact.dependents)
    assert all(item.source_table != '"public"."grandchild"' for item in impact.dependents)
    transitive = transitive_table_impact(tmp_path, "erp", "public.parent")
    assert [(item.dependent_table, item.hops) for item in transitive.dependents] == [
        ('"public"."child"', 1),
        ('"public"."grandchild"', 2),
    ]
    assert table_impact(tmp_path, "erp", "public.parent", limit=1).truncated
    assert table_impact(tmp_path, "erp", "public.child").foreign_key_count == 1
    external = table_impact(tmp_path, "erp", "external.vendor")
    assert external.in_scope is False and external.foreign_key_count == 1
    assert table_impact(tmp_path, "other", "public.parent").foreign_key_count == 0


def test_impact_exact_resolution_and_failures(tmp_path: Path) -> None:
    _seed(tmp_path)
    for name, code in (("parent", "AMBIGUOUS_TABLE"), ("missing", "TABLE_NOT_FOUND")):
        with pytest.raises(QueryError) as error:
            table_impact(tmp_path, "erp", name)
        assert error.value.code == code
    for limit in (0, 101):
        with pytest.raises(QueryError) as error:
            table_impact(tmp_path, "erp", "public.parent", limit)
        assert error.value.code == "INVALID_LIMIT"
    with pytest.raises(QueryError) as unscanned:
        table_impact(tmp_path, "unscanned", "public.parent")
    assert unscanned.value.code == "NO_SNAPSHOT"


def test_transitive_impact_follows_shortest_declared_fk_paths(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    _seed(tmp_path)

    def no_source_connection(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Transitive impact must stay local")

    monkeypatch.setattr("psycopg.connect", no_source_connection)
    impact = transitive_table_impact(tmp_path, "erp", "public.parent")
    assert impact.snapshot_version == 2
    assert impact.scope == "DECLARED_FK_TRANSITIVE_TABLE"
    assert impact.dependent_table_count == 2
    assert not impact.truncated
    assert [(item.dependent_table, item.hops) for item in impact.dependents] == [
        ('"public"."child"', 1),
        ('"public"."grandchild"', 2),
    ]
    assert [step.foreign_key for step in impact.dependents[1].steps] == [
        "child_parent_fk",
        "grandchild_child_fk",
    ]
    assert all(
        step.direction == "REVERSE" and step.origin == "DATABASE" and step.status == "CONFIRMED"
        for item in impact.dependents
        for step in item.steps
    )
    assert (
        transitive_table_impact(tmp_path, "erp", "public.parent", max_hops=1).dependent_table_count
        == 1
    )
    page = transitive_table_impact(tmp_path, "erp", "public.parent", limit=1)
    assert page.truncated and page.dependent_table_count == 2
    assert [item.dependent_table for item in page.dependents] == ['"public"."child"']
    external = transitive_table_impact(tmp_path, "erp", "external.vendor")
    assert external.in_scope is False and external.dependent_table_count == 2
    assert transitive_table_impact(tmp_path, "other", "public.parent").dependents == ()


def test_transitive_impact_rejects_invalid_bounds_and_budget(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    _seed(tmp_path)
    for hops, limit, code in (
        (-1, 20, "INVALID_HOPS"),
        (9, 20, "INVALID_HOPS"),
        (1, 0, "INVALID_LIMIT"),
        (1, 101, "INVALID_LIMIT"),
    ):
        with pytest.raises(QueryError) as raised:
            transitive_table_impact(tmp_path, "erp", "public.parent", hops, limit)
        assert raised.value.code == code
    with pytest.raises(QueryError) as ambiguous:
        transitive_table_impact(tmp_path, "erp", "parent")
    assert ambiguous.value.code == "AMBIGUOUS_TABLE"
    monkeypatch.setattr("graphit.queries.MAX_PATH_EDGES", 1)
    with pytest.raises(QueryError) as exceeded:
        transitive_table_impact(tmp_path, "erp", "public.parent")
    assert exceeded.value.code == "IMPACT_BUDGET_EXCEEDED"


@pytest.mark.anyio
async def test_impact_cli_mcp_parity_and_response_ceiling(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    _seed(tmp_path)
    cli = CliRunner().invoke(
        app,
        [
            "impact",
            "public.parent",
            "--source",
            "erp",
            "--limit",
            "1",
            "--json",
            "--project",
            str(tmp_path),
        ],
    )
    assert cli.exit_code == 0
    expected = json.loads(json.dumps(asdict(table_impact(tmp_path, "erp", "public.parent", 1))))
    assert json.loads(cli.stdout) == expected
    human = CliRunner().invoke(
        app, ["impact", "public.parent", "--source", "erp", "--project", str(tmp_path)]
    )
    assert human.exit_code == 0
    assert "2 tables, 3 FKs" in human.stdout
    assert "Structural FK impact only" in human.stdout
    ambiguous_cli = CliRunner().invoke(
        app, ["impact", "parent", "--source", "erp", "--project", str(tmp_path)]
    )
    assert ambiguous_cli.exit_code == 6
    assert "AMBIGUOUS_TABLE" in ambiguous_cli.stderr

    async with Client(create_server(tmp_path)) as client:
        tools = await client.list_tools()
        tool = next(item for item in tools.tools if item.name == "get_impact_context")
        assert tool.annotations and tool.annotations.read_only_hint
        result = await client.call_tool(
            "get_impact_context", {"source": "erp", "name": "public.parent", "limit": 1}
        )
        invalid = await client.call_tool(
            "get_impact_context", {"source": "erp", "name": "public.parent", "limit": 101}
        )
        ambiguous = await client.call_tool(
            "get_impact_context", {"source": "erp", "name": "parent"}
        )
        unscanned = await client.call_tool(
            "get_impact_context", {"source": "unscanned", "name": "public.parent"}
        )
    assert _payload(result) == expected
    assert invalid.is_error and any(
        isinstance(item, TextContent) and "INVALID_LIMIT" in item.text for item in invalid.content
    )
    assert ambiguous.is_error and any(
        isinstance(item, TextContent) and "AMBIGUOUS_TABLE" in item.text
        for item in ambiguous.content
    )
    assert unscanned.is_error and any(
        isinstance(item, TextContent) and "NO_SNAPSHOT" in item.text for item in unscanned.content
    )
    monkeypatch.setattr(mcp_server, "MAX_MCP_RESPONSE_CHARS", 40)
    async with Client(create_server(tmp_path)) as client:
        oversized = await client.call_tool(
            "get_impact_context", {"source": "erp", "name": "public.parent"}
        )
    assert oversized.is_error and any(
        isinstance(item, TextContent) and "CONTEXT_TOO_LARGE" in item.text
        for item in oversized.content
    )


@pytest.mark.anyio
async def test_impact_real_stdio(tmp_path: Path) -> None:
    _seed(tmp_path)
    process = StdioServerParameters(
        command=sys.executable,
        args=["-m", "graphit", "mcp", "serve", "--project", str(tmp_path)],
    )
    with anyio.fail_after(15):
        async with Client(process) as client:
            result = await client.call_tool(
                "get_impact_context", {"source": "erp", "name": "public.parent", "limit": 2}
            )
    payload = _payload(result)
    assert payload["foreign_key_count"] == 3
    assert payload["dependent_table_count"] == 2
    assert payload["truncated"] is True


@pytest.mark.anyio
async def test_transitive_impact_cli_mcp_parity_and_limits(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    _seed(tmp_path)
    args = ["impact-tree", "public.parent", "--source", "erp", "--project", str(tmp_path)]
    json_result = CliRunner().invoke(app, [*args, "--limit", "1", "--json"])
    human = CliRunner().invoke(app, args)
    invalid_cli = CliRunner().invoke(app, [*args, "--max-hops", "9"])
    assert json_result.exit_code == 0 and human.exit_code == 0
    assert invalid_cli.exit_code == 2 and "INVALID_HOPS" in invalid_cli.stderr
    expected = json.loads(
        json.dumps(asdict(transitive_table_impact(tmp_path, "erp", "public.parent", limit=1)))
    )
    assert json.loads(json_result.stdout) == expected
    assert "2 tables" in human.stdout
    assert "Structural FK reachability only" in human.stdout
    async with Client(create_server(tmp_path)) as client:
        tools = await client.list_tools()
        tool = next(item for item in tools.tools if item.name == "get_transitive_impact")
        assert tool.annotations and tool.annotations.read_only_hint
        result = await client.call_tool(
            "get_transitive_impact", {"source": "erp", "name": "public.parent", "limit": 1}
        )
        invalid = await client.call_tool(
            "get_transitive_impact", {"source": "erp", "name": "public.parent", "limit": 21}
        )
    assert _payload(result) == expected
    assert invalid.is_error and any(
        isinstance(item, TextContent) and "INVALID_LIMIT" in item.text for item in invalid.content
    )
    monkeypatch.setattr(mcp_server, "MAX_MCP_RESPONSE_CHARS", 40)
    async with Client(create_server(tmp_path)) as client:
        oversized = await client.call_tool(
            "get_transitive_impact", {"source": "erp", "name": "public.parent"}
        )
    assert oversized.is_error and any(
        isinstance(item, TextContent) and "CONTEXT_TOO_LARGE" in item.text
        for item in oversized.content
    )


@pytest.mark.anyio
async def test_transitive_impact_real_stdio(tmp_path: Path) -> None:
    _seed(tmp_path)
    process = StdioServerParameters(
        command=sys.executable,
        args=["-m", "graphit", "mcp", "serve", "--project", str(tmp_path)],
    )
    with anyio.fail_after(15):
        async with Client(process) as client:
            result = await client.call_tool(
                "get_transitive_impact", {"source": "erp", "name": "public.parent"}
            )
    payload = _payload(result)
    assert payload["dependent_table_count"] == 2
    assert payload["scope"] == "DECLARED_FK_TRANSITIVE_TABLE"
