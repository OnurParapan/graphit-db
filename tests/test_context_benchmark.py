"""Fixed ERP-like grounding and response-size baseline for focused MCP graph context."""

import json
from dataclasses import dataclass
from pathlib import Path

import pytest
import tiktoken
from mcp import Client
from mcp.types import TextContent
from pytest import MonkeyPatch

from graphit.mcp_server import MAX_MCP_RESPONSE_CHARS, create_server
from graphit.queries import show_table
from graphit.review import approve_candidate, approve_manual_proposal, propose_relationship
from graphit.scanners.protocol import ColumnMetadata, quote_identifier
from tests.test_inference_benchmark import (
    DECLARED_FK,
    LABELS,
    ColumnRef,
    _display,
    _fixture_columns,
    _save_fixture,
)


@dataclass(frozen=True)
class GroundedQuestion:
    task: str
    focus: str
    source: ColumnRef
    target: ColumnRef
    relationship: bool
    expected_origin: str | None
    expected_status: str | None


# Labels are written separately from Graphit's name/type matching algorithm.
QUESTIONS = (
    GroundedQuestion(
        "Which declared key links a payment to its customer?",
        "sales.payment",
        DECLARED_FK[0],
        DECLARED_FK[1],
        True,
        "DATABASE",
        "CONFIRMED",
    ),
    GroundedQuestion(
        "Which reviewed supplier identity belongs to a purchase order?",
        "purchasing.purchase_order",
        ("purchasing", "purchase_order", "supplier_id"),
        ("purchasing", "supplier", "id"),
        True,
        "INFERRED",
        "APPROVED",
    ),
    GroundedQuestion(
        "Does support_ticket.client_id reach the CRM customer?",
        "sales.support_ticket",
        ("sales", "support_ticket", "client_id"),
        ("crm", "customer", "id"),
        True,
        None,
        None,
    ),
    GroundedQuestion(
        "Which customer identity does a sales invoice reference?",
        "sales.invoice",
        ("sales", "invoice", "customer_id"),
        ("crm", "customer", "id"),
        True,
        None,
        None,
    ),
    GroundedQuestion(
        "Is the billing account ID a confirmed local account relationship?",
        "billing.invoice",
        ("billing", "invoice", "account_id"),
        ("finance", "account", "id"),
        False,
        None,
        None,
    ),
)


def _distractor_columns() -> tuple[ColumnMetadata, ...]:
    return tuple(
        ColumnMetadata("archive", f"record_{index:03d}", name, ordinal, data_type, False)
        for index in range(100)
        for ordinal, name, data_type in (
            (1, "id", "bigint"),
            (2, "payload", "text"),
            (3, "revision", "integer"),
            (4, "state", "text"),
        )
    )


def _overlap_columns() -> tuple[ColumnMetadata, ...]:
    return tuple(
        ColumnMetadata("archive", f"customer_invoice_{index:03d}", "id", 1, "bigint", False)
        for index in range(60)
    )


