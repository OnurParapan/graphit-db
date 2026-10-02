"""Bounded, read-only graph projections for local visualization."""

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from graphit.inference import (
    MAX_CANDIDATE_LIMIT,
    CandidateEvidence,
    current_approval_presence,
    preview_candidates,
)
from graphit.queries import MAX_RELATIONSHIP_LIMIT, QueryError, table_relationships
from graphit.review import current_manual_graph_links
from graphit.scanners.protocol import quote_identifier
from graphit.sources import require_store_path, show_source

MAX_DATABASE_GRAPH_NODES = 5_000
MAX_DATABASE_GRAPH_LINKS = 100_000


@dataclass(frozen=True)
class GraphNode:
    qualified_name: str
    in_scope: bool
    selected: bool
    kind: str = "TABLE"


@dataclass(frozen=True)
class GraphLink:
    source_table: str
    target_table: str
    column_pairs: tuple[tuple[str, str], ...]
    origin: str
    status: str
    name: str | None = None
    confidence: float | None = None
    evidence: tuple[CandidateEvidence, ...] = ()
    target_in_scope: bool = True
    validated: bool | None = None
    inherited: bool | None = None
    reason: str | None = None


@dataclass(frozen=True)
class GraphProjection:
    source_name: str
    snapshot_version: int
    focus_table: str
    depth: int
    nodes: tuple[GraphNode, ...]
    links: tuple[GraphLink, ...]
    truncated: bool
    fk_truncated: bool
    approved_truncated: bool
    manual_truncated: bool = False


@dataclass(frozen=True)
class DatabaseGraphProjection:
    source_name: str
    snapshot_version: int
    nodes: tuple[GraphNode, ...]
    links: tuple[GraphLink, ...]
    complete: bool = True
    scope: str = "DATABASE"


def _latest_snapshot(connection: sqlite3.Connection, source_name: str) -> tuple[int, int]:
    row = connection.execute(
        """SELECT snapshots.id, snapshots.version FROM snapshots
        JOIN sources ON sources.id = snapshots.source_id
        JOIN scan_runs ON scan_runs.id = snapshots.scan_run_id
        WHERE sources.name = ? AND scan_runs.status = 'COMPLETED'
        ORDER BY snapshots.version DESC LIMIT 1""",
        (source_name,),
    ).fetchone()
    if row is None:
        raise QueryError("NO_SNAPSHOT", f"Source '{source_name}' has no successful scan.")
    return int(row[0]), int(row[1])


def _table_scope(raw_metadata: str) -> bool:
    try:
        metadata = json.loads(raw_metadata)
    except (TypeError, json.JSONDecodeError):
        raise QueryError("STORE_READ_FAILED", "Local graph table data is invalid.") from None
    if not isinstance(metadata, dict):
        raise QueryError("STORE_READ_FAILED", "Local graph table data is invalid.")
    in_scope = metadata.get("in_scope", True)
    if not isinstance(in_scope, bool):
        raise QueryError("STORE_READ_FAILED", "Local graph table scope is invalid.")
    return in_scope


def _database_link(source: str, target: str, raw_metadata: str) -> GraphLink:
    try:
        metadata = json.loads(raw_metadata)
    except (TypeError, json.JSONDecodeError):
        raise QueryError("STORE_READ_FAILED", "Local graph relationship data is invalid.") from None
    if not isinstance(metadata, dict):
        raise QueryError("STORE_READ_FAILED", "Local graph relationship data is invalid.")
    name = metadata.get("constraint")
    pairs = metadata.get("column_pairs")
    target_in_scope = metadata.get("target_in_scope")
    validated = metadata.get("validated")
    inherited = metadata.get("inherited")
    if (
        not isinstance(name, str)
        or metadata.get("level") != "TABLE"
        or not isinstance(pairs, list)
        or not pairs
        or any(
            not isinstance(pair, list)
            or len(pair) != 2
            or not all(isinstance(item, str) for item in pair)
            for pair in pairs
        )
        or not isinstance(target_in_scope, bool)
        or not isinstance(validated, bool)
        or not isinstance(inherited, bool)
    ):
        raise QueryError("STORE_READ_FAILED", "Local graph relationship data is invalid.")
    return GraphLink(
        source_table=source,
        target_table=target,
        column_pairs=tuple(
            (
                f"{source}.{quote_identifier(pair[0])}",
                f"{target}.{quote_identifier(pair[1])}",
            )
            for pair in pairs
        ),
        origin="DATABASE",
        status="CONFIRMED",
        name=name,
        target_in_scope=target_in_scope,
        validated=validated,
        inherited=inherited,
    )


