"""Approved manual links appear only in opt-in local graph projections."""

import json
import sys
from dataclasses import replace
from pathlib import Path

import anyio
import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from mcp.types import TextContent
from pytest import MonkeyPatch
from typer.testing import CliRunner

import graphit.mcp_server as mcp_server
from graphit.cli import app
from graphit.graph_dot import render_dot
from graphit.graph_export import table_graph
from graphit.graph_html import render_html
from graphit.mcp_server import MAX_MCP_RESPONSE_CHARS, create_server
from graphit.review import (
    approve_candidate,
    approve_manual_proposal,
    propose_relationship,
    revoke_manual_proposal,
)
from graphit.scanners.protocol import MetadataSnapshot, SchemaMetadata, TableMetadata
from graphit.snapshots import persist_snapshot
from graphit.sources import add_source, show_source
from tests.test_inference_benchmark import (
    _display,
    _fixture_columns,
    _fixture_keys,
    _save_fixture,
)

SOURCE = _display(("sales", "support_ticket", "client_id"))
TARGET = _display(("crm", "customer", "id"))
REASON = 'ERP owner confirmed identity; <script>alert("x")</script> is test text.'


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _approved(root: Path) -> None:
    propose_relationship(root, "erp", SOURCE, TARGET, 1, REASON)
    approve_manual_proposal(root, "erp", SOURCE, TARGET, 1)


