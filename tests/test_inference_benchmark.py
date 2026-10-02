"""Fixed ERP-like challenge set for the metadata-only inference baseline."""

from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import pytest

from graphit.inference import preview_candidates
from graphit.project import initialize_project
from graphit.scanners.protocol import (
    ColumnMetadata,
    ForeignKeyMetadata,
    KeyConstraintMetadata,
    MetadataSnapshot,
    SchemaMetadata,
    TableMetadata,
    quote_identifier,
)
from graphit.snapshots import persist_snapshot
from graphit.sources import SourceConfig, add_source

ColumnRef = tuple[str, str, str]
Pair = tuple[ColumnRef, ColumnRef]


@dataclass(frozen=True)
class LabeledPair:
    source: ColumnRef
    target: ColumnRef
    is_relationship: bool
    reason: str


# These labels are fixture assumptions, not facts inferred from names alone.
LABELS = (
    LabeledPair(
        ("sales", "invoice", "customer_id"),
        ("crm", "customer", "id"),
        True,
        "Invoice belongs to the CRM customer.",
    ),
    LabeledPair(
        ("sales", "invoice", "product_id"),
        ("catalog", "product", "id"),
        True,
        "Invoice line identifies the catalog product.",
    ),
    LabeledPair(
        ("purchasing", "purchase_order", "supplier_id"),
        ("purchasing", "supplier", "id"),
        True,
        "Purchase order belongs to its supplier.",
    ),
    LabeledPair(
        ("sales", "shipment", "order_header_id"),
        ("public", "order_header", "id"),
        True,
        "Shipment belongs to an order header.",
    ),
    LabeledPair(
        ("sales", "support_ticket", "client_id"),
        ("crm", "customer", "id"),
        True,
        "Client is the same business entity as CRM customer; a synonym is needed.",
    ),
    LabeledPair(
        ("purchasing", "purchase_order", "vendor_id"),
        ("purchasing", "supplier", "id"),
        True,
        "Vendor is the supplier; a domain synonym is needed.",
    ),
    LabeledPair(
        ("sales", "order_line", "product_sku"),
        ("catalog", "product", "sku"),
        True,
        "Business key joins to the product SKU, not to id.",
    ),
    LabeledPair(
        ("public", "employee", "manager_id"),
        ("public", "employee", "id"),
        True,
        "Manager is another employee; this is a self-reference.",
    ),
    LabeledPair(
        ("sales", "invoice", "customer_id"),
        ("legacy", "customer", "id"),
        False,
        "Legacy customer table is a different identity space.",
    ),
    LabeledPair(
        ("billing", "invoice", "account_id"),
        ("finance", "account", "id"),
        False,
        "Billing account_id is an external system identifier, not a local account key.",
    ),
    LabeledPair(
        ("sales", "payment", "customer_id"),
        ("legacy", "customer", "id"),
        False,
        "Payment already has a declared FK to CRM; legacy is not an alternative.",
    ),
    LabeledPair(
        ("sales", "order_line", "customer_id"),
        ("crm", "customer", "id"),
        False,
        "The UUID source key is incompatible with CRM's integer customer key.",
    ),
)

DECLARED_FK: Pair = (("sales", "payment", "customer_id"), ("crm", "customer", "id"))


def _column(schema: str, table: str, name: str, data_type: str, ordinal: int = 1) -> ColumnMetadata:
    return ColumnMetadata(schema, table, name, ordinal, data_type, False)


def _fixture_columns() -> tuple[ColumnMetadata, ...]:
    return (
        _column("crm", "customer", "id", "integer"),
        _column("legacy", "customer", "id", "integer"),
        _column("catalog", "product", "id", "uuid"),
        _column("catalog", "product", "sku", "text", 2),
        _column("purchasing", "supplier", "id", "bigint"),
        _column("public", "order_header", "id", "bigint"),
        _column("finance", "account", "id", "integer"),
        _column("public", "employee", "id", "integer"),
        _column("public", "employee", "manager_id", "integer", 2),
        _column("sales", "invoice", "customer_id", "integer"),
        _column("sales", "invoice", "product_id", "uuid", 2),
        _column("sales", "invoice", "total_amount", "numeric", 3),
        _column("purchasing", "purchase_order", "supplier_id", "bigint"),
        _column("purchasing", "purchase_order", "vendor_id", "bigint", 2),
        _column("sales", "shipment", "order_header_id", "bigint"),
        _column("sales", "support_ticket", "client_id", "integer"),
        _column("sales", "order_line", "product_sku", "text"),
        _column("sales", "order_line", "customer_id", "uuid", 2),
        _column("billing", "invoice", "account_id", "integer"),
        _column("sales", "payment", "customer_id", "integer"),
    )


