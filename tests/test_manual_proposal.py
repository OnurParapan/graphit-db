"""Human proposals stay local and separate from approved graph relationships."""

import json
import sqlite3
from pathlib import Path

import pytest
from pytest import MonkeyPatch
from typer.testing import CliRunner

from graphit.cli import app
from graphit.graph_export import table_graph
from graphit.inference import preview_candidates
from graphit.queries import QueryError
from graphit.review import (
    approve_manual_proposal,
    list_proposals,
    propose_relationship,
    reject_candidate,
    revoke_manual_proposal,
)
from graphit.scanners.protocol import (
    ForeignKeyMetadata,
    MetadataSnapshot,
    SchemaMetadata,
    TableMetadata,
)
from graphit.snapshots import persist_snapshot
from graphit.sources import show_source
from tests.test_inference_benchmark import (
    _display,
    _fixture_columns,
    _fixture_keys,
    _save_fixture,
)
from tests.test_review import _save, _source

SOURCE = ("sales", "support_ticket", "client_id")
TARGET = ("crm", "customer", "id")
REASON = "Client ID is the CRM customer identity, confirmed by the ERP owner."


def test_manual_synonym_proposal_is_local_pending_and_idempotent(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    _save_fixture(tmp_path)

    def no_source_connection(*args: object, **kwargs: object) -> None:
        raise AssertionError("Proposal must not connect to PostgreSQL")

    monkeypatch.setattr("psycopg.connect", no_source_connection)
    source, target = _display(SOURCE), _display(TARGET)
    assert all(
        candidate.source_column != source
        for candidate in preview_candidates(tmp_path, "erp").candidates
    )
    first = propose_relationship(tmp_path, "erp", source, target, 1, REASON)
    repeated = propose_relationship(tmp_path, "erp", source, target, 1, REASON)
    assert (first.origin, first.status, first.changed, repeated.changed) == (
        "MANUAL",
        "PROPOSED",
        True,
        False,
    )
    assert table_graph(tmp_path, "erp", "sales.support_ticket").links == ()
    assert all(
        candidate.source_column != source
        for candidate in preview_candidates(tmp_path, "erp").candidates
    )
    with sqlite3.connect(tmp_path / ".graphit" / "graphit.db") as connection:
        rows = connection.execute(
            """SELECT relationship_kind, decision, comment FROM review_decisions"""
        ).fetchall()
        inferred_edges = connection.execute(
            "SELECT COUNT(*) FROM edges WHERE edge_type = 'LIKELY_REFERENCES'"
        ).fetchone()[0]
    assert rows == [("MANUAL_PROPOSAL", "PROPOSED", REASON)]
    assert inferred_edges == 0


def test_manual_proposal_rejects_invalid_or_stale_pairs(tmp_path: Path) -> None:
    _save_fixture(tmp_path)
    source, target = _display(SOURCE), _display(TARGET)
    invalid_cases = (
        (source, target, 0, REASON, "INVALID_SNAPSHOT_VERSION"),
        (source, target, 2, REASON, "SNAPSHOT_CHANGED"),
        (source, target, 1, " ", "INVALID_PROPOSAL"),
        (source, target, 1, "x" * 301, "INVALID_PROPOSAL"),
        (source, target, 1, "line one\nline two", "INVALID_PROPOSAL"),
        (source, source, 1, REASON, "INVALID_PROPOSAL"),
        (source, '"crm"."missing"."id"', 1, REASON, "INVALID_PROPOSAL"),
        (source, '"sales"."invoice"."total_amount"', 1, REASON, "INVALID_PROPOSAL"),
        (_display(("sales", "order_line", "customer_id")), target, 1, REASON, "INVALID_PROPOSAL"),
        (_display(("sales", "payment", "customer_id")), target, 1, REASON, "INVALID_PROPOSAL"),
    )
    for source_name, target_name, version, reason, code in invalid_cases:
        with pytest.raises(QueryError) as invalid:
            propose_relationship(tmp_path, "erp", source_name, target_name, version, reason)
        assert invalid.value.code == code
    with sqlite3.connect(tmp_path / ".graphit" / "graphit.db") as connection:
        assert connection.execute("SELECT COUNT(*) FROM review_decisions").fetchone()[0] == 0


def test_manual_proposal_cli_json_and_reason_update(tmp_path: Path) -> None:
    _save_fixture(tmp_path)
    source, target = _display(SOURCE), _display(TARGET)
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "review",
            "propose",
            source,
            target,
            "--source",
            "erp",
            "--snapshot-version",
            "1",
            "--reason",
            REASON,
            "--json",
            "--project",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert (payload["origin"], payload["status"], payload["changed"]) == (
        "MANUAL",
        "PROPOSED",
        True,
    )
    updated = propose_relationship(
        tmp_path, "erp", source, target, 1, "Owner reconfirmed identity."
    )
    assert updated.changed is True
    with sqlite3.connect(tmp_path / ".graphit" / "graphit.db") as connection:
        comments = connection.execute("SELECT comment FROM review_decisions ORDER BY id").fetchall()
    assert comments == [(REASON,), ("Owner reconfirmed identity.",)]


def test_manual_proposal_fails_closed_on_damaged_fk_metadata(tmp_path: Path) -> None:
    _save_fixture(tmp_path)
    with sqlite3.connect(tmp_path / ".graphit" / "graphit.db") as connection:
        connection.execute(
            """UPDATE edges SET metadata_json = '{}'
            WHERE edge_type = 'REFERENCES' AND origin = 'DATABASE'"""
        )
    with pytest.raises(QueryError) as invalid:
        propose_relationship(
            tmp_path,
            "erp",
            _display(("sales", "payment", "customer_id")),
            _display(TARGET),
            1,
            REASON,
        )
    assert invalid.value.code == "STORE_READ_FAILED"
    with sqlite3.connect(tmp_path / ".graphit" / "graphit.db") as connection:
        assert connection.execute("SELECT COUNT(*) FROM review_decisions").fetchone()[0] == 0


def test_manual_proposals_latest_reason_paging_and_source_isolation(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    _save_fixture(tmp_path)
    other = _source(tmp_path, "other")
    _save(other, tmp_path)

    def no_source_connection(*args: object, **kwargs: object) -> None:
        raise AssertionError("Proposal listing must not connect to PostgreSQL")

    monkeypatch.setattr("psycopg.connect", no_source_connection)
    source, target = _display(SOURCE), _display(TARGET)
    vendor = _display(("purchasing", "purchase_order", "vendor_id"))
    supplier = _display(("purchasing", "supplier", "id"))
    propose_relationship(tmp_path, "erp", source, target, 1, REASON)
    propose_relationship(tmp_path, "erp", vendor, supplier, 1, "Vendor is supplier.")
    propose_relationship(tmp_path, "erp", source, target, 1, "Owner reconfirmed identity.")
    propose_relationship(
        tmp_path,
        "other",
        _display(("public", "invoice", "customer_id")),
        _display(("public", "customer", "id")),
        1,
        "Other source relationship.",
    )
    first = list_proposals(tmp_path, "erp", limit=1)
    second = list_proposals(tmp_path, "erp", limit=1, offset=1)
    assert (first.snapshot_version, first.truncated, first.proposals[0].reason) == (
        1,
        True,
        "Owner reconfirmed identity.",
    )
    assert first.proposals[0].eligibility == "ELIGIBLE"
    assert (second.proposals[0].source_column, second.truncated) == (vendor, False)
    assert len(list_proposals(tmp_path, "other").proposals) == 1
    assert list_proposals(tmp_path, "erp", offset=2).proposals == ()

    result = CliRunner().invoke(
        app,
        [
            "review",
            "proposals",
            "--source",
            "erp",
            "--limit",
            "1",
            "--json",
            "--project",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["truncated"] is True
    assert payload["proposals"][0]["status"] == "PROPOSED"
    assert payload["proposals"][0]["eligibility"] == "ELIGIBLE"


def test_manual_proposal_list_marks_rescan_staleness_without_hiding_history(tmp_path: Path) -> None:
    _save_fixture(tmp_path)
    source, target = _display(SOURCE), _display(TARGET)
    propose_relationship(tmp_path, "erp", source, target, 1, REASON)
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
    listed = list_proposals(tmp_path, "erp")
    assert listed.snapshot_version == 2
    assert len(listed.proposals) == 1
    assert listed.proposals[0].eligibility == "SOURCE_COLUMN_MISSING"
    assert listed.proposals[0].status == "PROPOSED"
    with pytest.raises(QueryError) as stale:
        approve_manual_proposal(tmp_path, "erp", source, target, 2)
    assert stale.value.code == "PROPOSAL_STALE"

    original_columns = _fixture_columns()
    original_tables = sorted(
        {(column.schema_name, column.table_name) for column in original_columns}
    )
    original_schemas = sorted({schema for schema, _ in original_tables})
    persist_snapshot(
        tmp_path,
        show_source(tmp_path, "erp"),
        MetadataSnapshot(
            source_name="erp",
            schemas=tuple(SchemaMetadata(name) for name in original_schemas),
            tables=tuple(TableMetadata(schema, table, False) for schema, table in original_tables),
            columns=original_columns,
            keys=_fixture_keys(),
            foreign_keys=(
                ForeignKeyMetadata(
                    "sales",
                    "support_ticket",
                    "ticket_customer_fk",
                    ("client_id",),
                    "crm",
                    "customer",
                    ("id",),
                    True,
                    True,
                    False,
                ),
            ),
        ),
    )
    declared = list_proposals(tmp_path, "erp")
    assert declared.snapshot_version == 3
    assert declared.proposals[0].eligibility == "DECLARED_FK_ADDED"
    with pytest.raises(QueryError) as declared_fk:
        approve_manual_proposal(tmp_path, "erp", source, target, 3)
    assert declared_fk.value.code == "PROPOSAL_STALE"


def test_manual_proposal_list_rejects_bad_limits_and_corrupt_history(tmp_path: Path) -> None:
    _save_fixture(tmp_path)
    for limit, offset in ((0, 0), (51, 0), (20, -1), (20, 5001)):
        with pytest.raises(QueryError) as invalid:
            list_proposals(tmp_path, "erp", limit, offset)
        assert invalid.value.code == "INVALID_LIMIT"
    source, target = _display(SOURCE), _display(TARGET)
    propose_relationship(tmp_path, "erp", source, target, 1, REASON)
    with sqlite3.connect(tmp_path / ".graphit" / "graphit.db") as connection:
        connection.execute(
            """UPDATE review_decisions SET decision = 'UNKNOWN'
            WHERE relationship_kind = 'MANUAL_PROPOSAL'"""
        )
    with pytest.raises(QueryError) as invalid:
        list_proposals(tmp_path, "erp")
    assert invalid.value.code == "STORE_READ_FAILED"


def test_manual_proposal_approval_is_explicit_local_and_idempotent(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    _save_fixture(tmp_path)

    def no_source_connection(*args: object, **kwargs: object) -> None:
        raise AssertionError("Manual approval must not connect to PostgreSQL")

    monkeypatch.setattr("psycopg.connect", no_source_connection)
    source, target = _display(SOURCE), _display(TARGET)
    propose_relationship(tmp_path, "erp", source, target, 1, REASON)
    runner = CliRunner()
    first = runner.invoke(
        app,
        [
            "review",
            "approve-proposal",
            source,
            target,
            "--source",
            "erp",
            "--snapshot-version",
            "1",
            "--json",
            "--project",
            str(tmp_path),
        ],
    )
    assert first.exit_code == 0, first.output
    payload = json.loads(first.stdout)
    assert (payload["origin"], payload["status"], payload["reason"], payload["changed"]) == (
        "MANUAL",
        "APPROVED",
        REASON,
        True,
    )
    repeated = approve_manual_proposal(tmp_path, "erp", source, target, 1)
    assert repeated.changed is False
    listed = list_proposals(tmp_path, "erp")
    assert (listed.proposals[0].status, listed.proposals[0].eligibility) == ("APPROVED", "ELIGIBLE")
    assert table_graph(tmp_path, "erp", "sales.support_ticket").links == ()
    assert all(
        candidate.source_column != source
        for candidate in preview_candidates(tmp_path, "erp").candidates
    )
    with sqlite3.connect(tmp_path / ".graphit" / "graphit.db") as connection:
        rows = connection.execute(
            """SELECT decision, comment FROM review_decisions
            WHERE relationship_kind = 'MANUAL_PROPOSAL' ORDER BY id"""
        ).fetchall()
    assert rows == [("PROPOSED", REASON), ("APPROVED", REASON)]
    with pytest.raises(QueryError) as conflict:
        propose_relationship(tmp_path, "erp", source, target, 1, "New reason")
    assert conflict.value.code == "REVIEW_CONFLICT"


def test_manual_approval_requires_exact_current_unconflicted_proposal(tmp_path: Path) -> None:
    _save_fixture(tmp_path)
    other = _source(tmp_path, "other")
    _save(other, tmp_path)
    source, target = _display(SOURCE), _display(TARGET)
    with pytest.raises(QueryError) as absent:
        approve_manual_proposal(tmp_path, "erp", source, target, 1)
    assert absent.value.code == "PROPOSAL_NOT_FOUND"
    propose_relationship(tmp_path, "erp", source, target, 1, REASON)
    for alias, version, code in (
        ("other", 1, "PROPOSAL_NOT_FOUND"),
        ("erp", 2, "SNAPSHOT_CHANGED"),
    ):
        with pytest.raises(QueryError) as invalid:
            approve_manual_proposal(tmp_path, alias, source, target, version)
        assert invalid.value.code == code

    inferred_source = _display(("sales", "invoice", "customer_id"))
    propose_relationship(tmp_path, "erp", inferred_source, target, 1, "Potential CRM link.")
    reject_candidate(tmp_path, "erp", inferred_source, target, 1)
    with pytest.raises(QueryError) as conflict:
        approve_manual_proposal(tmp_path, "erp", inferred_source, target, 1)
    assert conflict.value.code == "REVIEW_CONFLICT"


def test_manual_revocation_preserves_reason_and_requires_explicit_reproposal(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    _save_fixture(tmp_path)

    def no_source_connection(*args: object, **kwargs: object) -> None:
        raise AssertionError("Manual revocation must not connect to PostgreSQL")

    monkeypatch.setattr("psycopg.connect", no_source_connection)
    source, target = _display(SOURCE), _display(TARGET)
    propose_relationship(tmp_path, "erp", source, target, 1, REASON)
    with pytest.raises(QueryError) as not_approved:
        revoke_manual_proposal(tmp_path, "erp", source, target, 1)
    assert not_approved.value.code == "MANUAL_APPROVAL_NOT_FOUND"
    approve_manual_proposal(tmp_path, "erp", source, target, 1)
    runner = CliRunner()
    approved_view = runner.invoke(
        app, ["review", "proposals", "--source", "erp", "--project", str(tmp_path)]
    )
    assert approved_view.exit_code == 0, approved_view.output
    assert "APPROVED (manual)" in approved_view.stdout
    assert "PROPOSED (not approved)" not in approved_view.stdout

    revoked = runner.invoke(
        app,
        [
            "review",
            "revoke-proposal",
            source,
            target,
            "--source",
            "erp",
            "--snapshot-version",
            "1",
            "--json",
            "--project",
            str(tmp_path),
        ],
    )
    assert revoked.exit_code == 0, revoked.output
    payload = json.loads(revoked.stdout)
    assert (payload["origin"], payload["status"], payload["reason"], payload["changed"]) == (
        "MANUAL",
        "REVOKED",
        REASON,
        True,
    )
    assert revoke_manual_proposal(tmp_path, "erp", source, target, 1).changed is False
    listed = list_proposals(tmp_path, "erp")
    assert (listed.proposals[0].status, listed.proposals[0].reason) == ("REVOKED", REASON)
    assert table_graph(tmp_path, "erp", "sales.support_ticket").links == ()
    with pytest.raises(QueryError) as reapproval:
        approve_manual_proposal(tmp_path, "erp", source, target, 1)
    assert reapproval.value.code == "PROPOSAL_REVOKED"
    reproposed = propose_relationship(tmp_path, "erp", source, target, 1, REASON)
    assert (reproposed.status, reproposed.changed) == ("PROPOSED", True)
    assert approve_manual_proposal(tmp_path, "erp", source, target, 1).changed is True
    with sqlite3.connect(tmp_path / ".graphit" / "graphit.db") as connection:
        rows = connection.execute(
            """SELECT decision, comment FROM review_decisions
            WHERE relationship_kind = 'MANUAL_PROPOSAL' ORDER BY id"""
        ).fetchall()
    assert rows == [
        ("PROPOSED", REASON),
        ("APPROVED", REASON),
        ("REVOKED", REASON),
        ("PROPOSED", REASON),
        ("APPROVED", REASON),
    ]


def test_manual_revocation_is_source_scoped_and_works_after_stale_rescan(tmp_path: Path) -> None:
    _save_fixture(tmp_path)
    other = _source(tmp_path, "other")
    _save(other, tmp_path)
    source, target = _display(SOURCE), _display(TARGET)
    with pytest.raises(QueryError) as absent:
        revoke_manual_proposal(tmp_path, "erp", source, target, 1)
    assert absent.value.code == "MANUAL_APPROVAL_NOT_FOUND"
    propose_relationship(tmp_path, "erp", source, target, 1, REASON)
    approve_manual_proposal(tmp_path, "erp", source, target, 1)
    for alias, version, code in (
        ("other", 1, "MANUAL_APPROVAL_NOT_FOUND"),
        ("erp", 2, "SNAPSHOT_CHANGED"),
    ):
        with pytest.raises(QueryError) as invalid:
            revoke_manual_proposal(tmp_path, alias, source, target, version)
        assert invalid.value.code == code

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
    assert list_proposals(tmp_path, "erp").proposals[0].eligibility == "SOURCE_COLUMN_MISSING"
    revoked = revoke_manual_proposal(tmp_path, "erp", source, target, 2)
    assert (revoked.status, revoked.changed) == ("REVOKED", True)
    assert list_proposals(tmp_path, "erp").proposals[0].status == "REVOKED"
