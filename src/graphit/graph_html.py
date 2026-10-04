"""Offline interactive HTML/SVG rendering of one bounded graph projection."""

import base64
import hashlib
import math
from collections import Counter
from html import escape

from graphit.graph_export import DatabaseGraphProjection, GraphLink, GraphNode, GraphProjection

_STYLE = """
body{font:16px/1.5 system-ui,sans-serif;color:#17212b;background:#f7f9fc;margin:0}
main{max-width:1280px;margin:auto;padding:1.25rem}
h1,h2{line-height:1.2} .muted{color:#45576b}
.warning{border:2px solid #8a4b00;background:#fff4d9;padding:.75rem}
.legend{display:flex;flex-wrap:wrap;gap:1rem;margin:1rem 0}
.legend span{border-left:5px solid #274c77;padding-left:.5rem}
.legend .approved{border-color:#176b4b}
.legend .manual{border-color:#854d0e}
.graph{overflow:auto;max-height:70vh;border:1px solid #9bacc0;background:white}
svg{display:block;min-width:900px;max-width:none}
svg .edge-fk path{fill:none;stroke:#274c77;stroke-width:2}
svg .edge-approved path{fill:none;stroke:#176b4b;stroke-width:3}
svg .edge-manual path{fill:none;stroke:#854d0e;stroke-width:3;stroke-dasharray:8 4}
svg .edge-label{fill:#17212b;font-size:12px;font-weight:700}
svg .edge-label{paint-order:stroke;stroke:white;stroke-width:4}
svg .node rect{fill:white;stroke:#273b50;stroke-width:2}
svg .node.selected rect{stroke-width:4}
svg .node.external rect{stroke-dasharray:7 4}
svg .node text{fill:#17212b;font-size:13px}
svg .erd-node .table-body{fill:#fff;stroke:#273b50;stroke-width:2}
svg .erd-node.external .table-body{stroke-dasharray:7 4}
svg .erd-node .table-header{fill:#273b50;stroke:#273b50}
svg .erd-node .table-title{fill:#fff;font-size:13px;font-weight:700}
svg .erd-node .column-row{fill:#fff;stroke:#d7e0ea;stroke-width:1}
svg .erd-node .column-row.alt{fill:#f3f6fa}
svg .erd-node .column-name{font-size:12px;font-weight:600}
svg .erd-node .column-type{fill:#45576b;font-size:11px}
svg .erd-node .column-badge{fill:#274c77;font-size:10px;font-weight:800}
ol{padding-left:1.5rem} li{margin:.7rem 0;overflow-wrap:anywhere}
code{background:#e9eef5;padding:.1rem .25rem}
details{margin:.5rem 0} summary{cursor:pointer} summary:focus-visible{outline:3px solid #274c77}
.controls{display:flex;flex-wrap:wrap;gap:.75rem;align-items:end;padding:1rem;
border:1px solid #9bacc0;background:white;margin:1rem 0}
.controls label{display:grid;gap:.25rem;font-weight:700}
.controls input,.controls select,.controls button{font:inherit;padding:.45rem .6rem}
.controls input{min-width:min(28rem,70vw)}
[hidden]{display:none!important}.filter-status{min-height:1.5rem}
""".strip()

