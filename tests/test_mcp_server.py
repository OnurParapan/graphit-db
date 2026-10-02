"""MCP tools expose bounded saved context without querying source databases."""

import json
import sys
from dataclasses import asdict
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
from graphit.graph_export import table_graph
from graphit.mcp_server import create_server
from graphit.project import initialize_project
from graphit.review import approve_candidate, revoke_approval
from graphit.scanners.protocol import (
    ColumnMetadata,
    ForeignKeyMetadata,
    MetadataSnapshot,
    SchemaMetadata,
    TableMetadata,
)
from graphit.snapshots import persist_snapshot
from graphit.sources import SourceConfig, add_source, show_source
from tests.test_review import SOURCE_COLUMN, TARGET_COLUMN, _save, _source


def _json_content(result: CallToolResult) -> dict[str, object]:
    content = result.content
    assert len(content) == 1
    assert isinstance(content[0], TextContent)
    payload = json.loads(content[0].text)
    assert isinstance(payload, dict)
    return payload


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _seed_project(root: Path, *, snapshot: bool = True) -> None:
    initialize_project(root)
    source = SourceConfig(
        name="erp",
        host="localhost",
        port=5432,
        database_name="erp",
        username="reader",
        credential_env="ERP_PASSWORD",
        schemas=("public",),
    )
    add_source(root, source)
    if snapshot:
        persist_snapshot(
            root,
            source,
            MetadataSnapshot(
                source_name="erp",
                schemas=(SchemaMetadata("public"),),
                tables=(TableMetadata("public", "Customer", False),),
                columns=(ColumnMetadata("public", "Customer", "id", 1, "integer", False),),
                keys=(),
                foreign_keys=(),
            ),
        )


def _seed_relationship_project(root: Path) -> None:
    _seed_project(root, snapshot=False)
    persist_snapshot(
        root,
        show_source(root, "erp"),
        MetadataSnapshot(
            source_name="erp",
            schemas=(SchemaMetadata("public"),),
            tables=tuple(
                TableMetadata("public", name, False)
                for name in ("customer", "orders", "payment", "isolated")
            ),
            columns=(
                ColumnMetadata("public", "customer", "id", 1, "integer", False),
                ColumnMetadata("public", "orders", "id", 1, "integer", False),
                ColumnMetadata("public", "orders", "customer_id", 2, "integer", False),
                ColumnMetadata("public", "payment", "order_id", 1, "integer", False),
                ColumnMetadata("public", "isolated", "id", 1, "integer", False),
            ),
            keys=(),
            foreign_keys=(
                ForeignKeyMetadata(
                    "public",
                    "orders",
                    "orders_customer_fk",
                    ("customer_id",),
                    "public",
                    "customer",
                    ("id",),
                    True,
                    True,
                    False,
                ),
                ForeignKeyMetadata(
                    "public",
                    "payment",
                    "payment_order_fk",
                    ("order_id",),
                    "public",
                    "orders",
                    ("id",),
                    True,
                    True,
                    False,
                ),
                ForeignKeyMetadata(
                    "public",
                    "orders",
                    "orders_legacy_fk",
                    ("customer_id",),
                    "external",
                    "legacy",
                    ("id",),
                    False,
                    True,
                    False,
                ),
            ),
        ),
    )


