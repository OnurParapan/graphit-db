"""Read-only stdio MCP tools backed by Graphit's local query services."""

import json
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import TypeAlias

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, TextContent, ToolAnnotations

from graphit import __version__
from graphit.graph_export import GraphProjection, table_graph
from graphit.queries import (
    DEFAULT_CONTEXT_CHARS,
    DEFAULT_CONTEXT_FOLLOWUPS,
    DEFAULT_CONTEXT_OBJECTS,
    DEFAULT_IMPACT_LIMIT,
    DEFAULT_PATH_HOPS,
    MAX_CONTEXT_CHARS,
    MAX_CONTEXT_FOLLOWUPS,
    MAX_CONTEXT_OBJECTS,
    MAX_OVERVIEW_LIMIT,
    MAX_PATH_HOPS,
    MIN_CONTEXT_CHARS,
    ColumnImpactContext,
    DatabaseOverview,
    ImpactContext,
    PathResult,
    QueryError,
    RelationshipContext,
    RelevantContext,
    SearchResult,
    TableContext,
    TableIndexContext,
    TransitiveImpactContext,
    ViewContext,
    column_impact,
    database_overview,
    find_table_path,
    get_relevant_context,
    list_table_indexes,
    relevant_context_payload,
    search_objects,
    show_table,
    show_view,
    table_impact,
    table_relationships,
    transitive_table_impact,
)
from graphit.snapshot_diff import DEFAULT_DIFF_LIMIT, SnapshotDiff, compare_snapshots
from graphit.snapshot_history import DEFAULT_HISTORY_LIMIT, SnapshotHistory, list_snapshots
from graphit.sources import ProjectNotInitialized, SourceError, SourceNotFound

MAX_MCP_SEARCH_LIMIT = 50
MAX_MCP_TABLE_LIMIT = 50
MAX_MCP_RELATIONSHIP_LIMIT = 50
MAX_MCP_GRAPH_LIMIT = 20
DEFAULT_MCP_TRANSITIVE_IMPACT_LIMIT = 5
MAX_MCP_TRANSITIVE_IMPACT_LIMIT = 20
DEFAULT_MCP_INDEX_LIMIT = 5
MAX_MCP_INDEX_LIMIT = 20
MAX_MCP_RESPONSE_CHARS = 12000

_QueryResult: TypeAlias = (
    DatabaseOverview
    | RelevantContext
    | SearchResult
    | TableContext
    | TableIndexContext
    | ViewContext
    | RelationshipContext
    | PathResult
    | GraphProjection
    | SnapshotDiff
    | SnapshotHistory
    | ImpactContext
    | ColumnImpactContext
    | TransitiveImpactContext
)

_READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)


def _compact_graph(projection: GraphProjection) -> dict[str, object]:
    """Keep agent context small without hiding relationship provenance."""

    links: list[dict[str, object]] = []
    for link in projection.links:
        item: dict[str, object] = {
            "source_table": link.source_table,
            "target_table": link.target_table,
            "column_pairs": link.column_pairs,
            "origin": link.origin,
            "status": link.status,
        }
        if (link.origin, link.status) == ("DATABASE", "CONFIRMED"):
            item.update(
                foreign_key=link.name,
                target_in_scope=link.target_in_scope,
                validated=link.validated,
                inherited=link.inherited,
            )
        elif (link.origin, link.status) == ("INFERRED", "APPROVED"):
            item.update(
                metadata_score=link.confidence,
                evidence=[
                    {"signal": evidence.signal, "score": evidence.score, "weight": evidence.weight}
                    for evidence in link.evidence
                ],
            )
        elif (link.origin, link.status) == ("MANUAL", "APPROVED") and link.reason:
            item["human_reason"] = link.reason
        else:
            raise ToolError("INVALID_GRAPH_LINK: Graph contains an unsupported relationship state.")
        links.append(item)
    payload: dict[str, object] = {
        "source_name": projection.source_name,
        "snapshot_version": projection.snapshot_version,
        "focus_table": projection.focus_table,
        "depth": projection.depth,
        "nodes": [
            {"qualified_name": node.qualified_name, "in_scope": node.in_scope}
            for node in projection.nodes
        ],
        "links": links,
        "truncated": projection.truncated,
        "fk_truncated": projection.fk_truncated,
        "approved_truncated": projection.approved_truncated,
    }
    if projection.manual_truncated:
        payload["manual_truncated"] = True
    return payload


