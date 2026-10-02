"""Local review decisions for inferred, never database-confirmed, pairs."""

import json
import sqlite3
from pathlib import Path

import pytest
from pytest import MonkeyPatch
from typer.testing import CliRunner

import graphit.inference as inference
import graphit.review as review
from graphit.cli import app
from graphit.inference import CandidatePreview, candidate_review_keys, preview_candidates
from graphit.project import initialize_project
from graphit.queries import QueryError, table_relationships
from graphit.scanners.protocol import (
    ColumnMetadata,
    ForeignKeyMetadata,
    KeyConstraintMetadata,
    MetadataSnapshot,
    SchemaMetadata,
    TableMetadata,
)
from graphit.snapshots import persist_snapshot
from graphit.sources import SourceConfig, add_source

SOURCE_COLUMN = '"public"."invoice"."customer_id"'
TARGET_COLUMN = '"public"."customer"."id"'


def _source(root: Path, name: str = "erp", schemas: tuple[str, ...] = ("public",)) -> SourceConfig:
    source = SourceConfig(
        name=name,
        host="localhost",
        port=5432,
        database_name=name,
        username="reader",
        credential_env="ERP_PASSWORD",
        schemas=schemas,
    )
    add_source(root, source)
    return source


def _save(
    source: SourceConfig, root: Path, *, declared_fk: bool = False, ambiguous: bool = False
) -> None:
    persist_snapshot(
        root,
        source,
        MetadataSnapshot(
            source_name=source.name,
            schemas=(SchemaMetadata("public"),) + ((SchemaMetadata("sales"),) if ambiguous else ()),
            tables=(
                TableMetadata("public", "customer", False),
                TableMetadata("public", "invoice", False),
            )
            + ((TableMetadata("sales", "customer", False),) if ambiguous else ()),
            columns=(
                ColumnMetadata("public", "customer", "id", 1, "integer", False),
                ColumnMetadata("public", "invoice", "customer_id", 1, "integer", False),
            )
            + (
                (ColumnMetadata("sales", "customer", "id", 1, "integer", False),)
                if ambiguous
                else ()
            ),
            keys=(
                KeyConstraintMetadata(
                    "public", "customer", "customer_pkey", "PRIMARY_KEY", ("id",), False
                ),
            )
            + (
                (
                    KeyConstraintMetadata(
                        "sales", "customer", "customer_pkey", "PRIMARY_KEY", ("id",), False
                    ),
                )
                if ambiguous
                else ()
            ),
            foreign_keys=(
                (
                    ForeignKeyMetadata(
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
                    ),
                )
                if declared_fk
                else ()
            ),
        ),
    )


