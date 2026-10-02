"""Local graph search contracts and safety bounds."""

import json
import sqlite3
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from graphit.project import initialize_project
from graphit.queries import (
    QueryError,
    SearchResult,
    database_overview,
    find_table_path,
    get_relevant_context,
    relevant_context_payload,
    search_objects,
    show_table,
    table_relationships,
)
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


def _project(tmp_path: Path) -> SourceConfig:
    initialize_project(tmp_path)
    source = SourceConfig(
        name="erp",
        host="localhost",
        port=5432,
        database_name="erp",
        username="reader",
        credential_env="ERP_PASSWORD",
        schemas=("public",),
    )
    add_source(tmp_path, source)
    return source


def _metadata(*table_names: str) -> MetadataSnapshot:
    return MetadataSnapshot(
        source_name="erp",
        schemas=(SchemaMetadata("public"),),
        tables=tuple(TableMetadata("public", name, False) for name in table_names),
        columns=tuple(
            ColumnMetadata("public", name, "CustomerID", 1, "integer", False)
            for name in table_names
        ),
        keys=(),
        foreign_keys=(),
    )


def _overview_metadata() -> MetadataSnapshot:
    return MetadataSnapshot(
        source_name="erp",
        schemas=(SchemaMetadata("sales"), SchemaMetadata("public")),
        tables=(
            TableMetadata("public", "customer", False),
            TableMetadata("sales", "orders", False),
            TableMetadata("sales", "payment", False),
            TableMetadata("public", "orphan", False),
        ),
        columns=(
            ColumnMetadata("public", "customer", "id", 1, "integer", False),
            ColumnMetadata("sales", "orders", "customer_id", 1, "integer", False),
            ColumnMetadata("sales", "orders", "legacy_id", 2, "integer", True),
            ColumnMetadata("sales", "payment", "order_id", 1, "integer", False),
            ColumnMetadata("public", "orphan", "id", 1, "integer", False),
        ),
        keys=(),
        foreign_keys=(
            ForeignKeyMetadata(
                "sales",
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
                "sales",
                "payment",
                "payment_order_fk",
                ("order_id",),
                "sales",
                "orders",
                ("customer_id",),
                True,
                True,
                False,
            ),
            ForeignKeyMetadata(
                "sales",
                "orders",
                "orders_legacy_fk",
                ("legacy_id",),
                "external",
                "legacy",
                ("id",),
                False,
                True,
                False,
            ),
        ),
    )


def test_overview_counts_scanned_vs_external_and_ranks_connected_tables(tmp_path: Path) -> None:
    source = _project(tmp_path)
    persist_snapshot(tmp_path, source, _overview_metadata())

    result = database_overview(tmp_path, "erp")
    bounded = database_overview(tmp_path, "erp", limit=1)

    assert result.snapshot_version == 1
    assert (result.schema_count, result.external_schema_count) == (2, 1)
    assert (result.table_count, result.external_table_count) == (4, 1)
    assert (result.column_count, result.foreign_key_count) == (5, 3)
    assert result.schemas == ('"public"', '"sales"')
    assert [(table.qualified_name, table.declared_fk_count) for table in result.entry_tables] == [
        ('"sales"."orders"', 3),
        ('"public"."customer"', 1),
        ('"sales"."payment"', 1),
        ('"public"."orphan"', 0),
    ]
    assert bounded.schemas == ('"public"',)
    assert bounded.schemas_truncated is True
    assert bounded.entry_tables_truncated is True
    assert bounded.entry_tables[0].qualified_name == '"sales"."orders"'


def test_overview_empty_latest_snapshot_and_errors(tmp_path: Path) -> None:
    source = _project(tmp_path)
    with pytest.raises(QueryError) as missing:
        database_overview(tmp_path, "erp")
    assert missing.value.code == "NO_SNAPSHOT"
    with pytest.raises(QueryError) as invalid:
        database_overview(tmp_path, "erp", limit=0)
    assert invalid.value.code == "INVALID_LIMIT"

    persist_snapshot(tmp_path, source, _overview_metadata())
    persist_snapshot(tmp_path, source, MetadataSnapshot("erp", (), (), (), (), ()))
    result = database_overview(tmp_path, "erp")
    assert result.snapshot_version == 2
    assert result.schemas == ()
    assert result.entry_tables == ()
    assert (result.schema_count, result.table_count, result.foreign_key_count) == (0, 0, 0)


