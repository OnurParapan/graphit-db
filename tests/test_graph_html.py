"""Self-contained HTML graph is interactive, safe, and source-independent."""

import base64
import hashlib
import re
from pathlib import Path

from typer.testing import CliRunner

from graphit.cli import app
from graphit.graph_export import DatabaseGraphProjection, GraphLink, GraphNode, GraphProjection
from graphit.graph_html import render_database_html, render_html
from graphit.inference import CandidateEvidence
from graphit.project import initialize_project
from graphit.review import approve_candidate
from tests.test_review import SOURCE_COLUMN, TARGET_COLUMN, _save, _source


def test_html_shows_graph_list_scope_provenance_and_truncation() -> None:
    projection = GraphProjection(
        source_name="erp",
        snapshot_version=2,
        focus_table='"public"."invoice"',
        depth=1,
        nodes=(
            GraphNode('"public"."invoice"', True, True),
            GraphNode('"external"."legacy"', False, False),
            GraphNode('"public"."customer"', True, False),
        ),
        links=(
            GraphLink(
                '"public"."invoice"',
                '"external"."legacy"',
                (('"public"."invoice"."legacy_id"', '"external"."legacy"."id"'),),
                "DATABASE",
                "CONFIRMED",
                name="legacy_fk",
                target_in_scope=False,
                validated=False,
            ),
            GraphLink(
                '"public"."invoice"',
                '"public"."customer"',
                ((SOURCE_COLUMN, TARGET_COLUMN),),
                "INFERRED",
                "APPROVED",
                confidence=0.8,
                evidence=(CandidateEvidence("TARGET_UNIQUE", 1.0, 0.25, "Single-column key."),),
            ),
        ),
        truncated=True,
        fk_truncated=True,
        approved_truncated=False,
    )
    html = render_html(projection)
    assert html == render_html(projection)
    assert html.startswith("<!doctype html>\n")
    assert '<svg xmlns="http://www.w3.org/2000/svg"' in html
    assert 'role="img" aria-labelledby="graph-title graph-desc"' in html
    assert "Partial graph: more foreign keys" in html
    assert "FK: database-confirmed" in html
    assert "Human-approved logical link" in html
    assert "metadata score 0.80; not a probability" in html
    assert "outside scan scope" in html
    assert "Metadata evidence" in html
    assert "TARGET_UNIQUE" in html
    assert "svg .edge-label{fill:#17212b;" in html
    assert "&quot;public&quot;.&quot;invoice&quot;.&quot;customer_id&quot;" in html
    assert "<h2>Tables</h2>" in html
    assert "<h2>Relationships and columns</h2>" in html
    assert '<input id="graph-search" type="search"' in html
    assert '<select id="relationship-filter">' in html
    assert 'data-kind="fk"' in html
    assert 'data-kind="approved"' in html
    assert "Search and filters require browser JavaScript" in html
    script = re.search(r"<script>(.*)</script>", html, re.DOTALL)
    assert script is not None
    digest = base64.b64encode(hashlib.sha256(script.group(1).encode()).digest()).decode()
    assert f"script-src 'sha256-{digest}'" in html
    assert "script-src 'unsafe-inline'" not in html
    assert "<link" not in html


def test_html_escapes_untrusted_metadata_and_keeps_svg_edges_bounded() -> None:
    hostile = '"public"."</style><script>alert(1)</script>"'
    projection = GraphProjection(
        "erp", 1, hostile, 1, (GraphNode(hostile, True, True),), (), False, False, False
    )
    html = render_html(projection)
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<script>alert(1)</script>" not in html
    assert html.count("<script>") == 1
    assert html.count("<svg ") == 1
    assert "No confirmed or human-approved relationships" in html
    assert "default-src 'none'" in html


def test_database_html_is_complete_searchable_and_has_no_fake_focus() -> None:
    projection = DatabaseGraphProjection(
        "erp",
        3,
        (
            GraphNode('"billing"."invoice"', True, False),
            GraphNode('"crm"."customer"', True, False),
        ),
        (
            GraphLink(
                '"billing"."invoice"',
                '"crm"."customer"',
                (
                    (
                        '"billing"."invoice"."customer_id"',
                        '"crm"."customer"."id"',
                    ),
                ),
                "DATABASE",
                "CONFIRMED",
                name="invoice_customer_fk",
                validated=True,
            ),
        ),
    )

    html = render_database_html(projection)

    assert "Graphit whole-database ERD: erp" in html
    assert "complete saved scope" in html
    assert "2 tables" in html
    assert "1 confirmed FKs" in html
    assert "Whole-database relationship graph" in html
    assert "invoice_customer_fk" in html
    assert "customer_id" in html
    assert "depth" not in html
    assert 'data-selected="false"' in html


def test_graph_cli_html_shows_only_current_approved_links(tmp_path: Path) -> None:
    initialize_project(tmp_path)
    source = _source(tmp_path)
    _save(source, tmp_path)
    runner = CliRunner()
    args = ["graph", "public.invoice", "--source", "erp", "--project", str(tmp_path)]
    pending = runner.invoke(app, [*args, "--format", "html"])
    assert pending.exit_code == 0
    assert "HUMAN APPROVED" not in pending.stdout
    assert "APPROVED: human logical link" in pending.stdout
    assert "No confirmed or human-approved relationships" in pending.stdout
    approve_candidate(tmp_path, "erp", SOURCE_COLUMN, TARGET_COLUMN, 1)
    approved = runner.invoke(app, [*args, "--format", "html"])
    assert approved.exit_code == 0
    assert "Human-approved logical link" in approved.stdout
    assert "metadata score 0.80" in approved.stdout


def test_graph_cli_explicit_output_is_utf8_and_never_overwrites(tmp_path: Path) -> None:
    initialize_project(tmp_path)
    source = _source(tmp_path)
    _save(source, tmp_path)
    runner = CliRunner()
    output = tmp_path / "invoice.html"
    args = [
        "graph",
        "public.invoice",
        "--source",
        "erp",
        "--project",
        str(tmp_path),
        "--format",
        "html",
        "--output",
        str(output),
    ]
    first = runner.invoke(app, args)
    assert first.exit_code == 0
    assert first.stdout.startswith("Created graph:")
    original = output.read_bytes()
    assert original.startswith(b"<!doctype html>\n")
    assert b"<svg " in original
    repeated = runner.invoke(app, args)
    assert repeated.exit_code == 2
    assert "OUTPUT_EXISTS" in repeated.output
    assert output.read_bytes() == original

    missing_output = tmp_path / "missing.html"
    missing = runner.invoke(
        app,
        [
            "graph",
            "public.absent",
            "--source",
            "erp",
            "--project",
            str(tmp_path),
            "--format",
            "html",
            "--output",
            str(missing_output),
        ],
    )
    assert missing.exit_code == 6
    assert not missing_output.exists()
