"""Conservative metadata-only relationship candidate preview."""

import json
import sqlite3
from pathlib import Path

import pytest
from pytest import MonkeyPatch
from typer.testing import CliRunner

import graphit.inference as inference
from graphit.cli import app
from graphit.inference import preview_candidates
from graphit.project import initialize_project
from graphit.queries import QueryError
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

runner = CliRunner()


def _project(root: Path) -> SourceConfig:
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
    return source


def _save(
    root: Path,
    source: SourceConfig,
    columns: tuple[ColumnMetadata, ...],
    keys: tuple[KeyConstraintMetadata, ...],
    foreign_keys: tuple[ForeignKeyMetadata, ...] = (),
) -> None:
    table_names = sorted({(column.schema_name, column.table_name) for column in columns})
    schemas = sorted({schema for schema, _ in table_names})
    persist_snapshot(
        root,
        source,
        MetadataSnapshot(
            source_name="erp",
            schemas=tuple(SchemaMetadata(name) for name in schemas),
            tables=tuple(TableMetadata(schema, table, False) for schema, table in table_names),
            columns=columns,
            keys=keys,
            foreign_keys=foreign_keys,
        ),
    )


def _base_columns() -> tuple[ColumnMetadata, ...]:
    return (
        ColumnMetadata("public", "customer", "id", 1, "integer", False),
        ColumnMetadata("public", "invoice", "customer_id", 1, "integer", False),
        ColumnMetadata("public", "invoice", "amount", 2, "numeric", False),
    )


def _customer_key() -> KeyConstraintMetadata:
    return KeyConstraintMetadata(
        "public", "customer", "customer_pkey", "PRIMARY_KEY", ("id",), False
    )


def test_exact_key_candidate_has_evidence_and_does_not_write_edge(tmp_path: Path) -> None:
    source = _project(tmp_path)
    _save(tmp_path, source, _base_columns(), (_customer_key(),))
    store = tmp_path / ".graphit" / "graphit.db"
    with sqlite3.connect(store) as connection:
        before = connection.execute("SELECT COUNT(*) FROM edges").fetchone()[0]

    result = preview_candidates(tmp_path, "erp")

    assert result.snapshot_version == 1
    assert result.scoring_method == "metadata_exact_key_v3"
    assert result.include_ambiguous is False
    assert result.truncated is False
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.source_column == '"public"."invoice"."customer_id"'
    assert candidate.target_column == '"public"."customer"."id"'
    assert (candidate.origin, candidate.status, candidate.confidence) == (
        "INFERRED",
        "PENDING",
        0.8,
    )
    assert [item.signal for item in candidate.evidence] == [
        "EXACT_TABLE_STEM",
        "EXACT_KEY_TYPE",
        "TARGET_UNIQUE",
        "SAME_SCHEMA",
    ]
    with sqlite3.connect(store) as connection:
        assert connection.execute("SELECT COUNT(*) FROM edges").fetchone()[0] == before