def test_manual_link_is_local_opt_in_and_rendered_with_reason(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    _save_fixture(tmp_path)

    def no_source_connection(*args: object, **kwargs: object) -> None:
        raise AssertionError("Graph projection must not contact PostgreSQL")

    monkeypatch.setattr("psycopg.connect", no_source_connection)
    assert table_graph(tmp_path, "erp", "sales.support_ticket", include_manual=True).links == ()
    _approved(tmp_path)
    projection = table_graph(tmp_path, "erp", "sales.support_ticket", include_manual=True)
    assert len(projection.links) == 1
    link = projection.links[0]
    assert (link.origin, link.status, link.reason, link.confidence) == (
        "MANUAL",
        "APPROVED",
        REASON,
        None,
    )
    assert link.column_pairs == ((SOURCE, TARGET),)
    target_graph = table_graph(tmp_path, "erp", "crm.customer", include_manual=True)
    assert tuple(item for item in target_graph.links if item.origin == "MANUAL") == (link,)
    assert any(item.origin == "DATABASE" for item in target_graph.links)
    assert table_graph(tmp_path, "erp", "sales.support_ticket").links == ()

    runner = CliRunner()
    args = ["graph", "sales.support_ticket", "--source", "erp", "--project", str(tmp_path)]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert (payload["links"][0]["origin"], payload["links"][0]["reason"]) == ("MANUAL", REASON)
    dot = render_dot(projection)
    assert "MANUAL APPROVED" in dot and 'style="dashed,bold"' in dot
    assert render_dot(projection) == dot
    html = render_html(projection)
    assert "Human-approved manual link" in html
    assert "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;" in html
    assert '<script>alert("x")</script>' not in html

    revoke_manual_proposal(tmp_path, "erp", SOURCE, TARGET, 1)
    assert table_graph(tmp_path, "erp", "sales.support_ticket", include_manual=True).links == ()
    propose_relationship(tmp_path, "erp", SOURCE, TARGET, 1, REASON)
    approve_manual_proposal(tmp_path, "erp", SOURCE, TARGET, 1)
    assert len(table_graph(tmp_path, "erp", "sales.support_ticket", include_manual=True).links) == 1


def test_manual_graph_limit_and_stale_rescan_do_not_claim_completeness(tmp_path: Path) -> None:
    _save_fixture(tmp_path)
    _approved(tmp_path)
    second = _display(("sales", "invoice", "customer_id"))
    propose_relationship(tmp_path, "erp", second, TARGET, 1, "Invoice belongs to customer.")
    approve_manual_proposal(tmp_path, "erp", second, TARGET, 1)
    graph = table_graph(tmp_path, "erp", "crm.customer", approved_limit=1, include_manual=True)
    assert len([link for link in graph.links if link.origin == "MANUAL"]) == 1
    assert graph.truncated and graph.manual_truncated
    assert not graph.fk_truncated and not graph.approved_truncated
    assert "TRUNCATED: MANUAL" in render_dot(graph)
    assert "more manual links" in render_html(graph)

    columns = tuple(column for column in _fixture_columns() if column.name != "client_id")
    tables = sorted({(column.schema_name, column.table_name) for column in columns})
    schemas = sorted({schema for schema, _ in tables})
    persist_snapshot(
        tmp_path,
        show_source(tmp_path, "erp"),
        MetadataSnapshot(
            source_name="erp",
            schemas=tuple(SchemaMetadata(name) for name in schemas),
            tables=tuple(TableMetadata(schema, table, False) for schema, table in tables),
            columns=columns,
            keys=_fixture_keys(),
            foreign_keys=(),
        ),
    )
    latest = table_graph(tmp_path, "erp", "crm.customer", include_manual=True)
    assert latest.snapshot_version == 2
    assert len(latest.links) == 1
    assert latest.links[0].column_pairs == ((second, TARGET),)


def test_manual_graph_prefers_human_reason_over_same_inferred_pair(tmp_path: Path) -> None:
    _save_fixture(tmp_path)
    source = _display(("sales", "invoice", "customer_id"))
    propose_relationship(tmp_path, "erp", source, TARGET, 1, "Owner confirmed identity.")
    approve_manual_proposal(tmp_path, "erp", source, TARGET, 1)
    approve_candidate(tmp_path, "erp", source, TARGET, 1)
    projection = table_graph(tmp_path, "erp", "sales.invoice", include_manual=True)
    same_pair = [link for link in projection.links if link.column_pairs == ((source, TARGET),)]
    assert len(same_pair) == 1
    assert (same_pair[0].origin, same_pair[0].reason) == ("MANUAL", "Owner confirmed identity.")


@pytest.mark.anyio
async def test_manual_graph_link_is_bounded_and_provenance_safe_in_mcp(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    _save_fixture(tmp_path)

    def no_source_connection(*args: object, **kwargs: object) -> None:
        raise AssertionError("MCP context must not contact PostgreSQL")

    monkeypatch.setattr("psycopg.connect", no_source_connection)
    async with Client(create_server(tmp_path)) as client:
        before = await client.call_tool(
            "get_graph_context", {"source": "erp", "name": "sales.support_ticket"}
        )
        _approved(tmp_path)
        result = await client.call_tool(
            "get_graph_context", {"source": "erp", "name": "sales.support_ticket"}
        )
        facts = await client.call_tool(
            "get_relationships", {"source": "erp", "name": "sales.support_ticket"}
        )
        assert isinstance(before.content[0], TextContent)
        assert isinstance(result.content[0], TextContent)
        middle = (len(before.content[0].text) + len(result.content[0].text)) // 2
        with monkeypatch.context() as patch:
            patch.setattr(mcp_server, "MAX_MCP_RESPONSE_CHARS", middle)
            over_budget = await client.call_tool(
                "get_graph_context", {"source": "erp", "name": "sales.support_ticket"}
            )
        revoke_manual_proposal(tmp_path, "erp", SOURCE, TARGET, 1)
        revoked = await client.call_tool(
            "get_graph_context", {"source": "erp", "name": "sales.support_ticket"}
        )
    assert result.is_error is False
    assert len(result.content) == 1
    assert isinstance(result.content[0], TextContent)
    payload = json.loads(result.content[0].text)
    assert len(payload["links"]) == 1
    link = payload["links"][0]
    assert (link["origin"], link["status"], link["human_reason"]) == ("MANUAL", "APPROVED", REASON)
    assert link["column_pairs"] == [[SOURCE, TARGET]]
    assert "metadata_score" not in link and "foreign_key" not in link
    assert "manual_truncated" not in payload
    assert over_budget.is_error is True
    assert any(
        isinstance(item, TextContent) and "CONTEXT_TOO_LARGE" in item.text
        for item in over_budget.content
    )
    assert len(result.content[0].text) <= MAX_MCP_RESPONSE_CHARS
    assert isinstance(before.content[0], TextContent)
    assert len(result.content[0].text) > len(before.content[0].text)
    assert len(result.content[0].text) - len(before.content[0].text) < 1000
    assert json.loads(before.content[0].text)["links"] == []
    assert isinstance(facts.content[0], TextContent)
    assert json.loads(facts.content[0].text)["outgoing"] == []
    assert isinstance(revoked.content[0], TextContent)
    assert json.loads(revoked.content[0].text)["links"] == []


@pytest.mark.anyio
async def test_manual_graph_mcp_real_stdio_and_source_isolation(tmp_path: Path) -> None:
    _save_fixture(tmp_path)
    _approved(tmp_path)
    original = show_source(tmp_path, "erp")
    other = replace(original, name="other")
    add_source(tmp_path, other)
    columns = _fixture_columns()
    tables = sorted({(column.schema_name, column.table_name) for column in columns})
    schemas = sorted({schema for schema, _ in tables})
    persist_snapshot(
        tmp_path,
        other,
        MetadataSnapshot(
            source_name="other",
            schemas=tuple(SchemaMetadata(name) for name in schemas),
            tables=tuple(TableMetadata(schema, table, False) for schema, table in tables),
            columns=columns,
            keys=_fixture_keys(),
            foreign_keys=(),
        ),
    )
    process = StdioServerParameters(
        command=sys.executable,
        args=["-m", "graphit", "mcp", "serve", "--project", str(tmp_path)],
    )
    with anyio.fail_after(15):
        async with Client(process) as client:
            erp = await client.call_tool(
                "get_graph_context", {"source": "erp", "name": "sales.support_ticket"}
            )
            isolated = await client.call_tool(
                "get_graph_context", {"source": "other", "name": "sales.support_ticket"}
            )
    assert isinstance(erp.content[0], TextContent)
    assert json.loads(erp.content[0].text)["links"][0]["origin"] == "MANUAL"
    assert isinstance(isolated.content[0], TextContent)
    assert json.loads(isolated.content[0].text)["links"] == []


@pytest.mark.anyio
async def test_manual_mcp_truncation_and_stale_rescan(tmp_path: Path) -> None:
    _save_fixture(tmp_path)
    _approved(tmp_path)
    second = _display(("sales", "invoice", "customer_id"))
    propose_relationship(tmp_path, "erp", second, TARGET, 1, "Invoice owner confirmed link.")
    approve_manual_proposal(tmp_path, "erp", second, TARGET, 1)
    async with Client(create_server(tmp_path)) as client:
        limited = await client.call_tool(
            "get_graph_context", {"source": "erp", "name": "crm.customer", "limit": 1}
        )
        assert isinstance(limited.content[0], TextContent)
        limited_payload = json.loads(limited.content[0].text)
        assert limited_payload["manual_truncated"] is True
        assert limited_payload["truncated"] is True
        assert len([link for link in limited_payload["links"] if link["origin"] == "MANUAL"]) == 1

        columns = tuple(column for column in _fixture_columns() if column.name != "client_id")
        tables = sorted({(column.schema_name, column.table_name) for column in columns})
        schemas = sorted({schema for schema, _ in tables})
        persist_snapshot(
            tmp_path,
            show_source(tmp_path, "erp"),
            MetadataSnapshot(
                source_name="erp",
                schemas=tuple(SchemaMetadata(name) for name in schemas),
                tables=tuple(TableMetadata(schema, table, False) for schema, table in tables),
                columns=columns,
                keys=_fixture_keys(),
                foreign_keys=(),
            ),
        )
        current = await client.call_tool(
            "get_graph_context", {"source": "erp", "name": "crm.customer"}
        )
    assert isinstance(current.content[0], TextContent)
    payload = json.loads(current.content[0].text)
    assert payload["snapshot_version"] == 2
    manual = [link for link in payload["links"] if link["origin"] == "MANUAL"]
    assert len(manual) == 1 and manual[0]["column_pairs"] == [[second, TARGET]]
    assert "manual_truncated" not in payload