def test_overview_ignores_pending_edges_and_rejects_corrupt_metadata(tmp_path: Path) -> None:
    source = _project(tmp_path)
    persist_snapshot(tmp_path, source, _overview_metadata())
    store = tmp_path / ".graphit" / "graphit.db"
    with sqlite3.connect(store) as connection:
        rows = connection.execute(
            """SELECT id, object_name FROM objects
            WHERE object_type = 'TABLE' AND object_name IN ('orders', 'orphan')"""
        ).fetchall()
        table_ids = {str(name): int(table_id) for table_id, name in rows}
        snapshot_id = connection.execute("SELECT id FROM snapshots").fetchone()[0]
        connection.execute(
            """INSERT INTO edges
            (snapshot_id, source_object_id, target_object_id, edge_type,
             origin, status, confidence, metadata_json)
            VALUES (?, ?, ?, 'REFERENCES', 'INFERRED', 'PENDING', 0.5, '{}')""",
            (snapshot_id, table_ids["orders"], table_ids["orphan"]),
        )
    result = database_overview(tmp_path, "erp")
    assert result.foreign_key_count == 3
    assert result.entry_tables[-1].declared_fk_count == 0

    with sqlite3.connect(store) as connection:
        connection.execute(
            "UPDATE objects SET metadata_json = 'not-json' WHERE id = ?",
            (table_ids["orders"],),
        )
    with pytest.raises(QueryError) as invalid:
        database_overview(tmp_path, "erp")
    assert invalid.value.code == "STORE_READ_FAILED"


def test_relevant_context_ranks_name_matches_and_suggests_exact_tables(tmp_path: Path) -> None:
    source = _project(tmp_path)
    persist_snapshot(tmp_path, source, _metadata("Customer", "Invoice", "CustomerOrder"))

    task = "change customer invoice handling"
    context = get_relevant_context(tmp_path, "erp", task, max_objects=4)
    repeated = get_relevant_context(tmp_path, "erp", task, max_objects=4)

    assert context == repeated
    assert context.snapshot_version == 1
    assert context.task_terms == ("customer", "invoice", "handling")
    assert context.unmatched_terms == ("handling",)
    assert context.objects[0].qualified_name == '"public"."Invoice"."CustomerID"'
    assert context.objects[0].matched_terms == ("customer", "invoice")
    assert all(item.reason == "MATCHED_TASK_TERMS" for item in context.objects)
    assert context.objects[0].data_type == "integer"
    assert any(call.tool == "get_table" for call in context.suggested_followups)
    assert all(call.arguments["source"] == "erp" for call in context.suggested_followups)
    one_followup = get_relevant_context(tmp_path, "erp", task, max_objects=4, max_followups=1)
    assert one_followup.objects == context.objects
    assert one_followup.suggested_followups == context.suggested_followups[:1]
    payload = relevant_context_payload(context)
    assert len(json.dumps(payload, separators=(",", ":"))) < len(
        json.dumps(asdict(context), separators=(",", ":"))
    )


def test_relevant_context_prefers_exact_object_name_with_equal_term_hits(tmp_path: Path) -> None:
    source = _project(tmp_path)
    persist_snapshot(tmp_path, source, _metadata("Payment", "customer_invoice_000"))

    context = get_relevant_context(tmp_path, "erp", "payment customer")
    table_names = [item.qualified_name for item in context.objects if item.kind == "TABLE"]
    assert table_names.index('"public"."Payment"') < table_names.index(
        '"public"."customer_invoice_000"'
    )
    assert context.suggested_followups[0].arguments["name"] == '"public"."Payment"'


