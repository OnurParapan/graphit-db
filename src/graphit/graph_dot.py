"""Deterministic Graphviz DOT rendering of a bounded local graph projection."""

from graphit.graph_export import GraphLink, GraphProjection


def _dot_string(value: str) -> str:
    """Quote an untrusted identifier or label as one DOT string token."""

    escaped: list[str] = []
    for character in value:
        if character == "\\":
            escaped.append("\\\\")
        elif character == '"':
            escaped.append('\\"')
        elif character == "\n":
            escaped.append("\\n")
        elif ord(character) < 32 or ord(character) == 127:
            escaped.append(" ")
        else:
            escaped.append(character)
    return '"' + "".join(escaped) + '"'


def _edge_label(link: GraphLink) -> str:
    pairs = "\n".join(f"{source} -> {target}" for source, target in link.column_pairs)
    if (link.origin, link.status) == ("DATABASE", "CONFIRMED"):
        flags = []
        if link.validated is False:
            flags.append("NOT VALID")
        if link.inherited:
            flags.append("INHERITED")
        heading = f"FK {link.name or '(unnamed)'}"
        if flags:
            heading += " [" + ", ".join(flags) + "]"
        return heading + "\n" + pairs
    if (link.origin, link.status) == ("INFERRED", "APPROVED"):
        score = f"; metadata score {link.confidence:.2f}" if link.confidence is not None else ""
        return f"HUMAN APPROVED (logical{score})\n{pairs}"
    if (link.origin, link.status) == ("MANUAL", "APPROVED") and link.reason:
        return f"MANUAL APPROVED (logical)\n{pairs}\nReason: {link.reason}"
    raise ValueError("DOT rendering accepts only confirmed FKs and approved logical links.")


def render_dot(projection: GraphProjection) -> str:
    """Render the same bounded nodes and links as JSON, without source access."""

    node_ids = {node.qualified_name: f"n{index}" for index, node in enumerate(projection.nodes)}
    flags = []
    if projection.fk_truncated:
        flags.append("FK")
    if projection.approved_truncated:
        flags.append("APPROVED")
    if projection.manual_truncated:
        flags.append("MANUAL")
    title = (
        f"Graphit | source {projection.source_name} | snapshot {projection.snapshot_version}"
        f" | focus {projection.focus_table} | depth {projection.depth}"
    )
    if flags:
        title += " | TRUNCATED: " + ", ".join(flags)
    lines = [
        "digraph graphit {",
        f'  graph [label={_dot_string(title)}, labelloc="t", rankdir="LR"];',
        "  node [shape=box];",
    ]
    for node in projection.nodes:
        label = node.qualified_name
        if not node.in_scope:
            label += "\n(outside scan scope)"
        attributes = [f"label={_dot_string(label)}"]
        if node.selected:
            attributes.append("peripheries=2")
        if not node.in_scope:
            attributes.append('style="dashed"')
        lines.append(f"  {node_ids[node.qualified_name]} [{', '.join(attributes)}];")
    for link in projection.links:
        source_id = node_ids.get(link.source_table)
        target_id = node_ids.get(link.target_table)
        if source_id is None or target_id is None:
            raise ValueError("Graph link endpoint is missing from projection nodes.")
        attributes = [f"label={_dot_string(_edge_label(link))}"]
        if link.origin == "DATABASE":
            attributes.extend(('style="solid"', 'arrowhead="normal"'))
        elif link.origin == "MANUAL":
            attributes.extend(('style="dashed,bold"', 'arrowhead="diamond"'))
        else:
            attributes.extend(('style="bold"', 'arrowhead="vee"'))
        lines.append(f"  {source_id} -> {target_id} [{', '.join(attributes)}];")
    lines.append("}")
    return "\n".join(lines) + "\n"