@pytest.mark.anyio
async def test_mcp_tools_read_local_snapshot(tmp_path: Path) -> None:
    _seed_project(tmp_path)

    async with Client(create_server(tmp_path)) as client:
        tools = await client.list_tools()
        assert {tool.name for tool in tools.tools} == {
            "database_overview",
            "get_relevant_context",
            "search_objects",
            "get_table",
            "get_view",
            "get_relationships",
            "get_impact_context",
            "get_transitive_impact",
            "get_column_impact",
            "get_index_context",
            "get_graph_context",
            "find_path",
            "compare_snapshots",
            "list_snapshots",
        }
        assert all(tool.annotations and tool.annotations.read_only_hint for tool in tools.tools)
        relevant_tool = next(tool for tool in tools.tools if tool.name == "get_relevant_context")
        assert relevant_tool.description is not None
        assert "Inspect suggested tables in order" in relevant_tool.description

        found = await client.call_tool(
            "search_objects", {"source": "erp", "query": "customer", "limit": 1}
        )
        assert found.is_error is False
        assert found.structured_content is None
        assert _json_content(found)["snapshot_version"] == 1
        assert _json_content(found)["truncated"] is True

        table = await client.call_tool(
            "get_table", {"source": "erp", "name": '"public"."Customer"'}
        )
        assert table.is_error is False
        assert table.structured_content is None
        payload = _json_content(table)
        assert payload["qualified_name"] == '"public"."Customer"'
        columns = payload["columns"]
        assert isinstance(columns, list) and len(columns) == 1


@pytest.mark.anyio
async def test_mcp_errors_are_safe_and_explicit(tmp_path: Path) -> None:
    _seed_project(tmp_path, snapshot=False)

    async with Client(create_server(tmp_path)) as client:
        overview = await client.call_tool("database_overview", {"source": "erp"})
        assert overview.is_error is True
        assert any(
            isinstance(item, TextContent) and "NO_SNAPSHOT" in item.text
            for item in overview.content
        )

        no_snapshot = await client.call_tool(
            "search_objects", {"source": "erp", "query": "customer"}
        )
        assert no_snapshot.is_error is True
        assert any(
            isinstance(item, TextContent) and "NO_SNAPSHOT" in item.text
            for item in no_snapshot.content
        )
        no_context = await client.call_tool(
            "get_relevant_context", {"source": "erp", "task": "customer"}
        )
        assert no_context.is_error is True
        assert any(
            isinstance(item, TextContent) and "NO_SNAPSHOT" in item.text
            for item in no_context.content
        )

        invalid = await client.call_tool(
            "get_table", {"source": "erp", "name": "public.Customer", "limit": 51}
        )
        assert invalid.is_error is True
        assert any(
            isinstance(item, TextContent) and "INVALID_LIMIT" in item.text
            for item in invalid.content
        )