def test_relevant_context_budgets_no_match_and_latest_snapshot(tmp_path: Path) -> None:
    source = _project(tmp_path)
    with pytest.raises(QueryError) as missing:
        get_relevant_context(tmp_path, "erp", "customer")
    assert missing.value.code == "NO_SNAPSHOT"

    persist_snapshot(tmp_path, source, _metadata("Customer", "Invoice", "CustomerOrder"))
    limited = get_relevant_context(tmp_path, "erp", "customer invoice", max_objects=1)
    compact = get_relevant_context(tmp_path, "erp", "customer invoice", max_chars=500)
    unmatched = get_relevant_context(tmp_path, "erp", "nomatch")
    assert len(limited.objects) == 1 and limited.truncated is True
    assert (
        len(
            json.dumps(relevant_context_payload(compact), ensure_ascii=False, separators=(",", ":"))
        )
        <= 500
    )
    assert compact.truncated is True
    assert unmatched.objects == ()
    assert unmatched.unmatched_terms == ("nomatch",)
    assert unmatched.suggested_followups[0].tool == "database_overview"

    persist_snapshot(tmp_path, source, _metadata("Current"))
    latest = get_relevant_context(tmp_path, "erp", "customer")
    assert latest.snapshot_version == 2
    assert latest.objects[0].kind == "COLUMN"


def test_relevant_context_rejects_invalid_task_and_budgets(tmp_path: Path) -> None:
    for task, objects, chars, code in (
        ("", 8, 4000, "INVALID_TASK"),
        ("the and ve", 8, 4000, "INVALID_TASK"),
        ("x" * 301, 8, 4000, "INVALID_TASK"),
        ("customer", 0, 4000, "INVALID_BUDGET"),
        ("customer", 8, 499, "INVALID_BUDGET"),
        ("customer", 8, 12001, "INVALID_BUDGET"),
    ):
        with pytest.raises(QueryError) as invalid:
            get_relevant_context(tmp_path, "erp", task, objects, chars)
        assert invalid.value.code == code
    for followups in (0, 4):
        with pytest.raises(QueryError) as invalid:
            get_relevant_context(tmp_path, "erp", "customer", max_followups=followups)
        assert invalid.value.code == "INVALID_BUDGET"


def test_relevant_context_reports_term_and_candidate_caps(tmp_path: Path) -> None:
    source = _project(tmp_path)
    names = tuple(f"customer_{index:02d}" for index in range(60))
    persist_snapshot(tmp_path, source, _metadata(*names))

    many_terms = get_relevant_context(
        tmp_path, "erp", "alpha beta gamma delta epsilon zeta eta theta iota"
    )
    many_candidates = get_relevant_context(tmp_path, "erp", "customer", max_objects=5)

    assert many_terms.task_terms == (
        "alpha",
        "beta",
        "gamma",
        "delta",
        "epsilon",
        "zeta",
        "eta",
        "theta",
    )
    assert many_terms.terms_truncated is True
    assert many_terms.truncated is True
    assert many_candidates.candidate_search_truncated is True
    assert many_candidates.truncated is True
    assert len(many_candidates.objects) <= 5

    long_unmatched = " ".join(f"unusedterm{index}{'x' * 20}" for index in range(8))
    with pytest.raises(QueryError) as too_small:
        get_relevant_context(tmp_path, "erp", long_unmatched, max_chars=500)
    assert too_small.value.code == "CONTEXT_TOO_LARGE"


def test_relevant_context_detects_rescan_between_searches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    versions = iter((1, 2))

    def changing_search(root: Path, source: str, term: str, limit: int) -> SearchResult:
        return SearchResult(source, next(versions), (), False)

    monkeypatch.setattr("graphit.queries.search_objects", changing_search)
    with pytest.raises(QueryError) as changed:
        get_relevant_context(tmp_path, "erp", "customer invoice")
    assert changed.value.code == "SNAPSHOT_CHANGED"


def test_no_successful_snapshot_is_explicit(tmp_path: Path) -> None:
    _project(tmp_path)

    with pytest.raises(QueryError) as raised:
        search_objects(tmp_path, "erp", "customer")

    assert raised.value.code == "NO_SNAPSHOT"


def test_search_uses_latest_successful_snapshot_only(tmp_path: Path) -> None:
    source = _project(tmp_path)
    persist_snapshot(tmp_path, source, _metadata("Legacy"))
    persist_snapshot(tmp_path, source, _metadata("Customer"))
    with sqlite3.connect(tmp_path / ".graphit" / "graphit.db") as connection:
        connection.execute(
            """INSERT INTO scan_runs(source_id, status)
            SELECT id, 'FAILED' FROM sources WHERE name = 'erp'"""
        )

    result = search_objects(tmp_path, "erp", "customer")
    old = search_objects(tmp_path, "erp", "legacy")

    assert result.snapshot_version == 2
    assert [match.kind for match in result.matches] == ["TABLE", "COLUMN"]
    assert old.matches == ()
    assert old.snapshot_version == 2


