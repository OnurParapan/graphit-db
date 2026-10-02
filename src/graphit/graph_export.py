"""Bounded, read-only table-neighborhood projection for local visualization."""

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