def _fixture_keys() -> tuple[KeyConstraintMetadata, ...]:
    primary = (
        ("crm", "customer"),
        ("legacy", "customer"),
        ("catalog", "product"),
        ("purchasing", "supplier"),
        ("public", "order_header"),
        ("finance", "account"),
        ("public", "employee"),
    )
    keys = tuple(
        KeyConstraintMetadata(schema, table, f"{table}_pkey", "PRIMARY_KEY", ("id",), False)
        for schema, table in primary
    )
    return keys + (
        KeyConstraintMetadata("catalog", "product", "product_sku_key", "UNIQUE", ("sku",), False),
    )


def _save_fixture(root: Path, extra_columns: tuple[ColumnMetadata, ...] = ()) -> None:
    columns = _fixture_columns() + extra_columns
    tables = sorted({(column.schema_name, column.table_name) for column in columns})
    schemas = sorted({schema for schema, _ in tables})
    initialize_project(root)
    source = SourceConfig(
        name="erp",
        host="localhost",
        port=5432,
        database_name="synthetic_erp",
        username="reader",
        credential_env="ERP_PASSWORD",
        schemas=tuple(schemas),
    )
    add_source(root, source)
    persist_snapshot(
        root,
        source,
        MetadataSnapshot(
            source_name="erp",
            schemas=tuple(SchemaMetadata(name) for name in schemas),
            tables=tuple(TableMetadata(schema, table, False) for schema, table in tables),
            columns=columns,
            keys=_fixture_keys(),
            foreign_keys=(
                ForeignKeyMetadata(
                    "sales",
                    "payment",
                    "payment_customer_fk",
                    ("customer_id",),
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


def _display(ref: ColumnRef) -> str:
    return ".".join(quote_identifier(part) for part in ref)


def test_metadata_v3_ground_truth_precision_and_recall(tmp_path: Path) -> None:
    _save_fixture(tmp_path)
    column_refs = {
        (column.schema_name, column.table_name, column.name) for column in _fixture_columns()
    }
    assert all(label.source in column_refs and label.target in column_refs for label in LABELS)
    assert len({(label.source, label.target) for label in LABELS}) == len(LABELS)

    first = preview_candidates(tmp_path, "erp", limit=50)
    second = preview_candidates(tmp_path, "erp", limit=50)
    assert first == second
    assert first.truncated is False
    assert first.skipped_ambiguous_columns == 1
    assert first.scoring_method == "metadata_exact_key_v3"
    assert first.include_ambiguous is False

    predictions = {(item.source_column, item.target_column) for item in first.candidates}
    positives = {
        (_display(label.source), _display(label.target))
        for label in LABELS
        if label.is_relationship
    }
    negatives = {
        (_display(label.source), _display(label.target))
        for label in LABELS
        if not label.is_relationship
    }
    assert predictions <= positives | negatives, "Every predicted pair needs a ground-truth label"
    expected_predictions = {
        (_display(label.source), _display(label.target)) for label in (*LABELS[1:4], LABELS[9])
    }
    assert predictions == expected_predictions
    assert (_display(DECLARED_FK[0]), _display(DECLARED_FK[1])) not in predictions

    true_positives = predictions & positives
    false_positives = predictions & negatives
    false_negatives = positives - predictions
    assert (len(true_positives), len(false_positives), len(false_negatives)) == (3, 1, 5)
    assert len(true_positives) / len(predictions) == pytest.approx(3 / 4)
    assert len(true_positives) / len(positives) == pytest.approx(3 / 8)

    by_score = Counter(item.confidence for item in first.candidates)
    assert by_score == {0.75: 3, 0.8: 1}
    assert (
        sum(
            item.confidence == 0.75 and (item.source_column, item.target_column) in true_positives
            for item in first.candidates
        )
        == 2
    )

    diagnostic = preview_candidates(tmp_path, "erp", limit=50, include_ambiguous=True)
    diagnostic_predictions = {
        (item.source_column, item.target_column) for item in diagnostic.candidates
    }
    assert diagnostic.include_ambiguous is True
    assert diagnostic.skipped_ambiguous_columns == 0
    assert diagnostic_predictions == predictions | {
        (_display(label.source), _display(label.target)) for label in (LABELS[0], LABELS[8])
    }
    assert (len(diagnostic_predictions & positives), len(diagnostic_predictions & negatives)) == (
        4,
        2,
    )


def test_external_identity_cannot_be_resolved_by_metadata_preview(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _save_fixture(tmp_path)

    def reject_source_connection(*args: object, **kwargs: object) -> None:
        raise AssertionError("Metadata preview must not connect to the source database.")

    monkeypatch.setattr("psycopg.connect", reject_source_connection)
    preview = preview_candidates(tmp_path, "erp")
    external_label = LABELS[9]
    assert external_label.is_relationship is False
    candidate = next(
        item
        for item in preview.candidates
        if (item.source_column, item.target_column)
        == (_display(external_label.source), _display(external_label.target))
    )
    assert (candidate.origin, candidate.status, candidate.confidence) == (
        "INFERRED",
        "PENDING",
        0.75,
    )
    assert {evidence.signal for evidence in candidate.evidence} == {
        "EXACT_TABLE_STEM",
        "EXACT_KEY_TYPE",
        "TARGET_UNIQUE",
        "SAME_SCHEMA",
    }