def test_casefold_multi_term_literal_search_and_column_details(tmp_path: Path) -> None:
    source = _project(tmp_path)
    persist_snapshot(tmp_path, source, _metadata("Customer_Order", "Customer%"))

    result = search_objects(tmp_path, "erp", "CUSTOMER order")
    literal = search_objects(tmp_path, "erp", "%")

    assert result.snapshot_version == 1
    assert [match.kind for match in result.matches] == ["TABLE", "COLUMN"]
    assert result.matches[0].qualified_name == '"public"."Customer_Order"'
    assert result.matches[1].data_type == "integer"
    assert result.matches[1].nullable is False
    assert [match.qualified_name for match in literal.matches] == [
        '"public"."Customer%"',
        '"public"."Customer%"."CustomerID"',
    ]


def test_search_limit_is_bounded_and_order_stable(tmp_path: Path) -> None:
    source = _project(tmp_path)
    persist_snapshot(tmp_path, source, _metadata("CustomerB", "CustomerA", "CustomerC"))

    one = search_objects(tmp_path, "erp", "customer", limit=1)
    two = search_objects(tmp_path, "erp", "customer", limit=2)

    assert one.truncated is True
    assert [match.qualified_name for match in one.matches] == ['"public"."CustomerA"']
    assert [match.qualified_name for match in two.matches] == [
        '"public"."CustomerA"',
        '"public"."CustomerB"',
    ]
    assert two.truncated is True


def test_unicode_casefold_and_corrupt_metadata_error(tmp_path: Path) -> None:
    source = _project(tmp_path)
    persist_snapshot(tmp_path, source, _metadata("Straße"))

    result = search_objects(tmp_path, "erp", "STRASSE")
    assert result.matches[0].qualified_name == '"public"."Straße"'

    with sqlite3.connect(tmp_path / ".graphit" / "graphit.db") as connection:
        connection.execute(
            """UPDATE objects SET metadata_json = 'not-json'
            WHERE object_type = 'TABLE' AND object_name = 'Straße'"""
        )
    with pytest.raises(QueryError) as raised:
        search_objects(tmp_path, "erp", "STRASSE")
    assert raised.value.code == "STORE_READ_FAILED"


def test_out_of_scope_fk_target_is_labeled_as_partial(tmp_path: Path) -> None:
    source = _project(tmp_path)
    metadata = replace(
        _metadata("Order"),
        foreign_keys=(
            ForeignKeyMetadata(
                "public",
                "Order",
                "order_customer_fk",
                ("CustomerID",),
                "external",
                "Customer",
                ("ID",),
                False,
                True,
                False,
            ),
        ),
    )
    persist_snapshot(tmp_path, source, metadata)

    result = search_objects(tmp_path, "erp", "external")

    assert [match.kind for match in result.matches] == ["SCHEMA", "TABLE"]
    assert all(match.in_scope is False for match in result.matches)


@pytest.mark.parametrize(
    ("query", "limit", "code"),
    [
        ("", 20, "INVALID_QUERY"),
        ("a\x00b", 20, "INVALID_QUERY"),
        ("a " * 9, 20, "INVALID_QUERY"),
        ("x" * 201, 20, "INVALID_QUERY"),
        ("customer", 0, "INVALID_LIMIT"),
        ("customer", 101, "INVALID_LIMIT"),
    ],
)
def test_invalid_search_is_rejected_before_store_access(
    tmp_path: Path, query: str, limit: int, code: str
) -> None:
    with pytest.raises(QueryError) as raised:
        search_objects(tmp_path, "erp", query, limit)
    assert raised.value.code == code