def database_graph(
    root: Path,
    source_name: str,
    *,
    node_limit: int = MAX_DATABASE_GRAPH_NODES,
    link_limit: int = MAX_DATABASE_GRAPH_LINKS,
) -> DatabaseGraphProjection:
    """Project every saved table and confirmed FK, or fail instead of truncating."""

    if not 1 <= node_limit <= MAX_DATABASE_GRAPH_NODES:
        raise QueryError(
            "INVALID_LIMIT", f"Database graph node limit must be 1-{MAX_DATABASE_GRAPH_NODES}."
        )
    if not 1 <= link_limit <= MAX_DATABASE_GRAPH_LINKS:
        raise QueryError(
            "INVALID_LIMIT", f"Database graph link limit must be 1-{MAX_DATABASE_GRAPH_LINKS}."
        )
    source = show_source(root, source_name)
    path = require_store_path(root)
    try:
        with closing(sqlite3.connect(path, timeout=5)) as connection:
            connection.execute("PRAGMA query_only = ON")
            connection.execute("BEGIN")
            snapshot_id, version = _latest_snapshot(connection, source.name)
            node_rows = connection.execute(
                """SELECT qualified_name, metadata_json FROM objects
                WHERE snapshot_id = ? AND object_type = 'TABLE'
                ORDER BY qualified_name LIMIT ?""",
                (snapshot_id, node_limit + 1),
            ).fetchall()
            if len(node_rows) > node_limit:
                raise QueryError(
                    "ERD_NODE_LIMIT_EXCEEDED",
                    f"Snapshot has more than {node_limit} tables; no partial ERD was created.",
                )
            link_rows = connection.execute(
                """SELECT source.qualified_name, target.qualified_name, edge.metadata_json
                FROM edges AS edge
                JOIN objects AS source ON source.id = edge.source_object_id
                  AND source.snapshot_id = edge.snapshot_id AND source.object_type = 'TABLE'
                JOIN objects AS target ON target.id = edge.target_object_id
                  AND target.snapshot_id = edge.snapshot_id AND target.object_type = 'TABLE'
                WHERE edge.snapshot_id = ? AND edge.edge_type = 'REFERENCES'
                  AND edge.origin = 'DATABASE' AND edge.status = 'CONFIRMED'
                ORDER BY source.qualified_name, target.qualified_name, edge.id LIMIT ?""",
                (snapshot_id, link_limit + 1),
            ).fetchall()
            if len(link_rows) > link_limit:
                raise QueryError(
                    "ERD_LINK_LIMIT_EXCEEDED",
                    f"Snapshot has more than {link_limit} foreign keys; "
                    "no partial ERD was created.",
                )
    except QueryError:
        raise
    except sqlite3.Error:
        raise QueryError("STORE_READ_FAILED", "Could not read the local graph store.") from None
    nodes = tuple(
        GraphNode(str(name), _table_scope(str(raw_metadata)), False)
        for name, raw_metadata in node_rows
    )
    links = tuple(
        _database_link(str(source_table), str(target_table), str(raw_metadata))
        for source_table, target_table, raw_metadata in link_rows
    )
    return DatabaseGraphProjection(source.name, version, nodes, links)