def test_reject_is_idempotent_source_scoped_and_survives_rescan(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    initialize_project(tmp_path)
    erp = _source(tmp_path)
    other = _source(tmp_path, "other")
    _save(erp, tmp_path)
    _save(other, tmp_path)

    def reject_source_connection(*args: object, **kwargs: object) -> None:
        raise AssertionError("Review must not connect to PostgreSQL.")

    monkeypatch.setattr("psycopg.connect", reject_source_connection)
    result = review.reject_candidate(tmp_path, "erp", SOURCE_COLUMN, TARGET_COLUMN, 1)
    again = review.reject_candidate(tmp_path, "erp", SOURCE_COLUMN, TARGET_COLUMN, 1)
    assert (result.decision, result.changed, again.changed) == ("REJECTED", True, False)
    assert preview_candidates(tmp_path, "erp").candidates == ()
    assert len(preview_candidates(tmp_path, "other").candidates) == 1
    _save(erp, tmp_path)
    assert preview_candidates(tmp_path, "erp").snapshot_version == 2
    assert preview_candidates(tmp_path, "erp").candidates == ()

    with sqlite3.connect(tmp_path / ".graphit" / "graphit.db") as connection:
        rows = connection.execute(
            "SELECT source_logical_key, target_logical_key, decision FROM review_decisions"
        ).fetchall()
        inferred_edges = connection.execute(
            "SELECT COUNT(*) FROM edges WHERE edge_type = 'LIKELY_REFERENCES'"
        ).fetchone()[0]
    assert len(rows) == 1
    assert rows[0][0].startswith("erp:postgres:column:")
    assert rows[0][1].startswith("erp:postgres:column:")
    assert rows[0][2] == "REJECTED"
    assert inferred_edges == 0


def test_reject_requires_current_exact_candidate_and_snapshot(tmp_path: Path) -> None:
    initialize_project(tmp_path)
    source = _source(tmp_path)
    _save(source, tmp_path)
    with pytest.raises(QueryError) as missing:
        review.reject_candidate(tmp_path, "erp", SOURCE_COLUMN, '"public"."other"."id"', 1)
    assert missing.value.code == "CANDIDATE_NOT_FOUND"
    with pytest.raises(QueryError) as invalid:
        review.reject_candidate(tmp_path, "erp", SOURCE_COLUMN, TARGET_COLUMN, 0)
    assert invalid.value.code == "INVALID_SNAPSHOT_VERSION"
    with pytest.raises(QueryError) as stale:
        review.reject_candidate(tmp_path, "erp", SOURCE_COLUMN, TARGET_COLUMN, 2)
    assert stale.value.code == "SNAPSHOT_CHANGED"

    _save(source, tmp_path, declared_fk=True)
    with pytest.raises(QueryError) as declared:
        review.reject_candidate(tmp_path, "erp", SOURCE_COLUMN, TARGET_COLUMN, 2)
    assert declared.value.code == "CANDIDATE_NOT_FOUND"


def test_reject_one_ambiguous_target_reveals_the_other(tmp_path: Path) -> None:
    initialize_project(tmp_path)
    source = _source(tmp_path, schemas=("public", "sales"))
    _save(source, tmp_path, ambiguous=True)
    assert preview_candidates(tmp_path, "erp").skipped_ambiguous_columns == 1
    assert len(preview_candidates(tmp_path, "erp", include_ambiguous=True).candidates) == 2

    review.reject_candidate(tmp_path, "erp", SOURCE_COLUMN, '"sales"."customer"."id"', 1)
    remaining = preview_candidates(tmp_path, "erp")
    assert remaining.skipped_ambiguous_columns == 0
    assert len(remaining.candidates) == 1
    assert remaining.candidates[0].target_column == TARGET_COLUMN
    assert remaining.candidates[0].alternatives_for_source == 1


@pytest.mark.parametrize("decision", ("reject", "approve"))
def test_current_review_detects_rescan_between_preview_and_write(
    tmp_path: Path, monkeypatch: MonkeyPatch, decision: str
) -> None:
    initialize_project(tmp_path)
    source = _source(tmp_path)
    _save(source, tmp_path)
    original = preview_candidates

    def rescan_after_preview(
        root: Path,
        source_name: str,
        limit: int,
        *,
        include_ambiguous: bool,
        pair: tuple[str, str],
        include_rejected: bool,
    ) -> CandidatePreview:
        result = original(
            root,
            source_name,
            limit,
            include_ambiguous=include_ambiguous,
            pair=pair,
            include_rejected=include_rejected,
        )
        _save(source, tmp_path)
        return result

    monkeypatch.setattr(review, "preview_candidates", rescan_after_preview)
    action = review.reject_candidate if decision == "reject" else review.approve_candidate
    with pytest.raises(QueryError) as stale:
        action(tmp_path, "erp", SOURCE_COLUMN, TARGET_COLUMN, 1)
    assert stale.value.code == "SNAPSHOT_CHANGED"
    with sqlite3.connect(tmp_path / ".graphit" / "graphit.db") as connection:
        assert connection.execute("SELECT COUNT(*) FROM review_decisions").fetchone()[0] == 0


def test_exact_review_lookup_still_honors_inference_work_budget(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    initialize_project(tmp_path)
    source = _source(tmp_path)
    _save(source, tmp_path)
    monkeypatch.setattr(inference, "MAX_PREVIEW_PAIRS", 0)
    with pytest.raises(QueryError) as budget:
        review.reject_candidate(tmp_path, "erp", SOURCE_COLUMN, TARGET_COLUMN, 1)
    assert budget.value.code == "INFERENCE_BUDGET_EXCEEDED"
    with sqlite3.connect(tmp_path / ".graphit" / "graphit.db") as connection:
        assert connection.execute("SELECT COUNT(*) FROM review_decisions").fetchone()[0] == 0


def test_restore_preserves_history_and_can_be_rejected_again(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    initialize_project(tmp_path)
    source = _source(tmp_path)
    _save(source, tmp_path)

    def reject_source_connection(*args: object, **kwargs: object) -> None:
        raise AssertionError("Review and preview must stay local.")

    monkeypatch.setattr("psycopg.connect", reject_source_connection)
    review.reject_candidate(tmp_path, "erp", SOURCE_COLUMN, TARGET_COLUMN, 1)
    restored = review.restore_candidate(tmp_path, "erp", SOURCE_COLUMN, TARGET_COLUMN, 1)
    repeated = review.restore_candidate(tmp_path, "erp", SOURCE_COLUMN, TARGET_COLUMN, 1)
    assert (restored.decision, restored.changed, repeated.changed) == (
        "RESTORED",
        True,
        False,
    )
    assert len(preview_candidates(tmp_path, "erp").candidates) == 1
    with sqlite3.connect(tmp_path / ".graphit" / "graphit.db") as connection:
        decisions = connection.execute(
            "SELECT decision FROM review_decisions ORDER BY id"
        ).fetchall()
    assert decisions == [("REJECTED",), ("RESTORED",)]

    re_rejected = review.reject_candidate(tmp_path, "erp", SOURCE_COLUMN, TARGET_COLUMN, 1)
    assert re_rejected.changed is True
    assert preview_candidates(tmp_path, "erp").candidates == ()
    with sqlite3.connect(tmp_path / ".graphit" / "graphit.db") as connection:
        decisions = connection.execute(
            "SELECT decision FROM review_decisions ORDER BY id"
        ).fetchall()
    assert decisions == [("REJECTED",), ("RESTORED",), ("REJECTED",)]


def test_restore_requires_existing_source_scoped_rejection_and_current_version(
    tmp_path: Path,
) -> None:
    initialize_project(tmp_path)
    erp = _source(tmp_path)
    other = _source(tmp_path, "other")
    _save(erp, tmp_path)
    _save(other, tmp_path)

    with pytest.raises(QueryError) as absent:
        review.restore_candidate(tmp_path, "erp", SOURCE_COLUMN, TARGET_COLUMN, 1)
    assert absent.value.code == "REJECTION_NOT_FOUND"
    review.reject_candidate(tmp_path, "erp", SOURCE_COLUMN, TARGET_COLUMN, 1)
    with pytest.raises(QueryError) as different_source:
        review.restore_candidate(tmp_path, "other", SOURCE_COLUMN, TARGET_COLUMN, 1)
    assert different_source.value.code == "REJECTION_NOT_FOUND"
    with pytest.raises(QueryError) as invalid:
        review.restore_candidate(tmp_path, "erp", SOURCE_COLUMN, TARGET_COLUMN, 0)
    assert invalid.value.code == "INVALID_SNAPSHOT_VERSION"

    _save(erp, tmp_path, declared_fk=True)
    with pytest.raises(QueryError) as stale:
        review.restore_candidate(tmp_path, "erp", SOURCE_COLUMN, TARGET_COLUMN, 1)
    assert stale.value.code == "SNAPSHOT_CHANGED"
    restored = review.restore_candidate(tmp_path, "erp", SOURCE_COLUMN, TARGET_COLUMN, 2)
    assert restored.changed is True
    assert preview_candidates(tmp_path, "erp").candidates == ()


def test_restore_reintroduces_ambiguity_after_rejecting_alternative(tmp_path: Path) -> None:
    initialize_project(tmp_path)
    source = _source(tmp_path, schemas=("public", "sales"))
    _save(source, tmp_path, ambiguous=True)
    rejected_target = '"sales"."customer"."id"'
    review.reject_candidate(tmp_path, "erp", SOURCE_COLUMN, rejected_target, 1)
    assert len(preview_candidates(tmp_path, "erp").candidates) == 1
    review.restore_candidate(tmp_path, "erp", SOURCE_COLUMN, rejected_target, 1)
    default = preview_candidates(tmp_path, "erp")
    assert default.candidates == ()
    assert default.skipped_ambiguous_columns == 1
    assert len(preview_candidates(tmp_path, "erp", include_ambiguous=True).candidates) == 2


def test_approval_is_human_logical_status_not_database_fk(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    initialize_project(tmp_path)
    erp = _source(tmp_path)
    other = _source(tmp_path, "other")
    _save(erp, tmp_path)
    _save(other, tmp_path)

    def reject_source_connection(*args: object, **kwargs: object) -> None:
        raise AssertionError("Approval and preview must stay local.")

    monkeypatch.setattr("psycopg.connect", reject_source_connection)
    first = review.approve_candidate(tmp_path, "erp", SOURCE_COLUMN, TARGET_COLUMN, 1)
    repeated = review.approve_candidate(tmp_path, "erp", SOURCE_COLUMN, TARGET_COLUMN, 1)
    assert (first.decision, first.changed, repeated.changed) == ("APPROVED", True, False)
    candidate = preview_candidates(tmp_path, "erp").candidates[0]
    assert (candidate.origin, candidate.status, candidate.confidence) == (
        "INFERRED",
        "APPROVED",
        0.8,
    )
    assert preview_candidates(tmp_path, "other").candidates[0].status == "PENDING"
    declared_context = table_relationships(tmp_path, "erp", "public.invoice")
    assert declared_context.incoming == declared_context.outgoing == ()
    _save(erp, tmp_path)
    assert preview_candidates(tmp_path, "erp").candidates[0].status == "APPROVED"

    with sqlite3.connect(tmp_path / ".graphit" / "graphit.db") as connection:
        assert connection.execute(
            "SELECT decision FROM review_decisions ORDER BY id"
        ).fetchall() == [("APPROVED",)]
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM edges WHERE edge_type = 'LIKELY_REFERENCES'"
            ).fetchone()[0]
            == 0
        )


def test_approval_of_one_ambiguous_target_keeps_other_pending_in_diagnostics(
    tmp_path: Path,
) -> None:
    initialize_project(tmp_path)
    source = _source(tmp_path, schemas=("public", "sales"))
    _save(source, tmp_path, ambiguous=True)
    assert preview_candidates(tmp_path, "erp").skipped_ambiguous_columns == 1

    approved_target = '"sales"."customer"."id"'
    review.approve_candidate(tmp_path, "erp", SOURCE_COLUMN, approved_target, 1)
    default = preview_candidates(tmp_path, "erp")
    assert len(default.candidates) == 1
    assert default.candidates[0].target_column == approved_target
    assert default.candidates[0].status == "APPROVED"
    assert default.candidates[0].origin == "INFERRED"
    assert default.candidates[0].alternatives_for_source == 2

    diagnostic = preview_candidates(tmp_path, "erp", include_ambiguous=True)
    assert [(item.target_column, item.status) for item in diagnostic.candidates] == [
        (approved_target, "APPROVED"),
        (TARGET_COLUMN, "PENDING"),
    ]


def test_approval_requires_current_candidate_and_nonconflicting_review(tmp_path: Path) -> None:
    initialize_project(tmp_path)
    source = _source(tmp_path)
    _save(source, tmp_path)
    with pytest.raises(QueryError) as missing:
        review.approve_candidate(tmp_path, "erp", SOURCE_COLUMN, '"public"."other"."id"', 1)
    assert missing.value.code == "CANDIDATE_NOT_FOUND"
    with pytest.raises(QueryError) as stale:
        review.approve_candidate(tmp_path, "erp", SOURCE_COLUMN, TARGET_COLUMN, 2)
    assert stale.value.code == "SNAPSHOT_CHANGED"

    review.reject_candidate(tmp_path, "erp", SOURCE_COLUMN, TARGET_COLUMN, 1)
    with pytest.raises(QueryError) as conflict:
        review.approve_candidate(tmp_path, "erp", SOURCE_COLUMN, TARGET_COLUMN, 1)
    assert conflict.value.code == "REVIEW_CONFLICT"
    review.restore_candidate(tmp_path, "erp", SOURCE_COLUMN, TARGET_COLUMN, 1)
    assert review.approve_candidate(tmp_path, "erp", SOURCE_COLUMN, TARGET_COLUMN, 1).changed
    with pytest.raises(QueryError) as reject_approved:
        review.reject_candidate(tmp_path, "erp", SOURCE_COLUMN, TARGET_COLUMN, 1)
    assert reject_approved.value.code == "REVIEW_CONFLICT"

    _save(source, tmp_path, declared_fk=True)
    with pytest.raises(QueryError) as declared:
        review.approve_candidate(tmp_path, "erp", SOURCE_COLUMN, TARGET_COLUMN, 2)
    assert declared.value.code == "CANDIDATE_NOT_FOUND"


def test_unknown_review_state_fails_preview_closed(tmp_path: Path) -> None:
    initialize_project(tmp_path)
    source = _source(tmp_path)
    _save(source, tmp_path)
    source_key, target_key = candidate_review_keys("erp", SOURCE_COLUMN, TARGET_COLUMN)
    with sqlite3.connect(tmp_path / ".graphit" / "graphit.db") as connection:
        connection.execute(
            """INSERT INTO review_decisions
            (source_logical_key, target_logical_key, relationship_kind, decision)
            VALUES (?, ?, 'LIKELY_REFERENCES', 'UNKNOWN')""",
            (source_key, target_key),
        )
    with pytest.raises(QueryError) as invalid:
        preview_candidates(tmp_path, "erp")
    assert invalid.value.code == "STORE_READ_FAILED"


def test_revoke_approval_is_append_only_idempotent_and_source_scoped(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    initialize_project(tmp_path)
    erp = _source(tmp_path)
    other = _source(tmp_path, "other")
    _save(erp, tmp_path)
    _save(other, tmp_path)

    def reject_source_connection(*args: object, **kwargs: object) -> None:
        raise AssertionError("Review must not connect to PostgreSQL.")

    monkeypatch.setattr("psycopg.connect", reject_source_connection)
    review.approve_candidate(tmp_path, "erp", SOURCE_COLUMN, TARGET_COLUMN, 1)
    revoked = review.revoke_approval(tmp_path, "erp", SOURCE_COLUMN, TARGET_COLUMN, 1)
    repeated = review.revoke_approval(tmp_path, "erp", SOURCE_COLUMN, TARGET_COLUMN, 1)
    assert (revoked.decision, revoked.changed, repeated.changed) == (
        "REVOKED",
        True,
        False,
    )
    assert preview_candidates(tmp_path, "erp").candidates[0].status == "PENDING"
    assert preview_candidates(tmp_path, "other").candidates[0].status == "PENDING"

    approved_again = review.approve_candidate(tmp_path, "erp", SOURCE_COLUMN, TARGET_COLUMN, 1)
    assert approved_again.changed is True
    assert preview_candidates(tmp_path, "erp").candidates[0].status == "APPROVED"
    with sqlite3.connect(tmp_path / ".graphit" / "graphit.db") as connection:
        decisions = connection.execute(
            "SELECT decision FROM review_decisions ORDER BY id"
        ).fetchall()
        inferred_edges = connection.execute(
            "SELECT COUNT(*) FROM edges WHERE edge_type = 'LIKELY_REFERENCES'"
        ).fetchone()[0]
    assert decisions == [("APPROVED",), ("REVOKED",), ("APPROVED",)]
    assert inferred_edges == 0


def test_revoke_requires_prior_approval_and_current_snapshot(tmp_path: Path) -> None:
    initialize_project(tmp_path)
    erp = _source(tmp_path)
    other = _source(tmp_path, "other")
    _save(erp, tmp_path)
    _save(other, tmp_path)
    with pytest.raises(QueryError) as absent:
        review.revoke_approval(tmp_path, "erp", SOURCE_COLUMN, TARGET_COLUMN, 1)
    assert absent.value.code == "APPROVAL_NOT_FOUND"
    review.approve_candidate(tmp_path, "erp", SOURCE_COLUMN, TARGET_COLUMN, 1)
    with pytest.raises(QueryError) as different_source:
        review.revoke_approval(tmp_path, "other", SOURCE_COLUMN, TARGET_COLUMN, 1)
    assert different_source.value.code == "APPROVAL_NOT_FOUND"
    with pytest.raises(QueryError) as invalid:
        review.revoke_approval(tmp_path, "erp", SOURCE_COLUMN, TARGET_COLUMN, 0)
    assert invalid.value.code == "INVALID_SNAPSHOT_VERSION"

    _save(erp, tmp_path, declared_fk=True)
    with pytest.raises(QueryError) as stale:
        review.revoke_approval(tmp_path, "erp", SOURCE_COLUMN, TARGET_COLUMN, 1)
    assert stale.value.code == "SNAPSHOT_CHANGED"
    revoked = review.revoke_approval(tmp_path, "erp", SOURCE_COLUMN, TARGET_COLUMN, 2)
    assert revoked.changed is True
    assert preview_candidates(tmp_path, "erp").candidates == ()
    with pytest.raises(QueryError) as wrong_action:
        review.restore_candidate(tmp_path, "erp", SOURCE_COLUMN, TARGET_COLUMN, 2)
    assert wrong_action.value.code == "REVIEW_CONFLICT"


def test_revoking_ambiguous_approval_restores_default_abstention(tmp_path: Path) -> None:
    initialize_project(tmp_path)
    source = _source(tmp_path, schemas=("public", "sales"))
    _save(source, tmp_path, ambiguous=True)
    approved_target = '"sales"."customer"."id"'
    review.approve_candidate(tmp_path, "erp", SOURCE_COLUMN, approved_target, 1)
    assert preview_candidates(tmp_path, "erp").candidates[0].status == "APPROVED"
    review.revoke_approval(tmp_path, "erp", SOURCE_COLUMN, approved_target, 1)
    default = preview_candidates(tmp_path, "erp")
    assert default.candidates == ()
    assert default.skipped_ambiguous_columns == 1
    diagnostic = preview_candidates(tmp_path, "erp", include_ambiguous=True)
    assert len(diagnostic.candidates) == 2
    assert {item.status for item in diagnostic.candidates} == {"PENDING"}


def test_cli_reject_json_and_stale_error(tmp_path: Path) -> None:
    initialize_project(tmp_path)
    source = _source(tmp_path)
    _save(source, tmp_path)
    runner = CliRunner()
    args = [
        "review",
        "reject",
        SOURCE_COLUMN,
        TARGET_COLUMN,
        "--source",
        "erp",
        "--snapshot-version",
        "1",
        "--project",
        str(tmp_path),
        "--json",
    ]
    result = runner.invoke(app, args)
    assert result.exit_code == 0
    assert json.loads(result.stdout)["decision"] == "REJECTED"
    assert json.loads(result.stdout)["changed"] is True
    assert json.loads(runner.invoke(app, args).stdout)["changed"] is False
    _save(source, tmp_path)
    stale = runner.invoke(app, args)
    assert stale.exit_code == 6
    assert "SNAPSHOT_CHANGED" in stale.stderr


def test_cli_restore_json_and_missing_decision_error(tmp_path: Path) -> None:
    initialize_project(tmp_path)
    source = _source(tmp_path)
    _save(source, tmp_path)
    runner = CliRunner()
    args = [
        "review",
        "restore",
        SOURCE_COLUMN,
        TARGET_COLUMN,
        "--source",
        "erp",
        "--snapshot-version",
        "1",
        "--project",
        str(tmp_path),
        "--json",
    ]
    missing = runner.invoke(app, args)
    assert missing.exit_code == 6
    assert "REJECTION_NOT_FOUND" in missing.stderr
    review.reject_candidate(tmp_path, "erp", SOURCE_COLUMN, TARGET_COLUMN, 1)
    restored = runner.invoke(app, args)
    assert restored.exit_code == 0
    assert json.loads(restored.stdout)["decision"] == "RESTORED"
    assert json.loads(restored.stdout)["changed"] is True
    assert json.loads(runner.invoke(app, args).stdout)["changed"] is False


def test_cli_approve_status_and_declared_fk_warning(tmp_path: Path) -> None:
    initialize_project(tmp_path)
    source = _source(tmp_path)
    _save(source, tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "review",
            "approve",
            SOURCE_COLUMN,
            TARGET_COLUMN,
            "--source",
            "erp",
            "--snapshot-version",
            "1",
            "--project",
            str(tmp_path),
            "--json",
        ],
    )
    assert result.exit_code == 0
    assert json.loads(result.stdout)["decision"] == "APPROVED"
    human = runner.invoke(app, ["candidates", "--source", "erp", "--project", str(tmp_path)])
    assert human.exit_code == 0
    assert "APPROVED 0.80" in human.stdout
    assert "not a declared FK" in human.stdout
    machine = runner.invoke(
        app, ["candidates", "--source", "erp", "--project", str(tmp_path), "--json"]
    )
    assert json.loads(machine.stdout)["candidates"][0]["origin"] == "INFERRED"
    assert json.loads(machine.stdout)["candidates"][0]["status"] == "APPROVED"


def test_cli_revoke_json_and_missing_approval_error(tmp_path: Path) -> None:
    initialize_project(tmp_path)
    source = _source(tmp_path)
    _save(source, tmp_path)
    runner = CliRunner()
    args = [
        "review",
        "revoke",
        SOURCE_COLUMN,
        TARGET_COLUMN,
        "--source",
        "erp",
        "--snapshot-version",
        "1",
        "--project",
        str(tmp_path),
        "--json",
    ]
    missing = runner.invoke(app, args)
    assert missing.exit_code == 6
    assert "APPROVAL_NOT_FOUND" in missing.stderr
    review.approve_candidate(tmp_path, "erp", SOURCE_COLUMN, TARGET_COLUMN, 1)
    revoked = runner.invoke(app, args)
    assert revoked.exit_code == 0
    assert json.loads(revoked.stdout)["decision"] == "REVOKED"
    assert json.loads(revoked.stdout)["changed"] is True
    assert json.loads(runner.invoke(app, args).stdout)["changed"] is False