@pytest.mark.anyio
async def test_mcp_rejects_oversized_context(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    _seed_project(tmp_path)
    monkeypatch.setattr(mcp_server, "MAX_MCP_RESPONSE_CHARS", 40)

    async with Client(create_server(tmp_path)) as client:
        result = await client.call_tool("search_objects", {"source": "erp", "query": "customer"})

    assert result.is_error is True
    assert any(
        isinstance(item, TextContent) and "CONTEXT_TOO_LARGE" in item.text
        for item in result.content
    )


@pytest.mark.anyio
async def test_mcp_relationships_preserve_directions_and_scope(tmp_path: Path) -> None:
    _seed_relationship_project(tmp_path)

    async with Client(create_server(tmp_path)) as client:
        result = await client.call_tool(
            "get_relationships", {"source": "erp", "name": "public.orders", "limit": 5}
        )
        bounded = await client.call_tool(
            "get_relationships", {"source": "erp", "name": "public.orders", "limit": 1}
        )
        external = await client.call_tool(
            "get_relationships", {"source": "erp", "name": "external.legacy"}
        )

    assert result.is_error is False
    assert result.structured_content is None
    payload = _json_content(result)
    assert payload["qualified_name"] == '"public"."orders"'
    incoming = payload["incoming"]
    outgoing = payload["outgoing"]
    assert isinstance(incoming, list) and isinstance(outgoing, list)
    assert [item["name"] for item in incoming] == ["payment_order_fk"]
    assert {item["name"] for item in outgoing} == {"orders_customer_fk", "orders_legacy_fk"}
    assert all(item["origin"] == "DATABASE" and item["status"] == "CONFIRMED" for item in outgoing)
    assert any(item["target_in_scope"] is False for item in outgoing)
    assert _json_content(bounded)["outgoing_truncated"] is True
    assert _json_content(external)["in_scope"] is False


@pytest.mark.anyio
async def test_mcp_graph_context_separates_reviewed_links_from_fk_facts(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    initialize_project(tmp_path)
    source = _source(tmp_path)
    _save(source, tmp_path)

    def no_source_connection(*args: object, **kwargs: object) -> None:
        raise AssertionError("MCP graph context must stay local")

    monkeypatch.setattr("psycopg.connect", no_source_connection)
    async with Client(create_server(tmp_path)) as client:
        pending = await client.call_tool(
            "get_graph_context", {"source": "erp", "name": "public.invoice"}
        )
        approve_candidate(tmp_path, "erp", SOURCE_COLUMN, TARGET_COLUMN, 1)
        approved = await client.call_tool(
            "get_graph_context", {"source": "erp", "name": "public.invoice"}
        )
        full_projection = table_graph(tmp_path, "erp", "public.invoice", 8, 8)
        fact_only = await client.call_tool(
            "get_relationships", {"source": "erp", "name": "public.invoice"}
        )
        revoke_approval(tmp_path, "erp", SOURCE_COLUMN, TARGET_COLUMN, 1)
        revoked = await client.call_tool(
            "get_graph_context", {"source": "erp", "name": "public.invoice"}
        )
    assert pending.is_error is False and _json_content(pending)["links"] == []
    assert approved.is_error is False and approved.structured_content is None
    payload = _json_content(approved)
    assert (payload["source_name"], payload["snapshot_version"]) == ("erp", 1)
    assert payload["focus_table"] == '"public"."invoice"'
    links = payload["links"]
    assert isinstance(links, list) and len(links) == 1
    assert (links[0]["origin"], links[0]["status"]) == ("INFERRED", "APPROVED")
    assert links[0]["metadata_score"] == 0.8
    assert links[0]["column_pairs"] == [[SOURCE_COLUMN, TARGET_COLUMN]]
    assert len(links[0]["evidence"]) == 4
    assert "foreign_key" not in links[0]
    assert "details" not in str(links[0])
    assert isinstance(approved.content[0], TextContent)
    full_json = json.dumps(asdict(full_projection), ensure_ascii=False, separators=(",", ":"))
    assert len(approved.content[0].text) < len(full_json)
    assert _json_content(fact_only)["outgoing"] == []
    assert revoked.is_error is False and _json_content(revoked)["links"] == []


@pytest.mark.anyio
async def test_mcp_graph_context_bounds_and_errors(tmp_path: Path) -> None:
    _seed_relationship_project(tmp_path)
    async with Client(create_server(tmp_path)) as client:
        graph = await client.call_tool(
            "get_graph_context", {"source": "erp", "name": "public.orders", "limit": 1}
        )
        invalid = await client.call_tool(
            "get_graph_context", {"source": "erp", "name": "public.orders", "limit": 0}
        )
        missing = await client.call_tool(
            "get_graph_context", {"source": "erp", "name": "public.unknown"}
        )
    assert graph.is_error is False
    payload = _json_content(graph)
    assert payload["truncated"] is True and payload["fk_truncated"] is True
    assert payload["approved_truncated"] is False
    links = payload["links"]
    assert isinstance(links, list) and len(links) == 2
    assert all(link["origin"] == "DATABASE" and link["status"] == "CONFIRMED" for link in links)
    assert all("foreign_key" in link and "metadata_score" not in link for link in links)
    for result, code in ((invalid, "INVALID_LIMIT"), (missing, "TABLE_NOT_FOUND")):
        assert result.is_error is True
        assert any(isinstance(item, TextContent) and code in item.text for item in result.content)


@pytest.mark.anyio
async def test_mcp_overview_is_compact_and_uses_latest_local_snapshot(tmp_path: Path) -> None:
    _seed_relationship_project(tmp_path)

    async with Client(create_server(tmp_path)) as client:
        overview = await client.call_tool("database_overview", {"source": "erp", "limit": 1})
        invalid = await client.call_tool("database_overview", {"source": "erp", "limit": 51})

    assert overview.is_error is False and overview.structured_content is None
    payload = _json_content(overview)
    assert payload["snapshot_version"] == 1
    assert (payload["schema_count"], payload["external_schema_count"]) == (1, 1)
    assert (payload["table_count"], payload["external_table_count"]) == (4, 1)
    assert payload["foreign_key_count"] == 3
    assert payload["schemas"] == ['"public"']
    assert payload["schemas_truncated"] is False
    entries = payload["entry_tables"]
    assert isinstance(entries, list) and len(entries) == 1
    assert entries[0]["qualified_name"] == '"public"."orders"'
    assert entries[0]["declared_fk_count"] == 3
    assert payload["entry_tables_truncated"] is True
    assert invalid.is_error is True
    assert any(
        isinstance(item, TextContent) and "INVALID_LIMIT" in item.text for item in invalid.content
    )


@pytest.mark.anyio
async def test_mcp_relevant_context_is_explainable_and_bounded(tmp_path: Path) -> None:
    _seed_relationship_project(tmp_path)

    async with Client(create_server(tmp_path)) as client:
        context = await client.call_tool(
            "get_relevant_context",
            {"source": "erp", "task": "change payment order handling", "max_objects": 3},
        )
        compact = await client.call_tool(
            "get_relevant_context",
            {"source": "erp", "task": "payment order", "max_chars": 500},
        )
        one_followup = await client.call_tool(
            "get_relevant_context",
            {
                "source": "erp",
                "task": "change payment order handling",
                "max_objects": 3,
                "max_followups": 1,
            },
        )
        invalid = await client.call_tool(
            "get_relevant_context",
            {"source": "erp", "task": "payment", "max_objects": 21},
        )
        invalid_followups = await client.call_tool(
            "get_relevant_context", {"source": "erp", "task": "payment", "max_followups": 4}
        )

    assert context.is_error is False and context.structured_content is None
    payload = _json_content(context)
    assert payload["snapshot_version"] == 1
    assert payload["task_terms"] == ["payment", "order", "handling"]
    assert payload["unmatched_terms"] == ["handling"]
    objects = payload["objects"]
    assert isinstance(objects, list) and 0 < len(objects) <= 3
    assert all(item["reason"] == "MATCHED_TASK_TERMS" for item in objects)
    for item in objects:
        if item["kind"] == "COLUMN":
            assert item["data_type"] == "integer"
            assert item["nullable"] is False
        else:
            assert "data_type" not in item
            assert "nullable" not in item
            assert "primary_key" not in item
            assert "unique_value" not in item
    assert compact.is_error is False
    assert one_followup.is_error is False
    original_followups = payload["suggested_followups"]
    assert isinstance(original_followups, list)
    assert _json_content(one_followup)["suggested_followups"] == original_followups[:1]
    assert len(compact.content) == 1 and isinstance(compact.content[0], TextContent)
    assert len(compact.content[0].text) <= 500
    assert invalid.is_error is True
    assert invalid_followups.is_error is True
    assert any(
        isinstance(item, TextContent) and "INVALID_BUDGET" in item.text
        for item in invalid_followups.content
    )
    assert any(
        isinstance(item, TextContent) and "INVALID_BUDGET" in item.text for item in invalid.content
    )


@pytest.mark.anyio
async def test_mcp_path_preserves_direction_and_reports_failures(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    _seed_relationship_project(tmp_path)

    async with Client(create_server(tmp_path)) as client:
        forward = await client.call_tool(
            "find_path", {"source": "erp", "from_table": "payment", "to_table": "customer"}
        )
        reverse = await client.call_tool(
            "find_path", {"source": "erp", "from_table": "customer", "to_table": "payment"}
        )
        missing = await client.call_tool(
            "find_path", {"source": "erp", "from_table": "payment", "to_table": "isolated"}
        )
        shallow = await client.call_tool(
            "find_path",
            {"source": "erp", "from_table": "payment", "to_table": "customer", "max_hops": 1},
        )
        invalid = await client.call_tool(
            "find_path",
            {"source": "erp", "from_table": "payment", "to_table": "customer", "max_hops": 9},
        )
        monkeypatch.setattr("graphit.queries.MAX_PATH_NODES", 2)
        budget = await client.call_tool(
            "find_path", {"source": "erp", "from_table": "payment", "to_table": "customer"}
        )

    assert forward.is_error is False and forward.structured_content is None
    forward_steps = _json_content(forward)["steps"]
    reverse_steps = _json_content(reverse)["steps"]
    assert isinstance(forward_steps, list) and isinstance(reverse_steps, list)
    assert [step["foreign_key"] for step in forward_steps] == [
        "payment_order_fk",
        "orders_customer_fk",
    ]
    assert [step["direction"] for step in reverse_steps] == ["REVERSE", "REVERSE"]
    for result, code in (
        (missing, "PATH_NOT_FOUND"),
        (shallow, "PATH_NOT_FOUND"),
        (invalid, "INVALID_HOPS"),
        (budget, "PATH_BUDGET_EXCEEDED"),
    ):
        assert result.is_error is True
        assert any(isinstance(item, TextContent) and code in item.text for item in result.content)


@pytest.mark.anyio
async def test_mcp_graph_tools_reject_oversized_context(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    _seed_relationship_project(tmp_path)
    monkeypatch.setattr(mcp_server, "MAX_MCP_RESPONSE_CHARS", 40)

    async with Client(create_server(tmp_path)) as client:
        overview = await client.call_tool("database_overview", {"source": "erp"})
        relationships = await client.call_tool(
            "get_relationships", {"source": "erp", "name": "public.orders"}
        )
        path = await client.call_tool(
            "find_path", {"source": "erp", "from_table": "payment", "to_table": "customer"}
        )
        graph = await client.call_tool(
            "get_graph_context", {"source": "erp", "name": "public.orders"}
        )

    for result in (overview, relationships, path, graph):
        assert result.is_error is True
        assert any(
            isinstance(item, TextContent) and "CONTEXT_TOO_LARGE" in item.text
            for item in result.content
        )


def test_mcp_serve_missing_project_keeps_stdout_clean(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["mcp", "serve", "--project", str(tmp_path)])

    assert result.exit_code == 3
    assert result.stdout == ""
    assert "PROJECT_NOT_INITIALIZED" in result.stderr


@pytest.mark.anyio
async def test_mcp_cli_uses_real_stdio_protocol(tmp_path: Path) -> None:
    _seed_project(tmp_path)
    process = StdioServerParameters(
        command=sys.executable,
        args=["-m", "graphit", "mcp", "serve", "--project", str(tmp_path)],
    )

    with anyio.fail_after(15):
        async with Client(process) as client:
            tools = await client.list_tools()
            assert {tool.name for tool in tools.tools} == {
                "database_overview",
                "get_relevant_context",
                "search_objects",
                "get_table",
                "get_view",
                "get_relationships",
                "get_impact_context",
                "get_transitive_impact",
                "get_column_impact",
                "get_index_context",
                "get_graph_context",
                "find_path",
                "compare_snapshots",
                "list_snapshots",
            }
            result = await client.call_tool(
                "search_objects", {"source": "erp", "query": "customer", "limit": 1}
            )
            assert result.is_error is False
            assert result.structured_content is None
            assert _json_content(result)["snapshot_version"] == 1
            overview = await client.call_tool("database_overview", {"source": "erp"})
            assert overview.is_error is False
            assert overview.structured_content is None
            assert _json_content(overview)["table_count"] == 1
            context = await client.call_tool(
                "get_relevant_context", {"source": "erp", "task": "customer"}
            )
            assert context.is_error is False
            assert context.structured_content is None
            assert _json_content(context)["selection_method"] == "lexical_name_match"
            graph = await client.call_tool(
                "get_graph_context", {"source": "erp", "name": '"public"."Customer"'}
            )
            assert graph.is_error is False and graph.structured_content is None
            assert _json_content(graph)["links"] == []
