"""DOT rendering remains local, bounded, deterministic, and provenance-aware."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from graphit.cli import app
from graphit.graph_dot import render_dot
from graphit.graph_export import GraphLink, GraphNode, GraphProjection
from graphit.project import initialize_project
from graphit.review import approve_candidate
from tests.test_review import SOURCE_COLUMN, TARGET_COLUMN, _save, _source


def test_dot_renders_fk_and_approved_link_with_distinct_labels() -> None:
    projection = GraphProjection(
        source_name="erp",
        snapshot_version=3,
        focus_table='"public"."invoice"',
        depth=1,
        nodes=(
            GraphNode('"public"."invoice"', True, True),
            GraphNode('"public"."customer"', True, False),
            GraphNode('"external"."legacy"', False, False),
        ),
        links=(
            GraphLink(
                '"public"."invoice"',
                '"external"."legacy"',
                (('"public"."invoice"."legacy_id"', '"external"."legacy"."id"'),),
                "DATABASE",
                "CONFIRMED",
                name="invoice_legacy_fk",
                target_in_scope=False,
                validated=False,
                inherited=True,
            ),
            GraphLink(
                '"public"."invoice"',
                '"public"."customer"',
                ((SOURCE_COLUMN, TARGET_COLUMN),),
                "INFERRED",
                "APPROVED",
                confidence=0.8,
            ),
        ),
        truncated=True,
        fk_truncated=True,
        approved_truncated=False,
    )
    dot = render_dot(projection)
    assert dot == render_dot(projection)
    assert dot.startswith("digraph graphit {\n")
    assert "TRUNCATED: FK" in dot
    assert "FK invoice_legacy_fk [NOT VALID, INHERITED]" in dot
    assert "HUMAN APPROVED (logical; metadata score 0.80)" in dot
    assert 'style="solid", arrowhead="normal"' in dot
    assert 'style="bold", arrowhead="vee"' in dot
    assert "(outside scan scope)" in dot
    assert "peripheries=2" in dot
    assert dot.count(" -> ") >= 2


def test_dot_escapes_untrusted_names_and_rejects_unrecognized_links() -> None:
    hostile = '"public"."bad\\name\n; n0 -> n0 [label="oops"]"'
    projection = GraphProjection(
        "erp", 1, hostile, 1, (GraphNode(hostile, True, True),), (), False, False, False
    )
    dot = render_dot(projection)
    assert '\\\\name\\n; n0 -> n0 [label=\\"oops\\"]' in dot
    assert len(dot.splitlines()) == 5
    bad = GraphLink(hostile, hostile, (("a", "b"),), "INFERRED", "PENDING")
    with pytest.raises(ValueError, match="only confirmed FKs"):
        render_dot(
            GraphProjection("erp", 1, hostile, 1, projection.nodes, (bad,), False, False, False)
        )


def test_graph_cli_dot_keeps_json_default_and_excludes_pending(tmp_path: Path) -> None:
    initialize_project(tmp_path)
    source = _source(tmp_path)
    _save(source, tmp_path)
    runner = CliRunner()
    args = ["graph", "public.invoice", "--source", "erp", "--project", str(tmp_path)]
    pending = runner.invoke(app, [*args, "--format", "dot"])
    assert pending.exit_code == 0
    assert pending.stdout.startswith("digraph graphit {\n")
    assert "HUMAN APPROVED" not in pending.stdout
    assert runner.invoke(app, args).stdout.startswith("{")

    approve_candidate(tmp_path, "erp", SOURCE_COLUMN, TARGET_COLUMN, 1)
    approved = runner.invoke(app, [*args, "--format", "dot"])
    assert approved.exit_code == 0
    assert "HUMAN APPROVED (logical; metadata score 0.80)" in approved.stdout
    invalid = runner.invoke(app, [*args, "--format", "svg"])
    assert invalid.exit_code == 2
