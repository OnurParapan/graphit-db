"""Prepare an offline, synthetic ERP bundle for controlled agent evaluation."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from graphit.queries import MAX_OVERVIEW_LIMIT, database_overview, show_table, table_relationships
from graphit.scanners.protocol import ColumnMetadata, quote_identifier
from tests.test_inference_benchmark import (
    DECLARED_FK,
    _fixture_columns,
    _fixture_keys,
    _save_fixture,
)

SOURCE = "erp"
PAGE_LIMIT = 100
SCALED_ARCHIVE_TABLES = 100


def scaled_distractor_columns() -> tuple[ColumnMetadata, ...]:
    """Return the deterministic 100-table archive corpus used by scale tests."""

    return tuple(
        ColumnMetadata("archive", f"record_{index:03d}", name, ordinal, data_type, False)
        for index in range(SCALED_ARCHIVE_TABLES)
        for ordinal, name, data_type in (
            (1, "id", "bigint"),
            (2, "payload", "text"),
            (3, "revision", "integer"),
            (4, "state", "text"),
        )
    )


def build_baseline_packet(project: Path, extra_columns: tuple[ColumnMetadata, ...] = ()) -> bytes:
    """Serialize all saved fixture facts, failing closed on missing/truncated facts."""

    fixture_columns = _fixture_columns() + extra_columns
    fixture_keys = _fixture_keys()
    table_names = sorted({(column.schema_name, column.table_name) for column in fixture_columns})
    overview = database_overview(project, SOURCE, MAX_OVERVIEW_LIMIT)
    if (
        overview.schemas_truncated
        or overview.entry_tables_truncated != (len(table_names) > MAX_OVERVIEW_LIMIT)
        or overview.table_count != len(table_names)
        or overview.column_count != len(fixture_columns)
        or overview.foreign_key_count != 1
    ):
        raise ValueError("Saved fixture counts are incomplete or differ from the evaluation corpus")

    tables: list[dict[str, object]] = []
    relationships: list[
        tuple[str, str, tuple[tuple[str, str], ...], str, bool, bool, bool, str, str]
    ] = []
    column_count = 0
    for schema_name, table_name in table_names:
        qualified = f"{quote_identifier(schema_name)}.{quote_identifier(table_name)}"
        table = show_table(project, SOURCE, qualified, PAGE_LIMIT)
        edges = table_relationships(project, SOURCE, qualified, PAGE_LIMIT)
        if (
            table.source_name != SOURCE
            or edges.source_name != SOURCE
            or table.snapshot_version != overview.snapshot_version
            or edges.snapshot_version != overview.snapshot_version
            or table.qualified_name != qualified
            or edges.qualified_name != qualified
            or not table.in_scope
            or not edges.in_scope
            or table.columns_truncated
            or table.keys_truncated
            or table.foreign_keys_truncated
            or edges.incoming_truncated
            or edges.outgoing_truncated
        ):
            raise ValueError(f"Saved fixture context is incomplete for {qualified}")

        expected_columns = sorted(
            (column.name, column.data_type, column.nullable)
            for column in fixture_columns
            if (column.schema_name, column.table_name) == (schema_name, table_name)
        )
        actual_columns = sorted(
            (column.name, column.data_type, column.nullable) for column in table.columns
        )
        expected_keys = sorted(
            (key.name, key.kind, key.columns, key.inherited)
            for key in fixture_keys
            if (key.schema_name, key.table_name) == (schema_name, table_name)
        )
        actual_keys = sorted((key.name, key.kind, key.columns, key.inherited) for key in table.keys)
        table_fk_facts = sorted(
            (
                key.name,
                key.target_table,
                key.column_pairs,
                key.target_in_scope,
                key.validated,
                key.inherited,
            )
            for key in table.foreign_keys
        )
        edge_fk_facts = sorted(
            (
                key.name,
                key.target_table,
                key.column_pairs,
                key.target_in_scope,
                key.validated,
                key.inherited,
            )
            for key in edges.outgoing
        )
        if (
            expected_columns != actual_columns
            or expected_keys != actual_keys
            or table_fk_facts != edge_fk_facts
        ):
            raise ValueError(f"Saved fixture facts differ from the evaluation corpus: {qualified}")

        for key in edges.outgoing:
            relationships.append(
                (
                    key.name,
                    key.source_table,
                    key.column_pairs,
                    key.target_table,
                    key.target_in_scope,
                    key.validated,
                    key.inherited,
                    key.origin,
                    key.status,
                )
            )
            if key.status != "CONFIRMED" or key.origin != "DATABASE":
                raise ValueError(f"Unexpected FK provenance in {qualified}")
        column_count += len(table.columns)
        tables.append(
            {
                "qualified_name": qualified,
                "columns": [asdict(column) for column in table.columns],
                "keys": [asdict(key) for key in table.keys],
                "foreign_keys": [asdict(key) for key in edges.outgoing],
            }
        )

    source_column = DECLARED_FK[0][2]
    target_column = DECLARED_FK[1][2]
    expected_fk = (
        "payment_customer_fk",
        '"sales"."payment"',
        ((source_column, target_column),),
        '"crm"."customer"',
        True,
        True,
        False,
        "DATABASE",
        "CONFIRMED",
    )
    if relationships != [expected_fk]:
        raise ValueError("Saved declared FK does not match the independent evaluation corpus")
    if column_count != overview.column_count:
        raise ValueError("Saved column count differs from the evaluation corpus")

    packet = {
        "source_name": SOURCE,
        "snapshot_version": overview.snapshot_version,
        "table_count": len(tables),
        "column_count": column_count,
        "foreign_key_count": len(relationships),
        "tables": tables,
    }
    return (
        json.dumps(packet, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def prepare_eval_bundle(output: Path, *, scaled: bool = False) -> str:
    """Create a new local bundle; never overwrite an existing path."""

    if output.exists() or output.is_symlink():
        raise FileExistsError(f"Evaluation output already exists: {output}")
    output.mkdir()
    graphit_project = output / "graphit-project"
    graphit_project.mkdir()
    extra_columns = scaled_distractor_columns() if scaled else ()
    _save_fixture(graphit_project, extra_columns)
    packet = build_baseline_packet(graphit_project, extra_columns)
    baseline_dir = output / "baseline-arm"
    baseline_dir.mkdir()
    with (baseline_dir / "baseline.json").open("xb") as stream:
        stream.write(packet)
    return hashlib.sha256(packet).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="new directory for the offline evaluation bundle")
    parser.add_argument(
        "--scaled",
        action="store_true",
        help="add 100 unrelated archive tables (114 tables / 420 columns total)",
    )
    args = parser.parse_args()
    try:
        digest = prepare_eval_bundle(args.output, scaled=args.scaled)
    except (FileExistsError, ValueError) as error:
        parser.exit(2, f"Evaluation preparation failed: {error}\n")
    print(f"Prepared {args.output} (baseline SHA-256 {digest})")


if __name__ == "__main__":
    main()