def test_show_exact_quoted_table_preserves_composite_facts(tmp_path: Path) -> None:
    source = _project(tmp_path)
    metadata = MetadataSnapshot(
        source_name="erp",
        schemas=(SchemaMetadata("public"),),
        tables=(
            TableMetadata("public", "Order.Detail", False),
            TableMetadata("public", "Customer", False),
        ),
        columns=(
            ColumnMetadata("public", "Order.Detail", "ID", 1, "integer", False),
            ColumnMetadata("public", "Order.Detail", "Region", 2, "text", False),
            ColumnMetadata("public", "Order.Detail", "CustomerID", 3, "integer", False),
            ColumnMetadata("public", "Order.Detail", "CustomerRegion", 4, "text", False),
            ColumnMetadata("public", "Customer", "ID", 1, "integer", False),
            ColumnMetadata("public", "Customer", "Region", 2, "text", False),
        ),
        keys=(
            KeyConstraintMetadata(
                "public", "Order.Detail", "order_pkey", "PRIMARY_KEY", ("ID", "Region"), False
            ),
            KeyConstraintMetadata(
                "public",
                "Order.Detail",
                "order_customer_key",
                "UNIQUE",
                ("CustomerID", "CustomerRegion"),
                True,
            ),
            KeyConstraintMetadata(
                "public", "Customer", "customer_pkey", "PRIMARY_KEY", ("ID", "Region"), False
            ),
        ),
        foreign_keys=(
            ForeignKeyMetadata(
                "public",
                "Order.Detail",
                "order_customer_fk",
                ("CustomerID", "CustomerRegion"),
                "public",
                "Customer",
                ("ID", "Region"),
                True,
                False,
                False,
            ),
            ForeignKeyMetadata(
                "public",
                "Order.Detail",
                "order_external_fk",
                ("CustomerID",),
                "external",
                "Legacy",
                ("ID",),
                False,
                True,
                True,
            ),
        ),
    )
    persist_snapshot(tmp_path, source, metadata)

    result = show_table(tmp_path, "erp", '"public"."Order.Detail"')

    assert result.qualified_name == '"public"."Order.Detail"'
    assert result.snapshot_version == 1
    assert [column.name for column in result.columns] == [
        "ID",
        "Region",
        "CustomerID",
        "CustomerRegion",
    ]
    assert (result.columns[0].primary_key, result.columns[0].unique_value) == (True, False)
    assert [(key.kind, key.columns, key.inherited) for key in result.keys] == [
        ("UNIQUE", ("CustomerID", "CustomerRegion"), True),
        ("PRIMARY_KEY", ("ID", "Region"), False),
    ]
    assert [key.name for key in result.foreign_keys] == ["order_customer_fk", "order_external_fk"]
    assert result.foreign_keys[0].column_pairs == (
        ("CustomerID", "ID"),
        ("CustomerRegion", "Region"),
    )
    assert result.foreign_keys[0].validated is False
    assert result.foreign_keys[1].target_table == '"external"."Legacy"'
    assert result.foreign_keys[1].target_in_scope is False
    assert result.foreign_keys[1].inherited is True

    bounded = show_table(tmp_path, "erp", '"Order.Detail"', limit=1)
    assert len(bounded.columns) == len(bounded.keys) == len(bounded.foreign_keys) == 1
    assert (bounded.columns_truncated, bounded.keys_truncated, bounded.foreign_keys_truncated) == (
        True,
        True,
        True,
    )
    with sqlite3.connect(tmp_path / ".graphit" / "graphit.db") as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM edges WHERE edge_type = 'UNIQUE_KEY'"
            ).fetchone()[0]
            == 2
        )
        connection.execute("UPDATE edges SET edge_type = 'UNIQUE' WHERE edge_type = 'UNIQUE_KEY'")
    assert len(show_table(tmp_path, "erp", '"public"."Order.Detail"').keys) == 2


def test_show_resolves_unquoted_names_and_reports_ambiguity(tmp_path: Path) -> None:
    source = _project(tmp_path)
    metadata = MetadataSnapshot(
        source_name="erp",
        schemas=(SchemaMetadata("public"), SchemaMetadata("archive")),
        tables=(
            TableMetadata("public", "orders", False),
            TableMetadata("archive", "orders", False),
        ),
        columns=(),
        keys=(),
        foreign_keys=(),
    )
    persist_snapshot(tmp_path, source, metadata)

    with pytest.raises(QueryError) as raised:
        show_table(tmp_path, "erp", "orders")
    assert raised.value.code == "AMBIGUOUS_TABLE"
    assert show_table(tmp_path, "erp", "PUBLIC.ORDERS").qualified_name == '"public"."orders"'
    with pytest.raises(QueryError) as missing:
        show_table(tmp_path, "erp", "public.missing")
    assert missing.value.code == "TABLE_NOT_FOUND"