def _full_structural_baseline(root: Path, extra_columns: tuple[ColumnMetadata, ...] = ()) -> str:
    """One compact local all-table dump, including the known approved decision."""

    names = sorted(
        {(column.schema_name, column.table_name) for column in _fixture_columns() + extra_columns}
    )
    tables = []
    for schema, table in names:
        exact = f"{quote_identifier(schema)}.{quote_identifier(table)}"
        context = show_table(root, "erp", exact, limit=100)
        assert not (
            context.columns_truncated or context.keys_truncated or context.foreign_keys_truncated
        )
        tables.append(
            {
                "table": context.qualified_name,
                "columns": [
                    (
                        column.name,
                        column.data_type,
                        column.nullable,
                        column.primary_key,
                        column.unique_value,
                    )
                    for column in context.columns
                ],
                "keys": [(key.kind, key.columns) for key in context.keys],
                "foreign_keys": [
                    (foreign_key.target_table, foreign_key.column_pairs)
                    for foreign_key in context.foreign_keys
                ],
            }
        )
    approved = QUESTIONS[1]
    return json.dumps(
        {
            "source_name": "erp",
            "snapshot_version": 1,
            "tables": tables,
            "approved_logical_links": [
                {
                    "source_column": _display(approved.source),
                    "target_column": _display(approved.target),
                    "origin": "INFERRED",
                    "status": "APPROVED",
                }
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _link_for_pair(
    payload: dict[str, object], question: GroundedQuestion
) -> dict[str, object] | None:
    links = payload["links"]
    assert isinstance(links, list)
    pair = [_display(question.source), _display(question.target)]
    for link in links:
        assert isinstance(link, dict)
        if pair in link["column_pairs"]:
            return link
    return None


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_fixed_erp_graph_context_coverage_and_size(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    _save_fixture(tmp_path)
    approved = QUESTIONS[1]
    approve_candidate(tmp_path, "erp", _display(approved.source), _display(approved.target), 1)

    labeled = {(label.source, label.target): label.is_relationship for label in LABELS}
    for question in QUESTIONS:
        if (question.source, question.target) == DECLARED_FK:
            assert question.relationship
        else:
            assert labeled[(question.source, question.target)] == question.relationship

    baseline = _full_structural_baseline(tmp_path)

    def no_source_connection(*args: object, **kwargs: object) -> None:
        raise AssertionError("Context benchmark must use only saved metadata")

    monkeypatch.setattr("psycopg.connect", no_source_connection)
    covered_positives = 0
    exposed_negatives = 0
    context_sizes: list[int] = []
    async with Client(create_server(tmp_path)) as client:
        for question in QUESTIONS:
            result = await client.call_tool(
                "get_graph_context", {"source": "erp", "name": question.focus, "limit": 8}
            )
            assert result.is_error is False and result.structured_content is None
            assert len(result.content) == 1 and isinstance(result.content[0], TextContent)
            response = result.content[0].text
            context_sizes.append(len(response))
            assert len(response) <= MAX_MCP_RESPONSE_CHARS
            payload = json.loads(response)
            assert payload["truncated"] is False
            link = _link_for_pair(payload, question)
            if question.relationship and link is not None:
                covered_positives += 1
            if not question.relationship and link is not None:
                exposed_negatives += 1
            if question.expected_origin is not None:
                assert link is not None
                assert (link["origin"], link["status"]) == (
                    question.expected_origin,
                    question.expected_status,
                )
            else:
                assert link is None

        path = await client.call_tool(
            "find_path",
            {"source": "erp", "from_table": "sales.payment", "to_table": "crm.customer"},
        )
        assert path.is_error is False
        assert len(path.content) == 1 and isinstance(path.content[0], TextContent)
        steps = json.loads(path.content[0].text)["steps"]
        assert len(steps) == 1 and steps[0]["foreign_key"] == "payment_customer_fk"

    assert covered_positives == 2
    assert exposed_negatives == 0
    assert len(context_sizes) == len(QUESTIONS)
    assert max(context_sizes) < len(baseline)
    print(
        "ERP_CONTEXT_BASELINE "
        + json.dumps(
            {
                "tables": len({(c.schema_name, c.table_name) for c in _fixture_columns()}),
                "positive_coverage": "2/4",
                "labeled_negative_exposure": "0/1",
                "full_schema_chars": len(baseline),
                "focused_graph_chars": context_sizes,
            },
            separators=(",", ":"),
        )
    )


@pytest.mark.anyio
async def test_fixed_erp_focus_discovery_without_oracle(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    _save_fixture(tmp_path)
    approved = QUESTIONS[1]
    approve_candidate(tmp_path, "erp", _display(approved.source), _display(approved.target), 1)

    def no_source_connection(*args: object, **kwargs: object) -> None:
        raise AssertionError("Discovery benchmark must use only saved metadata")

    monkeypatch.setattr("psycopg.connect", no_source_connection)
    encoding = tiktoken.get_encoding("o200k_base")
    baseline = _full_structural_baseline(tmp_path)
    baseline_chars = len(baseline)
    baseline_tokens = len(encoding.encode(baseline))
    focus_ranks: list[int | None] = []
    first_graph_hits: list[bool] = []
    top_three_graph_hits: list[bool] = []
    selector_truncated: list[bool] = []
    selector_sizes: list[int] = []
    selector_tokens: list[int] = []
    one_followup_selector_tokens: list[int] = []
    first_graph_sizes: list[int] = []
    first_graph_tokens: list[int] = []
    top_three_graph_sizes: list[int] = []
    top_three_graph_tokens: list[int] = []
    first_labeled_evidence_ranks: list[int | None] = []
    progressive_labeled_sizes: list[int] = []
    async with Client(create_server(tmp_path)) as client:
        for question in QUESTIONS:
            result = await client.call_tool(
                "get_relevant_context", {"source": "erp", "task": question.task}
            )
            assert result.is_error is False and result.structured_content is None
            assert len(result.content) == 1 and isinstance(result.content[0], TextContent)
            response = result.content[0].text
            selector_sizes.append(len(response))
            selector_tokens.append(len(encoding.encode(response)))
            payload = json.loads(response)
            selector_truncated.append(payload["truncated"])
            focus_schema, focus_table = question.focus.split(".")
            focus_name = f"{quote_identifier(focus_schema)}.{quote_identifier(focus_table)}"
            table_names = [
                item["qualified_name"] for item in payload["objects"] if item["kind"] == "TABLE"
            ]
            followup_names = [
                item["arguments"]["name"]
                for item in payload["suggested_followups"]
                if item["tool"] == "get_table"
            ]
            focus_ranks.append(
                table_names.index(focus_name) + 1 if focus_name in table_names else None
            )
            assert followup_names == table_names[:3]
            assert followup_names
            one_followup = await client.call_tool(
                "get_relevant_context",
                {"source": "erp", "task": question.task, "max_followups": 1},
            )
            assert one_followup.is_error is False and len(one_followup.content) == 1
            assert isinstance(one_followup.content[0], TextContent)
            one_response = one_followup.content[0].text
            one_payload = json.loads(one_response)
            assert one_payload["objects"] == payload["objects"]
            assert one_payload["suggested_followups"] == payload["suggested_followups"][:1]
            one_followup_selector_tokens.append(len(encoding.encode(one_response)))
            graph_sizes: list[int] = []
            graph_token_sizes: list[int] = []
            graph_hits: list[bool] = []
            for name in followup_names:
                graph_result = await client.call_tool(
                    "get_graph_context", {"source": "erp", "name": name, "limit": 8}
                )
                assert graph_result.is_error is False
                assert len(graph_result.content) == 1
                assert isinstance(graph_result.content[0], TextContent)
                graph_response = graph_result.content[0].text
                graph_sizes.append(len(graph_response))
                graph_token_sizes.append(len(encoding.encode(graph_response)))
                graph_hits.append(_link_for_pair(json.loads(graph_response), question) is not None)
            first_graph_hits.append(graph_hits[0])
            top_three_graph_hits.append(any(graph_hits))
            first_graph_sizes.append(graph_sizes[0])
            first_graph_tokens.append(graph_token_sizes[0])
            top_three_graph_sizes.append(sum(graph_sizes))
            top_three_graph_tokens.append(sum(graph_token_sizes))
            first_evidence_rank = next(
                (index + 1 for index, hit in enumerate(graph_hits) if hit), None
            )
            first_labeled_evidence_ranks.append(first_evidence_rank)
            examined = first_evidence_rank or len(graph_sizes)
            progressive_labeled_sizes.append(selector_sizes[-1] + sum(graph_sizes[:examined]))

    assert all(rank is not None and rank <= 3 for rank in focus_ranks)
    assert sum(rank == 1 for rank in focus_ranks) >= 2
    assert top_three_graph_hits == [True, True, False, False, False]
    assert first_labeled_evidence_ranks == [1, 1, None, None, None]
    all_three_total = sum(selector_sizes) + sum(top_three_graph_sizes)
    progressive_total = sum(progressive_labeled_sizes)
    assert progressive_total < all_three_total
    assert baseline_tokens == 618
    assert len(one_followup_selector_tokens) == len(QUESTIONS)
    assert [
        selector + graph
        for selector, graph in zip(one_followup_selector_tokens, first_graph_tokens, strict=True)
    ] == [641, 729, 680, 557, 566]
    assert [
        selector + graphs
        for selector, graphs in zip(selector_tokens, top_three_graph_tokens, strict=True)
    ] == [905, 1082, 768, 821, 651]

    print(
        "ERP_DISCOVERY_BASELINE "
        + json.dumps(
            {
                "focus_table_ranks": focus_ranks,
                "full_schema_chars": baseline_chars,
                "full_schema_tokens_o200k_base": baseline_tokens,
                "first_graph_hits": first_graph_hits,
                "top_three_graph_hits": top_three_graph_hits,
                "first_labeled_evidence_ranks": first_labeled_evidence_ranks,
                "selector_truncated": selector_truncated,
                "selector_chars": selector_sizes,
                "selector_tokens_o200k_base": selector_tokens,
                "one_followup_selector_tokens_o200k_base": one_followup_selector_tokens,
                "one_followup_path_tokens_o200k_base": [
                    selector + graph
                    for selector, graph in zip(
                        one_followup_selector_tokens, first_graph_tokens, strict=True
                    )
                ],
                "selector_plus_first_graph_chars": [
                    selector + graph
                    for selector, graph in zip(selector_sizes, first_graph_sizes, strict=True)
                ],
                "selector_plus_top_three_graph_chars": [
                    selector + graphs
                    for selector, graphs in zip(selector_sizes, top_three_graph_sizes, strict=True)
                ],
                "selector_plus_first_graph_tokens_o200k_base": [
                    selector + graph
                    for selector, graph in zip(selector_tokens, first_graph_tokens, strict=True)
                ],
                "selector_plus_top_three_graph_tokens_o200k_base": [
                    selector + graphs
                    for selector, graphs in zip(
                        selector_tokens, top_three_graph_tokens, strict=True
                    )
                ],
                "idealized_task_evidence_stop_chars": progressive_labeled_sizes,
                "all_three_total_chars": all_three_total,
                "idealized_total_chars": progressive_total,
            },
            separators=(",", ":"),
        )
    )


@pytest.mark.anyio
async def test_manual_synonym_approval_changes_agent_style_erp_context(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    _save_fixture(tmp_path)
    inferred = QUESTIONS[1]
    approve_candidate(tmp_path, "erp", _display(inferred.source), _display(inferred.target), 1)
    synonym = QUESTIONS[2]
    reason = next(
        label.reason
        for label in LABELS
        if (label.source, label.target) == (synonym.source, synonym.target)
    )

    def no_source_connection(*args: object, **kwargs: object) -> None:
        raise AssertionError("Manual-link benchmark must use only saved metadata")

    monkeypatch.setattr("psycopg.connect", no_source_connection)

    async def measure(client: Client) -> tuple[list[str], list[int], list[bool], list[int]]:
        selectors: list[str] = []
        graph_chars: list[int] = []
        hits: list[bool] = []
        first_hit_ranks: list[int] = []
        for question in QUESTIONS:
            selected = await client.call_tool(
                "get_relevant_context", {"source": "erp", "task": question.task}
            )
            assert selected.is_error is False and len(selected.content) == 1
            assert isinstance(selected.content[0], TextContent)
            selectors.append(selected.content[0].text)
            followups = [
                item["arguments"]["name"]
                for item in json.loads(selectors[-1])["suggested_followups"]
                if item["tool"] == "get_table"
            ][:3]
            assert followups
            responses: list[str] = []
            matching_links: list[dict[str, object]] = []
            ranks: list[int] = []
            for rank, name in enumerate(followups, 1):
                graph = await client.call_tool(
                    "get_graph_context", {"source": "erp", "name": name, "limit": 8}
                )
                assert graph.is_error is False and len(graph.content) == 1
                assert isinstance(graph.content[0], TextContent)
                response = graph.content[0].text
                assert len(response) <= MAX_MCP_RESPONSE_CHARS
                payload = json.loads(response)
                assert payload["truncated"] is False
                assert "manual_truncated" not in payload
                responses.append(response)
                match = _link_for_pair(payload, question)
                if match is not None:
                    matching_links.append(match)
                    ranks.append(rank)
            if question is synonym and matching_links:
                assert all(
                    (link["origin"], link["status"], link["human_reason"])
                    == ("MANUAL", "APPROVED", reason)
                    and "metadata_score" not in link
                    and "foreign_key" not in link
                    for link in matching_links
                )
            graph_chars.append(sum(len(response) for response in responses))
            hits.append(bool(matching_links))
            first_hit_ranks.append(min(ranks) if ranks else 0)
        return selectors, graph_chars, hits, first_hit_ranks

    async with Client(create_server(tmp_path)) as client:
        before = await measure(client)
        propose_relationship(
            tmp_path, "erp", _display(synonym.source), _display(synonym.target), 1, reason
        )
        approve_manual_proposal(
            tmp_path, "erp", _display(synonym.source), _display(synonym.target), 1
        )
        after = await measure(client)

    before_selectors, before_graph_chars, before_hits, before_ranks = before
    after_selectors, after_graph_chars, after_hits, after_ranks = after
    assert before_selectors == after_selectors
    assert before_hits == [True, True, False, False, False]
    assert after_hits == [True, True, True, False, False]
    assert before_ranks == [1, 1, 0, 0, 0]
    assert 1 <= after_ranks[2] <= 3
    assert after_ranks[:2] == before_ranks[:2]
    assert after_ranks[3:] == before_ranks[3:]
    assert sum(after_graph_chars) > sum(before_graph_chars)
    assert all(
        after_size >= before_size
        for before_size, after_size in zip(before_graph_chars, after_graph_chars, strict=True)
    )
    print(
        "ERP_MANUAL_APPROVAL "
        + json.dumps(
            {
                "positive_links_before": "2/4",
                "positive_links_after": "3/4",
                "labeled_negative_exposure": "0/1",
                "first_hit_ranks_before": before_ranks,
                "first_hit_ranks_after": after_ranks,
                "selector_chars": [len(response) for response in before_selectors],
                "three_graph_chars_before": before_graph_chars,
                "three_graph_chars_after": after_graph_chars,
                "three_graph_total_delta": sum(after_graph_chars) - sum(before_graph_chars),
            },
            separators=(",", ":"),
        )
    )


@pytest.mark.anyio
async def test_scaled_erp_discovery_with_unrelated_tables(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    distractors = _distractor_columns()
    _save_fixture(tmp_path, distractors)
    approved = QUESTIONS[1]
    approve_candidate(tmp_path, "erp", _display(approved.source), _display(approved.target), 1)
    encoding = tiktoken.get_encoding("o200k_base")
    baseline = _full_structural_baseline(tmp_path, distractors)
    baseline_chars = len(baseline)
    baseline_tokens = len(encoding.encode(baseline))

    def no_source_connection(*args: object, **kwargs: object) -> None:
        raise AssertionError("Scaled benchmark must use only saved metadata")

    monkeypatch.setattr("psycopg.connect", no_source_connection)
    focus_ranks: list[int | None] = []
    candidate_search_truncated: list[bool] = []
    selector_sizes: list[int] = []
    selector_tokens: list[int] = []
    one_followup_selector_tokens: list[int] = []
    first_graph_sizes: list[int] = []
    first_graph_tokens: list[int] = []
    all_graph_sizes: list[int] = []
    all_graph_tokens: list[int] = []
    graph_hits: list[bool] = []
    async with Client(create_server(tmp_path)) as client:
        for question in QUESTIONS:
            result = await client.call_tool(
                "get_relevant_context", {"source": "erp", "task": question.task}
            )
            assert result.is_error is False and result.structured_content is None
            assert len(result.content) == 1 and isinstance(result.content[0], TextContent)
            response = result.content[0].text
            assert len(response) <= 4000
            selector_sizes.append(len(response))
            selector_tokens.append(len(encoding.encode(response)))
            payload = json.loads(response)
            candidate_search_truncated.append(payload["candidate_search_truncated"])
            focus_schema, focus_table = question.focus.split(".")
            focus_name = f"{quote_identifier(focus_schema)}.{quote_identifier(focus_table)}"
            table_names = [
                item["qualified_name"] for item in payload["objects"] if item["kind"] == "TABLE"
            ]
            focus_ranks.append(
                table_names.index(focus_name) + 1 if focus_name in table_names else None
            )
            followup_names = [
                item["arguments"]["name"]
                for item in payload["suggested_followups"]
                if item["tool"] == "get_table"
            ]
            assert followup_names == table_names[:3]
            one_followup = await client.call_tool(
                "get_relevant_context",
                {"source": "erp", "task": question.task, "max_followups": 1},
            )
            assert one_followup.is_error is False and len(one_followup.content) == 1
            assert isinstance(one_followup.content[0], TextContent)
            one_response = one_followup.content[0].text
            one_payload = json.loads(one_response)
            assert one_payload["objects"] == payload["objects"]
            assert one_payload["suggested_followups"] == payload["suggested_followups"][:1]
            one_followup_selector_tokens.append(len(encoding.encode(one_response)))
            sizes: list[int] = []
            token_sizes: list[int] = []
            hits: list[bool] = []
            for name in followup_names:
                graph_result = await client.call_tool(
                    "get_graph_context", {"source": "erp", "name": name}
                )
                assert graph_result.is_error is False and graph_result.structured_content is None
                assert len(graph_result.content) == 1
                assert isinstance(graph_result.content[0], TextContent)
                graph_response = graph_result.content[0].text
                assert len(graph_response) <= MAX_MCP_RESPONSE_CHARS
                sizes.append(len(graph_response))
                token_sizes.append(len(encoding.encode(graph_response)))
                hits.append(_link_for_pair(json.loads(graph_response), question) is not None)
            first_graph_sizes.append(sizes[0])
            first_graph_tokens.append(token_sizes[0])
            all_graph_sizes.append(sum(sizes))
            all_graph_tokens.append(sum(token_sizes))
            graph_hits.append(any(hits))

    assert len(distractors) == 400
    assert all(rank is not None and rank <= 3 for rank in focus_ranks)
    assert graph_hits == [True, True, False, False, False]
    assert (
        max(
            selector + graph
            for selector, graph in zip(selector_sizes, all_graph_sizes, strict=True)
        )
        < baseline_chars
    )
    assert all(
        selector + graphs < baseline_tokens
        for selector, graphs in zip(selector_tokens, all_graph_tokens, strict=True)
    )
    assert baseline_tokens == 6418
    assert [
        selector + graph
        for selector, graph in zip(one_followup_selector_tokens, first_graph_tokens, strict=True)
    ] == [641, 729, 651, 557, 575]
    assert [
        selector + graphs
        for selector, graphs in zip(selector_tokens, all_graph_tokens, strict=True)
    ] == [905, 1082, 824, 821, 660]
    print(
        "ERP_SCALED_DISCOVERY "
        + json.dumps(
            {
                "tables": 114,
                "columns": 420,
                "full_schema_chars": baseline_chars,
                "full_schema_tokens_o200k_base": baseline_tokens,
                "focus_table_ranks": focus_ranks,
                "candidate_search_truncated": candidate_search_truncated,
                "selector_chars": selector_sizes,
                "selector_tokens_o200k_base": selector_tokens,
                "one_followup_selector_tokens_o200k_base": one_followup_selector_tokens,
                "one_followup_path_tokens_o200k_base": [
                    selector + graph
                    for selector, graph in zip(
                        one_followup_selector_tokens, first_graph_tokens, strict=True
                    )
                ],
                "selector_plus_first_graph_chars": [
                    selector + graph
                    for selector, graph in zip(selector_sizes, first_graph_sizes, strict=True)
                ],
                "selector_plus_top_three_graph_chars": [
                    selector + graph
                    for selector, graph in zip(selector_sizes, all_graph_sizes, strict=True)
                ],
                "selector_plus_top_three_graph_tokens_o200k_base": [
                    selector + graph
                    for selector, graph in zip(selector_tokens, all_graph_tokens, strict=True)
                ],
                "labeled_graph_hits": graph_hits,
            },
            separators=(",", ":"),
        )
    )


@pytest.mark.anyio
async def test_erp_discovery_with_name_overlap(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    _save_fixture(tmp_path, _distractor_columns() + _overlap_columns())
    approved = QUESTIONS[1]
    approve_candidate(tmp_path, "erp", _display(approved.source), _display(approved.target), 1)

    def no_source_connection(*args: object, **kwargs: object) -> None:
        raise AssertionError("Name-overlap benchmark must use only saved metadata")

    monkeypatch.setattr("psycopg.connect", no_source_connection)
    focus_ranks: list[int | None] = []
    suggested_tables: list[list[str]] = []
    candidate_search_truncated: list[bool] = []
    labeled_graph_hits: list[bool] = []
    selector_sizes: list[int] = []
    async with Client(create_server(tmp_path)) as client:
        for question in QUESTIONS:
            result = await client.call_tool(
                "get_relevant_context", {"source": "erp", "task": question.task}
            )
            assert result.is_error is False and result.structured_content is None
            assert len(result.content) == 1 and isinstance(result.content[0], TextContent)
            response = result.content[0].text
            assert len(response) <= 4000
            selector_sizes.append(len(response))
            payload = json.loads(response)
            candidate_search_truncated.append(payload["candidate_search_truncated"])
            focus_schema, focus_table = question.focus.split(".")
            focus_name = f"{quote_identifier(focus_schema)}.{quote_identifier(focus_table)}"
            table_names = [
                item["qualified_name"] for item in payload["objects"] if item["kind"] == "TABLE"
            ]
            focus_ranks.append(
                table_names.index(focus_name) + 1 if focus_name in table_names else None
            )
            followup_names = [
                item["arguments"]["name"]
                for item in payload["suggested_followups"]
                if item["tool"] == "get_table"
            ]
            assert followup_names == table_names[:3]
            suggested_tables.append(followup_names)
            hits: list[bool] = []
            for name in followup_names:
                graph_result = await client.call_tool(
                    "get_graph_context", {"source": "erp", "name": name}
                )
                assert graph_result.is_error is False
                assert len(graph_result.content) == 1
                assert isinstance(graph_result.content[0], TextContent)
                hits.append(
                    _link_for_pair(json.loads(graph_result.content[0].text), question) is not None
                )
            labeled_graph_hits.append(any(hits))

    assert len(_overlap_columns()) == 60
    assert all(rank is not None and rank <= 3 for rank in focus_ranks)
    assert candidate_search_truncated[0] and candidate_search_truncated[3]
    assert labeled_graph_hits[:2] == [True, True]
    assert labeled_graph_hits[-1] is False
    print(
        "ERP_OVERLAP_DISCOVERY "
        + json.dumps(
            {
                "tables": 174,
                "columns": 480,
                "focus_table_ranks": focus_ranks,
                "suggested_tables": suggested_tables,
                "candidate_search_truncated": candidate_search_truncated,
                "selector_chars": selector_sizes,
                "labeled_graph_hits": labeled_graph_hits,
            },
            separators=(",", ":"),
        )
    )