def _result(call: Callable[[], _QueryResult]) -> CallToolResult:
    try:
        result = call()
        if isinstance(result, GraphProjection):
            payload = _compact_graph(result)
        elif isinstance(result, RelevantContext):
            payload = relevant_context_payload(result)
        else:
            payload = asdict(result)
    except QueryError as error:
        raise ToolError(f"{error.code}: {error}") from None
    except ProjectNotInitialized:
        raise ToolError("PROJECT_NOT_INITIALIZED: Run graphit init in the project root.") from None
    except SourceNotFound as error:
        raise ToolError(f"SOURCE_NOT_FOUND: {error}") from None
    except SourceError:
        raise ToolError("STORE_READ_FAILED: Could not read the local Graphit store.") from None
    compact = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(compact) > MAX_MCP_RESPONSE_CHARS:
        raise ToolError("CONTEXT_TOO_LARGE: Narrow the query or use smaller limits.")
    return CallToolResult(content=[TextContent(type="text", text=compact)])


def create_server(root: Path) -> MCPServer:
    """Build a project-scoped MCP server with no target database connectivity."""

    project_root = root.resolve()
    server = MCPServer(
        "Graphit",
        version=__version__,
        instructions=(
            "Use get_relevant_context for a bounded task-oriented starting point, "
            "database_overview for a compact source overview, search_objects to locate "
            "relevant objects, get_table for exact table facts, "
            "get_view for exact saved view columns, "
            "get_relationships for direct declared FKs, get_graph_context for a bounded "
            "neighborhood including clearly labeled human-approved logical links, "
            "and find_path for one bounded FK route. "
            "Use compare_snapshots with two explicit completed versions for saved schema drift. "
            "Use list_snapshots to discover completed versions before comparing. "
            "Use get_impact_context for direct declared-FK dependents only. "
            "Use get_transitive_impact for bounded multi-hop declared-FK reachability only. "
            "Use get_column_impact for exact incoming declared-FK column pairs. "
            "Use get_index_context only when index details are needed for an exact relation. "
            "Inspect suggested tables in order; expand only while task-specific "
            "evidence is insufficient. An empty neighborhood does not prove "
            "a relationship is absent. "
            "Results come from a local snapshot, not a live database. "
            "Human approval is not a declared FK; manual reasons are user assertions, "
            "and metadata scores are not probabilities."
        ),
        log_level="WARNING",
    )

    @server.tool(
        name="database_overview",
        description="Summarize latest local snapshot counts, schemas, and connected entry tables.",
        annotations=_READ_ONLY,
    )
    def database_overview_tool(source: str, limit: int = 10) -> CallToolResult:
        """Read a compact orientation summary without database access."""

        if not 1 <= limit <= MAX_OVERVIEW_LIMIT:
            raise ToolError(f"INVALID_LIMIT: Limit must be between 1 and {MAX_OVERVIEW_LIMIT}.")
        return _result(lambda: database_overview(project_root, source, limit))

    @server.tool(
        name="get_relevant_context",
        description=(
            "Find bounded local name matches for a short task. Inspect suggested "
            "tables in order; continue if task-specific evidence is missing."
        ),
        annotations=_READ_ONLY,
    )
    def get_relevant_context_tool(
        source: str,
        task: str,
        max_objects: int = DEFAULT_CONTEXT_OBJECTS,
        max_chars: int = DEFAULT_CONTEXT_CHARS,
        max_followups: int = DEFAULT_CONTEXT_FOLLOWUPS,
    ) -> CallToolResult:
        """Select bounded task context without an LLM or a live database call."""

        if not 1 <= max_objects <= MAX_CONTEXT_OBJECTS:
            raise ToolError(
                f"INVALID_BUDGET: max_objects must be between 1 and {MAX_CONTEXT_OBJECTS}."
            )
        if not MIN_CONTEXT_CHARS <= max_chars <= MAX_CONTEXT_CHARS:
            raise ToolError(
                "INVALID_BUDGET: max_chars must be between "
                f"{MIN_CONTEXT_CHARS} and {MAX_CONTEXT_CHARS}."
            )
        if not 1 <= max_followups <= MAX_CONTEXT_FOLLOWUPS:
            raise ToolError(
                f"INVALID_BUDGET: max_followups must be between 1 and {MAX_CONTEXT_FOLLOWUPS}."
            )
        return _result(
            lambda: get_relevant_context(
                project_root, source, task, max_objects, max_chars, max_followups
            )
        )

    @server.tool(
        name="search_objects",
        description="Find a small set of schema, table, or column names in the latest local scan.",
        annotations=_READ_ONLY,
    )
    def search_objects_tool(source: str, query: str, limit: int = 20) -> CallToolResult:
        """Search a named source's latest completed snapshot without database access."""

        if not 1 <= limit <= MAX_MCP_SEARCH_LIMIT:
            raise ToolError(f"INVALID_LIMIT: Limit must be between 1 and {MAX_MCP_SEARCH_LIMIT}.")
        return _result(lambda: search_objects(project_root, source, query, limit))

    @server.tool(
        name="get_table",
        description="Get bounded columns, declared keys, and outgoing FKs for one exact table.",
        annotations=_READ_ONLY,
    )
    def get_table_tool(source: str, name: str, limit: int = 20) -> CallToolResult:
        """Read one table's saved structural facts; no SQL or live source access."""

        if not 1 <= limit <= MAX_MCP_TABLE_LIMIT:
            raise ToolError(f"INVALID_LIMIT: Limit must be between 1 and {MAX_MCP_TABLE_LIMIT}.")
        return _result(lambda: show_table(project_root, source, name, limit))

    @server.tool(
        name="get_view",
        description=(
            "Get bounded catalog columns and explicit kind for one saved view or materialized "
            "view; no definition, FK, or lineage claim."
        ),
        annotations=_READ_ONLY,
    )
    def get_view_tool(source: str, name: str, limit: int = 20) -> CallToolResult:
        """Read a view's saved columns without live database access."""

        if not 1 <= limit <= MAX_MCP_TABLE_LIMIT:
            raise ToolError(f"INVALID_LIMIT: Limit must be between 1 and {MAX_MCP_TABLE_LIMIT}.")
        return _result(lambda: show_view(project_root, source, name, limit))

    @server.tool(
        name="get_index_context",
        description=(
            "Page saved index structure for one exact table or materialized view. "
            "Keys, INCLUDE columns, and expression placeholders are labeled; no live SQL."
        ),
        annotations=_READ_ONLY,
    )
    def get_index_context_tool(
        source: str, name: str, limit: int = DEFAULT_MCP_INDEX_LIMIT, offset: int = 0
    ) -> CallToolResult:
        """Read a small opt-in index page from the latest local snapshot."""

        if not 1 <= limit <= MAX_MCP_INDEX_LIMIT:
            raise ToolError(f"INVALID_LIMIT: Limit must be between 1 and {MAX_MCP_INDEX_LIMIT}.")
        return _result(lambda: list_table_indexes(project_root, source, name, limit, offset))

    @server.tool(
        name="get_relationships",
        description="Get bounded incoming and outgoing confirmed declared FKs for one table.",
        annotations=_READ_ONLY,
    )
    def get_relationships_tool(source: str, name: str, limit: int = 20) -> CallToolResult:
        """Read both FK directions from the latest local snapshot."""

        if not 1 <= limit <= MAX_MCP_RELATIONSHIP_LIMIT:
            raise ToolError(
                f"INVALID_LIMIT: Limit must be between 1 and {MAX_MCP_RELATIONSHIP_LIMIT}."
            )
        return _result(lambda: table_relationships(project_root, source, name, limit))

    @server.tool(
        name="get_impact_context",
        description=(
            "Get bounded direct table dependents through confirmed declared FKs only; "
            "not application dependencies, inferred links, or verified lineage."
        ),
        annotations=_READ_ONLY,
    )
    def get_impact_context_tool(
        source: str, name: str, limit: int = DEFAULT_IMPACT_LIMIT
    ) -> CallToolResult:
        """Read direct structural FK impact from the latest local snapshot."""

        return _result(lambda: table_impact(project_root, source, name, limit))

    @server.tool(
        name="get_transitive_impact",
        description=(
            "Get bounded shortest incoming declared-FK paths to dependent tables; "
            "structural reachability, not verified application impact or inferred lineage."
        ),
        annotations=_READ_ONLY,
    )
    def get_transitive_impact_tool(
        source: str,
        name: str,
        max_hops: int = DEFAULT_PATH_HOPS,
        limit: int = DEFAULT_MCP_TRANSITIVE_IMPACT_LIMIT,
    ) -> CallToolResult:
        """Read local multi-hop FK reachability with a small response budget."""

        if not 1 <= limit <= MAX_MCP_TRANSITIVE_IMPACT_LIMIT:
            raise ToolError(
                f"INVALID_LIMIT: Limit must be between 1 and {MAX_MCP_TRANSITIVE_IMPACT_LIMIT}."
            )
        return _result(lambda: transitive_table_impact(project_root, source, name, max_hops, limit))

    @server.tool(
        name="get_column_impact",
        description=(
            "Get bounded source columns directly referencing one exact saved column "
            "through confirmed declared FKs, with composite pair positions."
        ),
        annotations=_READ_ONLY,
    )
    def get_column_impact_tool(
        source: str, name: str, limit: int = DEFAULT_IMPACT_LIMIT
    ) -> CallToolResult:
        """Read exact column-level FK impact without a target DB connection."""

        return _result(lambda: column_impact(project_root, source, name, limit))

    @server.tool(
        name="get_graph_context",
        description=(
            "Get one bounded table neighborhood with declared FKs and current "
            "human-approved inferred or manual logical links, clearly separated by origin/status."
        ),
        annotations=_READ_ONLY,
    )
    def get_graph_context_tool(source: str, name: str, limit: int = 8) -> CallToolResult:
        """Return compact graph context, never pending hypotheses or live data."""

        if not 1 <= limit <= MAX_MCP_GRAPH_LIMIT:
            raise ToolError(f"INVALID_LIMIT: Limit must be between 1 and {MAX_MCP_GRAPH_LIMIT}.")
        return _result(
            lambda: table_graph(project_root, source, name, limit, limit, include_manual=True)
        )

    @server.tool(
        name="find_path",
        description="Find one bounded shortest route through confirmed declared table FKs.",
        annotations=_READ_ONLY,
    )
    def find_path_tool(
        source: str,
        from_table: str,
        to_table: str,
        max_hops: int = DEFAULT_PATH_HOPS,
    ) -> CallToolResult:
        """Search one deterministic route in the saved FK graph."""

        if not 0 <= max_hops <= MAX_PATH_HOPS:
            raise ToolError(f"INVALID_HOPS: Maximum hops must be between 0 and {MAX_PATH_HOPS}.")
        return _result(
            lambda: find_table_path(project_root, source, from_table, to_table, max_hops)
        )

    @server.tool(
        name="compare_snapshots",
        description=(
            "Page exact schema, relation, column, key, and FK drift between two explicit "
            "completed local versions; use offset for later pages, with no live database access."
        ),
        annotations=_READ_ONLY,
    )
    def compare_snapshots_tool(
        source: str,
        from_version: int,
        to_version: int,
        limit: int = DEFAULT_DIFF_LIMIT,
        offset: int = 0,
    ) -> CallToolResult:
        """Return exact saved structural changes through the shared diff service."""

        return _result(
            lambda: compare_snapshots(project_root, source, from_version, to_version, limit, offset)
        )

    @server.tool(
        name="list_snapshots",
        description=(
            "List bounded, newest-first completed local snapshot versions and completion times "
            "for one source; no live database access."
        ),
        annotations=_READ_ONLY,
    )
    def list_snapshots_tool(source: str, limit: int = DEFAULT_HISTORY_LIMIT) -> CallToolResult:
        """Discover explicit version numbers for local snapshot comparison."""

        return _result(lambda: list_snapshots(project_root, source, limit))

    return server