def test_show_quoted_identifier_with_escaped_quote(tmp_path: Path) -> None:
    source = _project(tmp_path)
    persist_snapshot(tmp_path, source, _metadata('odd"name'))

    result = show_table(tmp_path, "erp", 'public."odd""name"')

    assert result.qualified_name == '"public"."odd""name"'


def test_show_uses_latest_snapshot_and_labels_external_stub(tmp_path: Path) -> None:
    source = _project(tmp_path)
    persist_snapshot(tmp_path, source, _metadata("legacy"))
    metadata = replace(
        _metadata("orders"),
        foreign_keys=(
            ForeignKeyMetadata(
                "public",
                "orders",
                "orders_customer_fk",
                ("CustomerID",),
                "external",
                "customer",
                ("id",),
                False,
                True,
                False,
            ),
        ),
    )
    persist_snapshot(tmp_path, source, metadata)

    result = show_table(tmp_path, "erp", "orders")
    stub = show_table(tmp_path, "erp", "external.customer")

    assert result.snapshot_version == 2
    assert stub.in_scope is False
    assert stub.partitioned is None
    assert stub.columns == ()
    with pytest.raises(QueryError) as raised:
        show_table(tmp_path, "erp", "legacy")
    assert raised.value.code == "TABLE_NOT_FOUND"


@pytest.mark.parametrize("reference", ["", "a.b.c", '"unterminated', '"a"b', ".orders"])
def test_show_rejects_malformed_table_reference(tmp_path: Path, reference: str) -> None:
    with pytest.raises(QueryError) as raised:
        show_table(tmp_path, "erp", reference)
    assert raised.value.code == "INVALID_TABLE"


def test_show_rejects_limits_and_missing_snapshot(tmp_path: Path) -> None:
    for limit in (0, 101):
        with pytest.raises(QueryError) as raised:
            show_table(tmp_path, "erp", "orders", limit)
        assert raised.value.code == "INVALID_LIMIT"
    _project(tmp_path)
    with pytest.raises(QueryError) as raised:
        show_table(tmp_path, "erp", "orders")
    assert raised.value.code == "NO_SNAPSHOT"


def _relationship_metadata() -> MetadataSnapshot:
    return MetadataSnapshot(
        source_name="erp",
        schemas=(SchemaMetadata("public"),),
        tables=(
            TableMetadata("public", "orders", False),
            TableMetadata("public", "customer", False),
            TableMetadata("public", "payment", False),
        ),
        columns=(
            ColumnMetadata("public", "orders", "id", 1, "integer", False),
            ColumnMetadata("public", "orders", "customer_id", 2, "integer", False),
            ColumnMetadata("public", "orders", "customer_region", 3, "text", False),
            ColumnMetadata("public", "orders", "parent_id", 4, "integer", True),
            ColumnMetadata("public", "customer", "id", 1, "integer", False),
            ColumnMetadata("public", "customer", "region", 2, "text", False),
            ColumnMetadata("public", "payment", "order_id", 1, "integer", False),
        ),
        keys=(),
        foreign_keys=(
            ForeignKeyMetadata(
                "public",
                "orders",
                "orders_customer_fk",
                ("customer_id", "customer_region"),
                "public",
                "customer",
                ("id", "region"),
                True,
                False,
                False,
            ),
            ForeignKeyMetadata(
                "public",
                "orders",
                "orders_parent_fk",
                ("parent_id",),
                "public",
                "orders",
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
                True,
            ),
            ForeignKeyMetadata(
                "public",
                "orders",
                "orders_external_fk",
                ("customer_id",),
                "external",
                "legacy",
                ("id",),
                False,
                True,
                False,
            ),
        ),
    )


def test_direct_relationships_both_directions_self_and_external(tmp_path: Path) -> None:
    source = _project(tmp_path)
    persist_snapshot(tmp_path, source, _relationship_metadata())

    orders = table_relationships(tmp_path, "erp", "public.orders")
    external = table_relationships(tmp_path, "erp", "external.legacy")

    assert orders.snapshot_version == 1
    assert [item.name for item in orders.incoming] == ["orders_parent_fk", "payment_order_fk"]
    assert [item.name for item in orders.outgoing] == [
        "orders_customer_fk",
        "orders_parent_fk",
        "orders_external_fk",
    ]
    assert orders.outgoing[0].column_pairs == (("customer_id", "id"), ("customer_region", "region"))
    assert orders.outgoing[0].validated is False
    assert orders.incoming[1].inherited is True
    assert all(item.origin == "DATABASE" and item.status == "CONFIRMED" for item in orders.outgoing)
    assert external.in_scope is False
    assert [item.name for item in external.incoming] == ["orders_external_fk"]
    assert external.outgoing == ()
    assert external.incoming[0].target_in_scope is False