_SCRIPT = """(() => {
"use strict";
const search = document.querySelector("#graph-search");
const kind = document.querySelector("#relationship-filter");
const reset = document.querySelector("#reset-filters");
const status = document.querySelector("#filter-status");
const edges = [...document.querySelectorAll("svg [data-edge]")];
const nodes = [...document.querySelectorAll("svg [data-node]")];
const tableRows = [...document.querySelectorAll("[data-table]")];
const relationshipRows = [...document.querySelectorAll("[data-relationship]")];
function applyFilters() {
  const query = search.value.trim().toLocaleLowerCase();
  const requestedKind = kind.value;
  const visibleNodes = new Set();
  let visibleRelationships = 0;
  for (const edge of edges) {
    const visible = (!query || edge.dataset.search.includes(query)) &&
      (requestedKind === "all" || edge.dataset.kind === requestedKind);
    edge.toggleAttribute("hidden", !visible);
    if (visible) {
      visibleNodes.add(edge.dataset.source);
      visibleNodes.add(edge.dataset.target);
    }
  }
  for (const row of relationshipRows) {
    const visible = (!query || row.dataset.search.includes(query)) &&
      (requestedKind === "all" || row.dataset.kind === requestedKind);
    row.toggleAttribute("hidden", !visible);
    if (visible) visibleRelationships += 1;
  }
  const active = query !== "" || requestedKind !== "all";
  let visibleTables = 0;
  for (const node of nodes) {
    const visible = !active || node.dataset.selected === "true" ||
      visibleNodes.has(node.dataset.node) || (query && node.dataset.search.includes(query));
    node.toggleAttribute("hidden", !visible);
  }
  for (const row of tableRows) {
    const visible = !active || row.dataset.selected === "true" ||
      visibleNodes.has(row.dataset.node) || (query && row.dataset.search.includes(query));
    row.toggleAttribute("hidden", !visible);
    if (visible) visibleTables += 1;
  }
  status.textContent = `${visibleTables} tables and ${visibleRelationships} relationships shown.`;
}
search.addEventListener("input", applyFilters);
kind.addEventListener("change", applyFilters);
reset.addEventListener("click", () => {
  search.value = "";
  kind.value = "all";
  applyFilters();
  search.focus();
});
applyFilters();
})();"""

_SCRIPT_HASH = base64.b64encode(hashlib.sha256(_SCRIPT.encode("utf-8")).digest()).decode("ascii")


def _link_kind(link: GraphLink) -> tuple[str, str]:
    if (link.origin, link.status) == ("DATABASE", "CONFIRMED"):
        return "FK", "edge-fk"
    if (link.origin, link.status) == ("INFERRED", "APPROVED"):
        return "APPROVED", "edge-approved"
    if (link.origin, link.status) == ("MANUAL", "APPROVED") and link.reason:
        return "MANUAL", "edge-manual"
    raise ValueError("HTML rendering accepts only confirmed FKs and approved logical links.")