def test_declared_fk_is_excluded_and_latest_snapshot_wins(tmp_path: Path) -> None:
    source = _project(tmp_path)
    fk = ForeignKeyMetadata(
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
    _save(tmp_path, source, _base_columns(), (_customer_key(),), (fk,))
    assert preview_candidates(tmp_path, "erp").candidates == ()

    _save(tmp_path, source, _base_columns(), (_customer_key(),))
    current = preview_candidates(tmp_path, "erp")

    assert current.snapshot_version == 2
    assert len(current.candidates) == 1


def test_external_single_and_composite_fks_suppress_only_their_source_columns(
    tmp_path: Path,
) -> None:
    source = _project(tmp_path)
    columns = (
        ColumnMetadata("public", "customer", "id", 1, "integer", False),
        ColumnMetadata("public", "product", "id", 1, "integer", False),
        ColumnMetadata("public", "supplier", "id", 1, "integer", False),
        ColumnMetadata("public", "invoice", "customer_id", 1, "integer", False),
        ColumnMetadata("public", "invoice", "product_id", 2, "integer", False),
        ColumnMetadata("public", "invoice", "region_id", 3, "integer", False),
        ColumnMetadata("public", "invoice", "supplier_id", 4, "integer", False),
    )
    keys = (
        _customer_key(),
        KeyConstraintMetadata("public", "product", "product_pkey", "PRIMARY_KEY", ("id",), False),
        KeyConstraintMetadata("public", "supplier", "supplier_pkey", "PRIMARY_KEY", ("id",), False),
    )
    foreign_keys = (
        ForeignKeyMetadata(
            "public",
            "invoice",
            "invoice_external_customer_fk",
            ("customer_id",),
            "external",
            "customer",
            ("id",),
            False,
            True,
            False,
        ),
        ForeignKeyMetadata(
            "public",
            "invoice",
            "invoice_external_product_fk",
            ("product_id", "region_id"),
            "external",
            "product",
            ("id", "region_id"),
            False,
            True,
            False,
        ),
    )
    _save(tmp_path, source, columns, keys, foreign_keys)

    result = preview_candidates(tmp_path, "erp")

    assert len(result.candidates) == 1
    assert result.candidates[0].source_column.endswith('"supplier_id"')
    assert result.candidates[0].target_column.endswith('"supplier"."id"')


def test_declared_fk_work_cap_and_invalid_pair_metadata_fail_closed(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    source = _project(tmp_path)
    fk = ForeignKeyMetadata(
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
    _save(tmp_path, source, _base_columns(), (_customer_key(),), (fk,))
    monkeypatch.setattr(inference, "MAX_PREVIEW_FK_EDGES", 0)
    with pytest.raises(QueryError) as budget:
        preview_candidates(tmp_path, "erp")
    assert budget.value.code == "INFERENCE_BUDGET_EXCEEDED"

    monkeypatch.setattr(inference, "MAX_PREVIEW_FK_EDGES", 20000)
    with sqlite3.connect(tmp_path / ".graphit" / "graphit.db") as connection:
        connection.execute(
            """UPDATE edges SET metadata_json = '{"level":"TABLE","column_pairs":[]}'
            WHERE edge_type = 'REFERENCES' AND source_object_id IN
              (SELECT id FROM objects WHERE object_type = 'TABLE')"""
        )
    with pytest.raises(QueryError) as invalid:
        preview_candidates(tmp_path, "erp")
    assert invalid.value.code == "STORE_READ_FAILED"


def test_type_key_and_name_filters_prevent_false_positives(tmp_path: Path) -> None:
    source = _project(tmp_path)
    columns = _base_columns() + (
        ColumnMetadata("public", "invoice", "customer_uuid_id", 3, "uuid", False),
        ColumnMetadata("public", "invoice", "order_id", 4, "bigint", False),
        ColumnMetadata("public", "invoice", "customerid", 5, "integer", False),
        ColumnMetadata("public", "order", "id", 1, "bigint", False),
        ColumnMetadata("public", "customer_uuid", "id", 1, "integer", False),
    )
    _save(tmp_path, source, columns, (_customer_key(),))

    result = preview_candidates(tmp_path, "erp")

    assert len(result.candidates) == 1
    assert result.candidates[0].source_column.endswith('"customer_id"')


def test_ambiguous_targets_are_labeled_and_over_cap_are_skipped(tmp_path: Path) -> None:
    source = _project(tmp_path)
    columns = (
        ColumnMetadata("public", "invoice", "customer_id", 1, "integer", False),
        ColumnMetadata("public", "customer", "id", 1, "integer", False),
        ColumnMetadata("sales", "customer", "id", 1, "integer", False),
    )
    keys = (
        _customer_key(),
        KeyConstraintMetadata("sales", "customer", "customer_pkey", "PRIMARY_KEY", ("id",), False),
    )
    _save(tmp_path, source, columns, keys)

    result = preview_candidates(tmp_path, "erp", limit=1)
    assert result.candidates == ()
    assert result.truncated is False
    assert result.skipped_ambiguous_columns == 1

    inspected = preview_candidates(tmp_path, "erp", limit=1, include_ambiguous=True)
    assert len(inspected.candidates) == 1
    assert inspected.truncated is True
    assert inspected.include_ambiguous is True
    assert inspected.candidates[0].confidence == 0.8
    assert inspected.candidates[0].alternatives_for_source == 2
    assert (
        preview_candidates(tmp_path, "erp", limit=2, include_ambiguous=True)
        .candidates[1]
        .confidence
        == 0.75
    )

    default_cli = runner.invoke(
        app, ["candidates", "--source", "erp", "--json", "--project", str(tmp_path)]
    )
    diagnostic_cli = runner.invoke(
        app,
        [
            "candidates",
            "--source",
            "erp",
            "--include-ambiguous",
            "--json",
            "--project",
            str(tmp_path),
        ],
    )
    assert default_cli.exit_code == diagnostic_cli.exit_code == 0
    assert json.loads(default_cli.stdout)["candidates"] == []
    assert len(json.loads(diagnostic_cli.stdout)["candidates"]) == 2

    extra_schemas = ("other", "archive")
    more_columns = columns + tuple(
        ColumnMetadata(schema, "customer", "id", 1, "integer", False) for schema in extra_schemas
    )
    more_keys = keys + tuple(
        KeyConstraintMetadata(schema, "customer", "customer_pkey", "PRIMARY_KEY", ("id",), False)
        for schema in extra_schemas
    )
    _save(tmp_path, source, more_columns, more_keys)
    too_ambiguous = preview_candidates(tmp_path, "erp", include_ambiguous=True)
    assert too_ambiguous.candidates == ()
    assert too_ambiguous.skipped_ambiguous_columns == 1


def test_missing_snapshot_invalid_limit_and_work_budget(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    source = _project(tmp_path)
    with pytest.raises(QueryError, match="no successful scan") as no_snapshot:
        preview_candidates(tmp_path, "erp")
    assert no_snapshot.value.code == "NO_SNAPSHOT"
    with pytest.raises(QueryError) as invalid:
        preview_candidates(tmp_path, "erp", limit=0)
    assert invalid.value.code == "INVALID_LIMIT"

    _save(tmp_path, source, _base_columns(), (_customer_key(),))
    monkeypatch.setattr(inference, "MAX_PREVIEW_TABLES", 1)
    with pytest.raises(QueryError) as budget:
        preview_candidates(tmp_path, "erp")
    assert budget.value.code == "INFERENCE_BUDGET_EXCEEDED"
    monkeypatch.setattr(inference, "MAX_PREVIEW_TABLES", 2000)
    monkeypatch.setattr(inference, "MAX_PREVIEW_COLUMNS", 1)
    with pytest.raises(QueryError) as column_budget:
        preview_candidates(tmp_path, "erp")
    assert column_budget.value.code == "INFERENCE_BUDGET_EXCEEDED"

    monkeypatch.setattr(inference, "MAX_PREVIEW_COLUMNS", 20000)
    monkeypatch.setattr(inference, "MAX_PREVIEW_PAIRS", 0)
    with pytest.raises(QueryError) as pair_budget:
        preview_candidates(tmp_path, "erp")
    assert pair_budget.value.code == "INFERENCE_BUDGET_EXCEEDED"


def test_cli_preview_human_json_and_errors(tmp_path: Path) -> None:
    source = _project(tmp_path)
    no_snapshot = runner.invoke(app, ["candidates", "--source", "erp", "--project", str(tmp_path)])
    assert no_snapshot.exit_code == 6
    assert "NO_SNAPSHOT" in no_snapshot.stderr
    _save(tmp_path, source, _base_columns(), (_customer_key(),))

    human = runner.invoke(app, ["candidates", "--source", "erp", "--project", str(tmp_path)])
    machine = runner.invoke(
        app, ["candidates", "--source", "erp", "--json", "--project", str(tmp_path)]
    )

    assert human.exit_code == machine.exit_code == 0
    assert "PENDING 0.80" in human.stdout
    payload = json.loads(machine.stdout)
    assert payload["candidates"][0]["origin"] == "INFERRED"
    assert payload["candidates"][0]["evidence"][0]["signal"] == "EXACT_TABLE_STEM"