def test_direct_relationships_bounds_and_excludes_inferred_edges(tmp_path: Path) -> None:
    source = _project(tmp_path)
    persist_snapshot(tmp_path, source, _relationship_metadata())
    path = tmp_path / ".graphit" / "graphit.db"
    with sqlite3.connect(path) as connection:
        source_id, snapshot_id = connection.execute(
            "SELECT id, snapshot_id FROM objects "
            "WHERE object_type = 'TABLE' AND object_name = 'orders'"
        ).fetchone()
        target_id = connection.execute(
            "SELECT id FROM objects WHERE object_type = 'TABLE' AND object_name = 'customer'"
        ).fetchone()[0]
        connection.execute(
            """INSERT INTO edges
            (snapshot_id, source_object_id, target_object_id, edge_type,
             origin, status, confidence, metadata_json)
            VALUES (?, ?, ?, 'REFERENCES', 'INFERRED', 'PENDING', 0.5, '{}')""",
            (snapshot_id, source_id, target_id),
        )

    bounded = table_relationships(tmp_path, "erp", "orders", limit=1)
    customer = table_relationships(tmp_path, "erp", "customer")

    assert len(bounded.incoming) == len(bounded.outgoing) == 1
    assert bounded.incoming_truncated is True
    assert bounded.outgoing_truncated is True
    assert [item.name for item in customer.incoming] == ["orders_customer_fk"]


def test_direct_relationships_latest_snapshot_and_errors(tmp_path: Path) -> None:
    source = _project(tmp_path)
    persist_snapshot(tmp_path, source, _relationship_metadata())
    persist_snapshot(tmp_path, source, _metadata("orders"))

    latest = table_relationships(tmp_path, "erp", "orders")
    assert latest.snapshot_version == 2
    assert latest.incoming == latest.outgoing == ()
    with pytest.raises(QueryError) as missing:
        table_relationships(tmp_path, "erp", "customer")
    assert missing.value.code == "TABLE_NOT_FOUND"
    for reference, limit, code in (("a.b.c", 20, "INVALID_TABLE"), ("orders", 0, "INVALID_LIMIT")):
        with pytest.raises(QueryError) as raised:
            table_relationships(tmp_path, "erp", reference, limit)
        assert raised.value.code == code


def test_direct_relationships_rejects_ambiguous_bare_table(tmp_path: Path) -> None:
    source = _project(tmp_path)
    metadata = MetadataSnapshot(
        source_name="erp",
        schemas=(SchemaMetadata("public"), SchemaMetadata("archive")),
        tables=(
            TableMetadata("public", "orders", False),
            TableMetadata("archive", "orders", False),
        ),
        columns=(),
        keys=(),
        foreign_keys=(),
    )
    persist_snapshot(tmp_path, source, metadata)

    with pytest.raises(QueryError) as raised:
        table_relationships(tmp_path, "erp", "orders")
    assert raised.value.code == "AMBIGUOUS_TABLE"
    assert table_relationships(tmp_path, "erp", "archive.orders").qualified_name == (
        '"archive"."orders"'
    )


def test_shortest_path_forward_reverse_self_and_external(tmp_path: Path) -> None:
    source = _project(tmp_path)
    persist_snapshot(tmp_path, source, _relationship_metadata())

    forward = find_table_path(tmp_path, "erp", "payment", "customer")
    reverse = find_table_path(tmp_path, "erp", "customer", "payment")
    same = find_table_path(tmp_path, "erp", "orders", "orders", max_hops=0)
    external = find_table_path(tmp_path, "erp", "payment", "external.legacy")

    assert [step.foreign_key for step in forward.steps] == [
        "payment_order_fk",
        "orders_customer_fk",
    ]
    assert [step.direction for step in forward.steps] == ["FORWARD", "FORWARD"]
    assert [step.direction for step in reverse.steps] == ["REVERSE", "REVERSE"]
    assert reverse.steps[0].column_pairs == (("customer_id", "id"), ("customer_region", "region"))
    assert all(step.origin == "DATABASE" and step.status == "CONFIRMED" for step in forward.steps)
    assert same.steps == ()
    assert external.steps[-1].target_in_scope is False
    assert external.end_table == '"external"."legacy"'