def _positions(projection: GraphProjection) -> tuple[dict[str, tuple[int, int]], int]:
    focus = projection.focus_table
    if not any(node.qualified_name == focus for node in projection.nodes):
        raise ValueError("Focus table is missing from projection nodes.")
    incoming = {
        link.source_table
        for link in projection.links
        if link.target_table == focus and link.source_table != focus
    }
    outgoing = {
        link.target_table
        for link in projection.links
        if link.source_table == focus and link.target_table != focus
    }
    other_nodes = sorted(
        node.qualified_name for node in projection.nodes if node.qualified_name != focus
    )
    left = [name for name in other_nodes if name in incoming and name not in outgoing]
    right = [name for name in other_nodes if name not in left]
    height = max(len(left), len(right), 1) * 86 + 160
    positions = {focus: (600, height // 2)}
    positions.update({name: (200, 110 + index * 86) for index, name in enumerate(left)})
    positions.update({name: (1000, 110 + index * 86) for index, name in enumerate(right)})
    return positions, height


def _node_svg(node: GraphNode, x: int, y: int) -> str:
    classes = ["node"]
    if node.selected:
        classes.append("selected")
    if not node.in_scope:
        classes.append("external")
    label = node.qualified_name
    if len(label) > 30:
        label = label[:29] + "…"
    scope = " (outside scan scope)" if not node.in_scope else ""
    return (
        f'<g class="{" ".join(classes)}" data-node="{escape(node.qualified_name)}" '
        f'data-search="{escape(node.qualified_name.lower())}" '
        f'data-selected="{str(node.selected).lower()}">'
        f"<title>{escape(node.qualified_name + scope)}</title>"
        f'<rect x="{x - 120}" y="{y - 25}" width="240" height="50" rx="8"/>'
        f'<text x="{x}" y="{y + 5}" text-anchor="middle">{escape(label)}</text>'
        "</g>"
    )


def _edge_svg(link: GraphLink, positions: dict[str, tuple[int, int]], offset: int) -> str:
    kind, css_class = _link_kind(link)
    source = positions.get(link.source_table)
    target = positions.get(link.target_table)
    if source is None or target is None:
        raise ValueError("Graph link endpoint is missing from projection nodes.")
    sx, sy = source
    tx, ty = target
    sy += offset
    ty += offset
    if source == target:
        path = (
            f"M {sx + 120} {sy - 12} C {sx + 220} {sy - 85}, "
            f"{sx + 220} {sy + 85}, {sx + 120} {sy + 12}"
        )
        label_x, label_y = sx + 205, sy
    else:
        x1 = sx + (120 if sx < tx else -120)
        x2 = tx + (-120 if sx < tx else 120)
        path = f"M {x1} {sy} L {x2} {ty}"
        label_x, label_y = (x1 + x2) // 2, (sy + ty) // 2 - 8
    heading = (
        f"FK {link.name or '(unnamed)'}"
        if kind == "FK"
        else "Human-approved manual link"
        if kind == "MANUAL"
        else "Human-approved logical link"
    )
    tooltip = (
        heading
        + ": "
        + "; ".join(
            f"{source_column} → {target_column}"
            for source_column, target_column in link.column_pairs
        )
    )
    search_text = " ".join(
        (
            link.name or "",
            link.source_table,
            link.target_table,
            *(item for pair in link.column_pairs for item in pair),
        )
    ).lower()
    return (
        f'<g class="{css_class}" data-edge data-kind="{kind.lower()}" '
        f'data-source="{escape(link.source_table)}" '
        f'data-target="{escape(link.target_table)}" '
        f'data-search="{escape(search_text)}"><title>{escape(tooltip)}</title>'
        f'<path d="{path}" marker-end="url(#{css_class}-arrow)"/>'
        f'<text class="edge-label" x="{label_x}" y="{label_y}" '
        f'text-anchor="middle">{kind}</text></g>'
    )


def _graph_svg(projection: GraphProjection) -> str:
    positions, height = _positions(projection)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 {height}" '
        f'width="1200" height="{height}" role="img" aria-labelledby="graph-title graph-desc">',
        '<title id="graph-title">One-hop database relationship graph</title>',
        '<desc id="graph-desc">Exact table and relationship details '
        "follow below the diagram.</desc>",
        '<defs><marker id="edge-fk-arrow" markerWidth="10" markerHeight="10" '
        'refX="8" refY="5" orient="auto"><path d="M 0 0 L 9 5 L 0 10 z" '
        'fill="#274c77"/></marker><marker id="edge-approved-arrow" '
        'markerWidth="10" markerHeight="10" refX="8" refY="5" orient="auto">'
        '<path d="M 0 0 L 9 5 L 0 10 z" fill="#176b4b"/></marker>'
        '<marker id="edge-manual-arrow" markerWidth="10" markerHeight="10" '
        'refX="8" refY="5" orient="auto"><path d="M 0 0 L 9 5 L 0 10 z" '
        'fill="#854d0e"/></marker></defs>',
    ]
    totals = Counter((link.source_table, link.target_table) for link in projection.links)
    seen: Counter[tuple[str, str]] = Counter()
    for link in projection.links:
        key = link.source_table, link.target_table
        offset = round((seen[key] - (totals[key] - 1) / 2) * 8)
        seen[key] += 1
        parts.append(_edge_svg(link, positions, offset))
    for node in projection.nodes:
        parts.append(_node_svg(node, *positions[node.qualified_name]))
    parts.append("</svg>")
    return "\n".join(parts)


_CARD_WIDTH = 300
_HEADER_HEIGHT = 38
_COLUMN_HEIGHT = 24
_CARD_GAP_X = 120
_CARD_GAP_Y = 80


def _database_positions(
    projection: DatabaseGraphProjection,
) -> tuple[dict[str, tuple[int, int, int]], int, int]:
    count = max(len(projection.nodes), 1)
    columns = min(math.ceil(math.sqrt(count)), 16)
    positions: dict[str, tuple[int, int, int]] = {}
    y = 50
    for row_start in range(0, len(projection.nodes), columns):
        row = projection.nodes[row_start : row_start + columns]
        heights = [_HEADER_HEIGHT + max(len(node.columns), 1) * _COLUMN_HEIGHT for node in row]
        row_height = max(heights, default=_HEADER_HEIGHT + _COLUMN_HEIGHT)
        for index, (node, card_height) in enumerate(zip(row, heights, strict=True)):
            x = 50 + index * (_CARD_WIDTH + _CARD_GAP_X)
            positions[node.qualified_name] = (x, y, card_height)
        y += row_height + _CARD_GAP_Y
    width = max(900, columns * (_CARD_WIDTH + _CARD_GAP_X) - _CARD_GAP_X + 100)
    height = max(260, y - _CARD_GAP_Y + 50)
    return positions, width, height


def _column_anchor(node: GraphNode, column_name: str, top: int) -> int:
    for index, column in enumerate(node.columns):
        if column.qualified_name == column_name:
            return top + _HEADER_HEIGHT + index * _COLUMN_HEIGHT + _COLUMN_HEIGHT // 2
    return top + _HEADER_HEIGHT // 2


def _database_node_svg(
    node: GraphNode,
    position: tuple[int, int, int],
    foreign_key_columns: frozenset[str],
) -> str:
    x, y, height = position
    classes = "erd-node" + (" external" if not node.in_scope else "")
    label = (
        node.qualified_name if len(node.qualified_name) <= 38 else node.qualified_name[:37] + "…"
    )
    search_text = " ".join(
        (node.qualified_name, *(f"{column.name} {column.data_type}" for column in node.columns))
    ).lower()
    parts = [
        f'<g class="{classes}" data-node="{escape(node.qualified_name)}" '
        f'data-search="{escape(search_text)}" data-selected="false">',
        f"<title>{escape(node.qualified_name)}</title>",
        f'<rect class="table-body" x="{x}" y="{y}" width="{_CARD_WIDTH}" '
        f'height="{height}" rx="8"/>',
        f'<rect class="table-header" x="{x}" y="{y}" width="{_CARD_WIDTH}" '
        f'height="{_HEADER_HEIGHT}" rx="8"/>',
        f'<text class="table-title" x="{x + 12}" y="{y + 24}">{escape(label)}</text>',
    ]
    if not node.columns:
        parts.append(
            f'<text class="column-type" x="{x + 12}" y="{y + _HEADER_HEIGHT + 17}">'
            "outside scanned columns</text>"
        )
    for index, column in enumerate(node.columns):
        row_y = y + _HEADER_HEIGHT + index * _COLUMN_HEIGHT
        row_class = "column-row alt" if index % 2 else "column-row"
        badges = []
        if column.primary_key:
            badges.append("PK")
        elif column.unique:
            badges.append("UQ")
        if column.qualified_name in foreign_key_columns:
            badges.append("FK")
        badge = " ".join(badges)
        display_name = column.name if len(column.name) <= 25 else column.name[:24] + "…"
        display_type = (
            column.data_type if len(column.data_type) <= 19 else column.data_type[:18] + "…"
        )
        nullable_type = display_type + ("?" if column.nullable else "")
        parts.extend(
            (
                f'<rect class="{row_class}" x="{x + 1}" y="{row_y}" '
                f'width="{_CARD_WIDTH - 2}" height="{_COLUMN_HEIGHT}"/>',
                f'<text class="column-badge" x="{x + 8}" y="{row_y + 16}">{escape(badge)}</text>',
                f'<text class="column-name" x="{x + 48}" y="{row_y + 16}">'
                f"{escape(display_name)}</text>",
                f'<text class="column-type" x="{x + _CARD_WIDTH - 8}" y="{row_y + 16}" '
                f'text-anchor="end">{escape(nullable_type)}</text>',
            )
        )
    parts.append("</g>")
    return "".join(parts)


def _database_edge_svg(
    link: GraphLink,
    nodes: dict[str, GraphNode],
    positions: dict[str, tuple[int, int, int]],
) -> str:
    kind, css_class = _link_kind(link)
    source = positions.get(link.source_table)
    target = positions.get(link.target_table)
    if source is None or target is None:
        raise ValueError("Database graph link endpoint is missing from projection nodes.")
    source_node = nodes[link.source_table]
    target_node = nodes[link.target_table]
    sx, source_top, _ = source
    tx, target_top, _ = target
    source_center = sx + _CARD_WIDTH // 2
    target_center = tx + _CARD_WIDTH // 2
    paths = []
    anchors = []
    label_xs = []
    for source_column, target_column in link.column_pairs:
        source_y = _column_anchor(source_node, source_column, source_top)
        target_y = _column_anchor(target_node, target_column, target_top)
        if link.source_table == link.target_table:
            x1 = sx + _CARD_WIDTH
            loop_x = x1 + 70
            path = f"M {x1} {source_y} C {loop_x} {source_y}, {loop_x} {target_y}, {x1} {target_y}"
            label_xs.append(loop_x)
        elif sx == tx:
            x1 = sx + _CARD_WIDTH
            outer_x = x1 + 70
            path = (
                f"M {x1} {source_y} C {outer_x} {source_y}, {outer_x} {target_y}, {x1} {target_y}"
            )
            label_xs.append(outer_x)
        else:
            left_to_right = source_center <= target_center
            x1 = sx + _CARD_WIDTH if left_to_right else sx
            x2 = tx if left_to_right else tx + _CARD_WIDTH
            middle = (x1 + x2) // 2
            path = f"M {x1} {source_y} C {middle} {source_y}, {middle} {target_y}, {x2} {target_y}"
            label_xs.append(middle)
        paths.append(f'<path d="{path}" marker-end="url(#{css_class}-arrow)"/>')
        anchors.append((source_y, target_y))
    label_x = min(label_xs)
    label_y = min((first + second) // 2 for first, second in anchors) - 7
    search_text = " ".join(
        (
            link.name or "",
            link.source_table,
            link.target_table,
            *(item for pair in link.column_pairs for item in pair),
        )
    ).lower()
    tooltip = f"FK {link.name or '(unnamed)'}: " + "; ".join(
        f"{source_column} → {target_column}" for source_column, target_column in link.column_pairs
    )
    label = f"FK · {link.name or '(unnamed)'}"
    if len(label) > 30:
        label = label[:29] + "…"
    return (
        f'<g class="{css_class}" data-edge data-kind="{kind.lower()}" '
        f'data-source="{escape(link.source_table)}" '
        f'data-target="{escape(link.target_table)}" '
        f'data-search="{escape(search_text)}"><title>{escape(tooltip)}</title>'
        + "".join(paths)
        + f'<text class="edge-label" x="{label_x}" y="{label_y}" '
        f'text-anchor="middle">{escape(label)}</text></g>'
    )


def _database_graph_svg(projection: DatabaseGraphProjection) -> str:
    positions, width, height = _database_positions(projection)
    nodes = {node.qualified_name: node for node in projection.nodes}
    foreign_key_columns = frozenset(
        source for link in projection.links for source, _target in link.column_pairs
    )
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" role="img" '
        'aria-labelledby="graph-title graph-desc">',
        '<title id="graph-title">Whole-database relationship graph</title>',
        '<desc id="graph-desc">Every saved table and confirmed database foreign key. '
        "Exact relationship details follow below the diagram.</desc>",
        '<defs><marker id="edge-fk-arrow" markerWidth="10" markerHeight="10" '
        'refX="8" refY="5" orient="auto"><path d="M 0 0 L 9 5 L 0 10 z" '
        'fill="#274c77"/></marker></defs>',
    ]
    parts.extend(_database_edge_svg(link, nodes, positions) for link in projection.links)
    parts.extend(
        _database_node_svg(node, positions[node.qualified_name], foreign_key_columns)
        for node in projection.nodes
    )
    parts.append("</svg>")
    return "\n".join(parts)


def _relationship_list(projection: GraphProjection) -> str:
    if not projection.links:
        return "<p>No confirmed or human-approved relationships in this neighborhood.</p>"
    items = []
    for link in projection.links:
        kind, _ = _link_kind(link)
        heading = (
            f"FK {link.name or '(unnamed)'}"
            if kind == "FK"
            else "Human-approved manual link"
            if kind == "MANUAL"
            else "Human-approved logical link"
        )
        facts = []
        if kind == "FK":
            if link.validated is False:
                facts.append("not validated")
            if link.inherited:
                facts.append("inherited")
            if not link.target_in_scope:
                facts.append("target outside scan scope")
        elif link.confidence is not None:
            facts.append(f"metadata score {link.confidence:.2f}; not a probability")
        if kind == "MANUAL" and link.reason:
            facts.append(f"Human reason: {link.reason}")
        pairs = "".join(
            f"<li><code>{escape(source)}</code> → <code>{escape(target)}</code></li>"
            for source, target in link.column_pairs
        )
        evidence = ""
        if link.evidence:
            signals = "".join(
                f"<li>{escape(item.signal)}: {item.score:.2f} × {item.weight:.2f}"
                f" — {escape(item.detail)}</li>"
                for item in link.evidence
            )
            evidence = f"<details><summary>Metadata evidence</summary><ul>{signals}</ul></details>"
        note = f"<p>{escape('; '.join(facts))}</p>" if facts else ""
        search_text = " ".join(
            (
                heading,
                link.source_table,
                link.target_table,
                *(item for pair in link.column_pairs for item in pair),
                link.reason or "",
            )
        ).lower()
        items.append(
            f'<li data-relationship data-kind="{kind.lower()}" '
            f'data-search="{escape(search_text)}"><strong>{escape(heading)}</strong> '
            f"<code>{escape(link.source_table)}</code> → "
            f"<code>{escape(link.target_table)}</code>{note}<ol>{pairs}</ol>{evidence}</li>"
        )
    return "<ol>" + "".join(items) + "</ol>"


def render_html(projection: GraphProjection) -> str:
    """Render a complete offline document with hash-authorized local interaction."""

    warnings = []
    if projection.fk_truncated:
        warnings.append("foreign keys")
    if projection.approved_truncated:
        warnings.append("approved links")
    if projection.manual_truncated:
        warnings.append("manual links")
    warning = (
        '<p class="warning" role="status">Partial graph: more '
        + escape(" and ".join(warnings))
        + " exist beyond the safety limit.</p>"
        if warnings
        else ""
    )
    nodes = "".join(
        f'<li data-table data-node="{escape(node.qualified_name)}" '
        f'data-search="{escape(node.qualified_name.lower())}" '
        f'data-selected="{str(node.selected).lower()}"><code>'
        f"{escape(node.qualified_name)}</code>"
        + (" — selected" if node.selected else "")
        + (" — outside scan scope" if not node.in_scope else "")
        + "</li>"
        for node in projection.nodes
    )
    return (
        "\n".join(
            (
                "<!doctype html>",
                '<html lang="en"><head><meta charset="utf-8">',
                '<meta name="viewport" content="width=device-width, initial-scale=1">',
                '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
                f"style-src 'unsafe-inline'; script-src 'sha256-{_SCRIPT_HASH}'; "
                "img-src data:; object-src 'none'; base-uri 'none'\">",
                f"<title>Graphit: {escape(projection.focus_table)}</title>",
                f"<style>{_STYLE}</style></head><body><main>",
                f"<h1>Graphit: {escape(projection.focus_table)}</h1>",
                f'<p class="muted">Source <code>{escape(projection.source_name)}</code> · '
                f"snapshot {projection.snapshot_version} · depth {projection.depth} · "
                f"{len(projection.nodes)} tables · {len(projection.links)} links</p>",
                warning,
                '<p class="legend"><span>FK: database-confirmed</span>'
                '<span class="approved">APPROVED: human logical link, not a database FK</span>'
                '<span class="manual">MANUAL: human-approved reason, not a database FK</span></p>',
                '<section class="controls" aria-label="Graph filters">'
                '<label for="graph-search">Search tables, columns, or constraints'
                '<input id="graph-search" type="search" autocomplete="off"></label>'
                '<label for="relationship-filter">Relationship type'
                '<select id="relationship-filter"><option value="all">All</option>'
                '<option value="fk">Database FK</option>'
                '<option value="approved">Approved inferred</option>'
                '<option value="manual">Approved manual</option></select></label>'
                '<button id="reset-filters" type="button">Reset</button></section>',
                '<p id="filter-status" class="muted filter-status" aria-live="polite"></p>',
                '<noscript><p class="warning">Search and filters require browser JavaScript; '
                "the complete accessible lists remain available below.</p></noscript>",
                f'<div class="graph">{_graph_svg(projection)}</div>',
                "<h2>Tables</h2><ul>" + nodes + "</ul>",
                "<h2>Relationships and columns</h2>" + _relationship_list(projection),
                f"<script>{_SCRIPT}</script>",
                "</main></body></html>",
            )
        )
        + "\n"
    )


def render_database_html(projection: DatabaseGraphProjection) -> str:
    """Render one complete saved database graph as a self-contained HTML document."""

    if not projection.complete:
        raise ValueError("Database HTML rendering refuses incomplete projections.")
    nodes = "".join(
        f'<li data-table data-node="{escape(node.qualified_name)}" '
        f'data-search="{escape(node.qualified_name.lower())}" data-selected="false"><code>'
        f"{escape(node.qualified_name)}</code>"
        + (" â€” outside scan scope" if not node.in_scope else "")
        + "</li>"
        for node in projection.nodes
    )
    relationships = (
        _relationship_list(
            GraphProjection(
                projection.source_name,
                projection.snapshot_version,
                "whole database",
                0,
                projection.nodes,
                projection.links,
                False,
                False,
                False,
            )
        )
        if projection.links
        else "<p>No confirmed database foreign keys in this snapshot.</p>"
    )
    return (
        "\n".join(
            (
                "<!doctype html>",
                '<html lang="en"><head><meta charset="utf-8">',
                '<meta name="viewport" content="width=device-width, initial-scale=1">',
                '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
                f"style-src 'unsafe-inline'; script-src 'sha256-{_SCRIPT_HASH}'; "
                "img-src data:; object-src 'none'; base-uri 'none'\">",
                f"<title>Graphit ERD: {escape(projection.source_name)}</title>",
                f"<style>{_STYLE}</style></head><body><main>",
                f"<h1>Graphit whole-database ERD: {escape(projection.source_name)}</h1>",
                f'<p class="muted">Snapshot {projection.snapshot_version} Â· complete saved scope '
                f"Â· {len(projection.nodes)} tables Â· {len(projection.links)} confirmed FKs</p>",
                '<p class="legend"><span>FK: database-confirmed</span>'
                "<span>Dashed table: referenced outside scan scope</span></p>",
                '<section class="controls" aria-label="Graph filters">'
                '<label for="graph-search">Search tables, columns, or constraints'
                '<input id="graph-search" type="search" autocomplete="off"></label>'
                '<label for="relationship-filter">Relationship type'
                '<select id="relationship-filter"><option value="all">All</option>'
                '<option value="fk">Database FK</option></select></label>'
                '<button id="reset-filters" type="button">Reset</button></section>',
                '<p id="filter-status" class="muted filter-status" aria-live="polite"></p>',
                '<noscript><p class="warning">Search and filters require browser JavaScript; '
                "the complete accessible lists remain available below.</p></noscript>",
                f'<div class="graph">{_database_graph_svg(projection)}</div>',
                "<h2>Tables</h2><ul>" + nodes + "</ul>",
                "<h2>Confirmed foreign keys and columns</h2>" + relationships,
                f"<script>{_SCRIPT}</script>",
                "</main></body></html>",
            )
        )
        + "\n"
    )
