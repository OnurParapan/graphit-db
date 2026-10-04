"""One-hop JSON projection keeps database facts and human decisions separate."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from pytest import MonkeyPatch
from typer.testing import CliRunner

import graphit.graph_export as graph_export
import graphit.inference as inference
from graphit.cli import app
from graphit.graph_export import database_graph, table_graph
from graphit.inference import MAX_PREVIEW_TABLES, current_approval_presence
from graphit.project import initialize_project
from graphit.queries import QueryError
from graphit.review import approve_candidate, revoke_approval
from graphit.scanners.protocol import (
    ColumnMetadata,
    ForeignKeyMetadata,
    KeyConstraintMetadata,
    MetadataSnapshot,
    SchemaMetadata,
    TableMetadata,
)
from graphit.snapshots import persist_snapshot
from tests.test_queries import _project, _relationship_metadata
from tests.test_review import SOURCE_COLUMN, TARGET_COLUMN, _save, _source


def test_approved_projection_is_local_and_revocation_removes_link(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    initialize_project(tmp_path)
    source = _source(tmp_path)
    _save(source, tmp_path)

    def no_source_connection(*args: object, **kwargs: object) -> None:
        raise AssertionError("Graph projection must not contact PostgreSQL")

    monkeypatch.setattr("psycopg.connect", no_source_connection)
    pending = table_graph(tmp_path, "erp", "public.invoice")
    assert pending.links == ()
    assert [node.qualified_name for node in pending.nodes] == ['"public"."invoice"']

    approve_candidate(tmp_path, "erp", SOURCE_COLUMN, TARGET_COLUMN, 1)
    approved = table_graph(tmp_path, "erp", "public.invoice")
    assert len(approved.links) == 1
    link = approved.links[0]
    assert (link.origin, link.status, link.confidence) == ("INFERRED", "APPROVED", 0.8)
    assert link.column_pairs == ((SOURCE_COLUMN, TARGET_COLUMN),)
    assert link.evidence
    assert len(approved.nodes) == 2
    assert not approved.truncated

    revoke_approval(tmp_path, "erp", SOURCE_COLUMN, TARGET_COLUMN, 1)
    assert table_graph(tmp_path, "erp", "public.invoice").links == ()


def test_fk_projection_deduplicates_self_link_and_labels_external_scope(tmp_path: Path) -> None:
    source = _project(tmp_path)
    persist_snapshot(tmp_path, source, _relationship_metadata())
    projection = table_graph(tmp_path, "erp", "public.orders")
    assert len(projection.links) == 4
    assert len({(link.name, link.source_table) for link in projection.links}) == 4
    assert all((link.origin, link.status) == ("DATABASE", "CONFIRMED") for link in projection.links)
    external = next(link for link in projection.links if link.name == "orders_external_fk")
    assert not external.target_in_scope
    assert external.column_pairs == (
        ('"public"."orders"."customer_id"', '"external"."legacy"."id"'),
    )
    external_node = next(
        node for node in projection.nodes if node.qualified_name == external.target_table
    )
    assert not external_node.in_scope
    assert projection.focus_table == '"public"."orders"'
    assert projection.depth == 1


def test_database_graph_contains_every_table_and_confirmed_fk_without_truncation(
    tmp_path: Path,
) -> None:
    source = _project(tmp_path)
    persist_snapshot(tmp_path, source, _relationship_metadata())

    projection = database_graph(tmp_path, "erp")

    assert projection.complete
    assert projection.scope == "DATABASE"
    assert projection.snapshot_version == 1
    assert len(projection.links) == 4
    assert all((link.origin, link.status) == ("DATABASE", "CONFIRMED") for link in projection.links)
    assert all(not node.selected for node in projection.nodes)
    external = next(
        node for node in projection.nodes if node.qualified_name == '"external"."legacy"'
    )
    assert not external.in_scope
    assert '"public"."orders"' in {node.qualified_name for node in projection.nodes}
    orders = next(node for node in projection.nodes if node.qualified_name == '"public"."orders"')
    assert orders.columns
    assert orders.columns[0].qualified_name.startswith('"public"."orders".')


def test_database_graph_fails_instead_of_emitting_partial_erd(tmp_path: Path) -> None:
    source = _project(tmp_path)
    persist_snapshot(tmp_path, source, _relationship_metadata())

    with pytest.raises(QueryError) as error:
        database_graph(tmp_path, "erp", node_limit=1)

    assert error.value.code == "ERD_NODE_LIMIT_EXCEEDED"


def test_erd_cli_creates_snapshot_named_html_and_never_overwrites(tmp_path: Path) -> None:
    source = _project(tmp_path)
    persist_snapshot(tmp_path, source, _relationship_metadata())
    runner = CliRunner()
    args = ["erd", "--source", "erp", "--project", str(tmp_path)]

    created = runner.invoke(app, args)
    output = tmp_path / ".graphit" / "exports" / "erp-snapshot-1-erd.html"

    assert created.exit_code == 0
    assert "Created whole-database ERD" in created.stdout
    assert output.read_text(encoding="utf-8").startswith("<!doctype html>\n")
    repeated = runner.invoke(app, args)
    assert repeated.exit_code == 2
    assert "OUTPUT_EXISTS" in repeated.output


def test_graph_cli_json_and_exact_lookup_errors(tmp_path: Path) -> None:
    initialize_project(tmp_path)
    source = _source(tmp_path)
    _save(source, tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        app, ["graph", "public.invoice", "--source", "erp", "--project", str(tmp_path)]
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["focus_table"] == '"public"."invoice"'
    assert payload["links"] == []
    missing = runner.invoke(
        app, ["graph", "public.nope", "--source", "erp", "--project", str(tmp_path)]
    )
    assert missing.exit_code == 6
    assert "TABLE_NOT_FOUND" in missing.output


def test_graph_rejects_ambiguous_table_and_reports_latest_snapshot(tmp_path: Path) -> None:
    initialize_project(tmp_path)
    source = _source(tmp_path, schemas=("public", "sales"))
    _save(source, tmp_path, ambiguous=True)
    with pytest.raises(QueryError, match="multiple schemas"):
        table_graph(tmp_path, "erp", "customer")
    _save(source, tmp_path, ambiguous=True)
    assert table_graph(tmp_path, "erp", "sales.customer").snapshot_version == 2


def test_graph_reports_fk_truncation_instead_of_implying_completeness(tmp_path: Path) -> None:
    source = _project(tmp_path)
    spokes = tuple(f"spoke_{number}" for number in range(101))
    persist_snapshot(
        tmp_path,
        source,
        MetadataSnapshot(
            source_name="erp",
            schemas=(SchemaMetadata("public"),),
            tables=(TableMetadata("public", "hub", False),)
            + tuple(TableMetadata("public", name, False) for name in spokes),
            columns=(ColumnMetadata("public", "hub", "id", 1, "integer", False),)
            + tuple(
                ColumnMetadata("public", name, "hub_id", 1, "integer", False) for name in spokes
            ),
            keys=(),
            foreign_keys=tuple(
                ForeignKeyMetadata(
                    "public",
                    name,
                    f"{name}_fk",
                    ("hub_id",),
                    "public",
                    "hub",
                    ("id",),
                    True,
                    True,
                    False,
                )
                for name in spokes
            ),
        ),
    )
    graph = table_graph(tmp_path, "erp", "public.hub")
    assert len(graph.links) == 100
    assert len(graph.nodes) == 101
    assert graph.truncated and graph.fk_truncated
    assert not graph.approved_truncated


def test_fk_only_graph_skips_whole_snapshot_inference_on_large_schema(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    source = _project(tmp_path)
    persist_snapshot(
        tmp_path,
        source,
        MetadataSnapshot(
            source_name="erp",
            schemas=(SchemaMetadata("public"),),
            tables=(TableMetadata("public", "focus", False),)
            + tuple(
                TableMetadata("public", f"table_{number}", False)
                for number in range(MAX_PREVIEW_TABLES)
            ),
            columns=(),
            keys=(),
            foreign_keys=(),
        ),
    )

    def no_inference(*args: object, **kwargs: object) -> None:
        raise AssertionError("No adjacent approval should skip candidate inference")

    monkeypatch.setattr(graph_export, "preview_candidates", no_inference)
    result = table_graph(tmp_path, "erp", "public.focus")
    assert result.links == ()
    assert len(result.nodes) == 1
    assert not result.truncated


def test_approval_presence_uses_latest_event_and_exact_table_prefix(tmp_path: Path) -> None:
    initialize_project(tmp_path)
    source = _source(tmp_path)
    other = _source(tmp_path, name="other")
    _save(source, tmp_path)
    _save(other, tmp_path)
    assert current_approval_presence(tmp_path, "erp", '"public"."invoice"') == (1, False)
    approve_candidate(tmp_path, "erp", SOURCE_COLUMN, TARGET_COLUMN, 1)
    assert current_approval_presence(tmp_path, "erp", '"public"."invoice"') == (1, True)
    assert current_approval_presence(tmp_path, "erp", '"public"."customer"') == (1, True)
    assert current_approval_presence(tmp_path, "erp", '"public"."invoice_extra"') == (1, False)
    assert current_approval_presence(tmp_path, "other", '"public"."invoice"') == (1, False)
    revoke_approval(tmp_path, "erp", SOURCE_COLUMN, TARGET_COLUMN, 1)
    assert current_approval_presence(tmp_path, "erp", '"public"."invoice"') == (1, False)


def test_graph_fails_on_rescan_between_fk_and_review_reads(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    initialize_project(tmp_path)
    source = _source(tmp_path)
    _save(source, tmp_path)
    monkeypatch.setattr(graph_export, "current_approval_presence", lambda *args: (2, False))
    with pytest.raises(QueryError) as error:
        table_graph(tmp_path, "erp", "public.invoice")
    assert error.value.code == "SNAPSHOT_CHANGED"


def test_approved_graph_on_large_snapshot_revalidates_pair_and_declared_fk(tmp_path: Path) -> None:
    initialize_project(tmp_path)
    source = _source(tmp_path)
    _save(source, tmp_path)
    approve_candidate(tmp_path, "erp", SOURCE_COLUMN, TARGET_COLUMN, 1)
    filler = tuple(f"other_{number}" for number in range(MAX_PREVIEW_TABLES - 1))
    large = MetadataSnapshot(
        source_name="erp",
        schemas=(SchemaMetadata("public"),),
        tables=(
            TableMetadata("public", "customer", False),
            TableMetadata("public", "invoice", False),
        )
        + tuple(TableMetadata("public", name, False) for name in filler),
        columns=(
            ColumnMetadata("public", "customer", "id", 1, "integer", False),
            ColumnMetadata("public", "invoice", "customer_id", 1, "integer", False),
        ),
        keys=(
            KeyConstraintMetadata(
                "public", "customer", "customer_pkey", "PRIMARY_KEY", ("id",), False
            ),
        ),
        foreign_keys=(),
    )
    persist_snapshot(tmp_path, source, large)
    approved = table_graph(tmp_path, "erp", "public.invoice")
    assert approved.snapshot_version == 2
    assert len(approved.links) == 1
    assert (approved.links[0].origin, approved.links[0].status) == ("INFERRED", "APPROVED")

    persist_snapshot(tmp_path, source, replace(large, keys=()))
    assert table_graph(tmp_path, "erp", "public.invoice").links == ()

    declared = ForeignKeyMetadata(
        "public",
        "invoice",
        "invoice_customer_fk",
        ("customer_id",),
        "public",
        "customer",
        ("id",),
        True,
        True,
        False,
    )
    persist_snapshot(tmp_path, source, replace(large, foreign_keys=(declared,)))
    current = table_graph(tmp_path, "erp", "public.invoice")
    assert current.snapshot_version == 4
    assert len(current.links) == 1
    assert (current.links[0].origin, current.links[0].status) == ("DATABASE", "CONFIRMED")


def test_approved_graph_keeps_ambiguous_alternative_count(tmp_path: Path) -> None:
    initialize_project(tmp_path)
    source = _source(tmp_path, schemas=("public", "sales"))
    _save(source, tmp_path, ambiguous=True)
    approve_candidate(tmp_path, "erp", SOURCE_COLUMN, TARGET_COLUMN, 1)
    graph = table_graph(tmp_path, "erp", "public.invoice")
    assert len(graph.links) == 1
    assert graph.links[0].status == "APPROVED"
    assert graph.links[0].confidence == 0.8


def test_targeted_approval_validation_fails_on_work_cap(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    initialize_project(tmp_path)
    source = _source(tmp_path)
    _save(source, tmp_path)
    approve_candidate(tmp_path, "erp", SOURCE_COLUMN, TARGET_COLUMN, 1)
    monkeypatch.setattr(inference, "MAX_APPROVED_RELEVANT_COLUMNS", 1)
    with pytest.raises(QueryError) as error:
        table_graph(tmp_path, "erp", "public.invoice")
    assert error.value.code == "INFERENCE_BUDGET_EXCEEDED"