def table_graph(
    root: Path,
    source_name: str,
    reference: str,
    fk_limit: int = MAX_RELATIONSHIP_LIMIT,
    approved_limit: int = MAX_CANDIDATE_LIMIT,
    *,
    include_manual: bool = False,
) -> GraphProjection:
    """Project one exact table and its direct FK/human-approved neighbors."""

    if not 1 <= approved_limit <= MAX_CANDIDATE_LIMIT:
        raise QueryError("INVALID_LIMIT", f"Approved limit must be 1-{MAX_CANDIDATE_LIMIT}.")
    relationships = table_relationships(root, source_name, reference, fk_limit)
    review_version, has_approval = current_approval_presence(
        root, source_name, relationships.qualified_name
    )
    if relationships.snapshot_version != review_version:
        raise QueryError("SNAPSHOT_CHANGED", "Source was rescanned during graph projection; retry.")
    approved = (
        preview_candidates(
            root,
            source_name,
            approved_limit,
            include_ambiguous=True,
            adjacent_table=relationships.qualified_name,
            approved_only=True,
        )
        if has_approval
        else None
    )
    if approved is not None and relationships.snapshot_version != approved.snapshot_version:
        raise QueryError("SNAPSHOT_CHANGED", "Source was rescanned during graph projection; retry.")
    manual = (
        current_manual_graph_links(root, source_name, relationships.qualified_name, approved_limit)
        if include_manual
        else None
    )
    if manual is not None and relationships.snapshot_version != manual.snapshot_version:
        raise QueryError("SNAPSHOT_CHANGED", "Source was rescanned during graph projection; retry.")

    scopes = {relationships.qualified_name: relationships.in_scope}
    links: dict[tuple[str, str, tuple[tuple[str, str], ...], str, str | None], GraphLink] = {}
    for relation in (*relationships.incoming, *relationships.outgoing):
        scopes[relation.source_table] = True
        scopes[relation.target_table] = relation.target_in_scope
        link = GraphLink(
            source_table=relation.source_table,
            target_table=relation.target_table,
            column_pairs=tuple(
                (
                    f"{relation.source_table}.{quote_identifier(source_column)}",
                    f"{relation.target_table}.{quote_identifier(target_column)}",
                )
                for source_column, target_column in relation.column_pairs
            ),
            origin=relation.origin,
            status=relation.status,
            name=relation.name,
            target_in_scope=relation.target_in_scope,
            validated=relation.validated,
            inherited=relation.inherited,
        )
        key = (link.source_table, link.target_table, link.column_pairs, link.origin, link.name)
        links[key] = link
    for candidate in approved.candidates if approved is not None else ():
        scopes[candidate.source_table] = True
        scopes[candidate.target_table] = True
        link = GraphLink(
            source_table=candidate.source_table,
            target_table=candidate.target_table,
            column_pairs=((candidate.source_column, candidate.target_column),),
            origin=candidate.origin,
            status=candidate.status,
            confidence=candidate.confidence,
            evidence=candidate.evidence,
        )
        links[(link.source_table, link.target_table, link.column_pairs, link.origin, None)] = link
    for entry in manual.links if manual is not None else ():
        scopes[entry.source_table] = True
        scopes[entry.target_table] = True
        link = GraphLink(
            source_table=entry.source_table,
            target_table=entry.target_table,
            column_pairs=((entry.source_column, entry.target_column),),
            origin="MANUAL",
            status="APPROVED",
            reason=entry.reason,
        )
        links.pop(
            (link.source_table, link.target_table, link.column_pairs, "INFERRED", None),
            None,
        )
        links[(link.source_table, link.target_table, link.column_pairs, link.origin, None)] = link
    nodes = tuple(
        GraphNode(name, in_scope, name == relationships.qualified_name)
        for name, in_scope in sorted(scopes.items())
    )

    def sort_key(
        item: tuple[str, str, tuple[tuple[str, str], ...], str, str | None],
    ) -> tuple[str, str, str, str, tuple[tuple[str, str], ...]]:
        return item[0], item[1], item[3], item[4] or "", item[2]

    ordered_links = tuple(links[key] for key in sorted(links, key=sort_key))
    fk_truncated = relationships.incoming_truncated or relationships.outgoing_truncated
    approved_truncated = approved.truncated if approved is not None else False
    manual_truncated = manual.truncated if manual is not None else False
    return GraphProjection(
        source_name=relationships.source_name,
        snapshot_version=relationships.snapshot_version,
        focus_table=relationships.qualified_name,
        depth=1,
        nodes=nodes,
        links=ordered_links,
        truncated=fk_truncated or approved_truncated or manual_truncated,
        fk_truncated=fk_truncated,
        approved_truncated=approved_truncated,
        manual_truncated=manual_truncated,
    )