def test_shortest_path_tie_is_deterministic_and_ignores_inferred(tmp_path: Path) -> None:
    source = _project(tmp_path)
    names = ("a", "b", "c", "d")
    metadata = MetadataSnapshot(
        source_name="erp",
        schemas=(SchemaMetadata("public"),),
        tables=tuple(TableMetadata("public", name, False) for name in names),
        columns=tuple(ColumnMetadata("public", name, "id", 1, "integer", False) for name in names),
        keys=(),
        foreign_keys=(
            ForeignKeyMetadata(
                "public", "a", "a_c_fk", ("id",), "public", "c", ("id",), True, True, False
            ),
            ForeignKeyMetadata(
                "public", "a", "a_b_fk", ("id",), "public", "b", ("id",), True, True, False
            ),
            ForeignKeyMetadata(
                "public", "c", "c_d_fk", ("id",), "public", "d", ("id",), True, True, False
            ),
            ForeignKeyMetadata(
                "public", "b", "b_d_fk", ("id",), "public", "d", ("id",), True, True, False
            ),
        ),
    )
    persist_snapshot(tmp_path, source, metadata)
    with sqlite3.connect(tmp_path / ".graphit" / "graphit.db") as connection:
        ids = dict(
            connection.execute(
                "SELECT object_name, id FROM objects WHERE object_type = 'TABLE'"
            ).fetchall()
        )
        connection.execute(
            """INSERT INTO edges
            (snapshot_id, source_object_id, target_object_id, edge_type,
             origin, status, confidence, metadata_json)
            VALUES (1, ?, ?, 'REFERENCES', 'INFERRED', 'PENDING', 0.8, '{}')""",
            (ids["a"], ids["d"]),
        )

    result = find_table_path(tmp_path, "erp", "a", "d")

    assert [step.foreign_key for step in result.steps] == ["a_b_fk", "b_d_fk"]
    assert len(result.steps) == 2


def test_shortest_path_hop_and_work_budgets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _project(tmp_path)
    persist_snapshot(tmp_path, source, _relationship_metadata())

    with pytest.raises(QueryError) as too_shallow:
        find_table_path(tmp_path, "erp", "payment", "customer", max_hops=1)
    assert too_shallow.value.code == "PATH_NOT_FOUND"

    monkeypatch.setattr("graphit.queries.MAX_PATH_NODES", 2)
    with pytest.raises(QueryError) as too_many_nodes:
        find_table_path(tmp_path, "erp", "payment", "customer")
    assert too_many_nodes.value.code == "PATH_BUDGET_EXCEEDED"

    monkeypatch.setattr("graphit.queries.MAX_PATH_NODES", 500)
    monkeypatch.setattr("graphit.queries.MAX_PATH_EDGES", 1)
    with pytest.raises(QueryError) as too_many_edges:
        find_table_path(tmp_path, "erp", "payment", "customer")
    assert too_many_edges.value.code == "PATH_BUDGET_EXCEEDED"


def test_shortest_path_rescan_no_path_and_invalid_inputs(tmp_path: Path) -> None:
    source = _project(tmp_path)
    persist_snapshot(tmp_path, source, _relationship_metadata())
    persist_snapshot(tmp_path, source, _metadata("payment", "customer"))

    with pytest.raises(QueryError) as missing_path:
        find_table_path(tmp_path, "erp", "payment", "customer")
    assert missing_path.value.code == "PATH_NOT_FOUND"
    for start, end, hops, code in (
        ("a.b.c", "customer", 4, "INVALID_TABLE"),
        ("payment", "customer", -1, "INVALID_HOPS"),
        ("payment", "customer", 9, "INVALID_HOPS"),
        ("payment", "missing", 4, "TABLE_NOT_FOUND"),
    ):
        with pytest.raises(QueryError) as raised:
            find_table_path(tmp_path, "erp", start, end, hops)
        assert raised.value.code == code
