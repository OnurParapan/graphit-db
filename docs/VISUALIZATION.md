# Graphit — Visualization

## Role

Visualization is a projection of Graphit's knowledge, not the core product or a
required server. CLI and MCP must remain fully useful without it.

## Initial exports

```bash
graphit graph public.orders --source erp
graphit graph public.orders --source erp --format dot
graphit graph public.orders --source erp --format html --output orders.html
graphit erd --source erp
```

The first implemented slice emits JSON to stdout for one exact table and its
direct neighbors. It includes confirmed declared FKs and still-eligible,
human-approved logical links; PENDING hypotheses are excluded. Each link retains
its origin/status, ordered fully qualified quoted column pair(s), and relevant
evidence or FK flags.
The output records snapshot version, focus, depth `1`, and explicit FK/approved
truncation flags. The hard caps are 100 incoming FKs, 100 outgoing FKs, and 50
approved links. Bare table names must be unambiguous. This command does not
contact the target database or write an export file.
When no current human approval touches the focus table, the projection skips
full-snapshot candidate inference and reads only the bounded local FK context
plus adjacent review history. If an approval exists, its current eligibility
is still verified by the inference rule before it appears as a link. This
validation reads only approved source columns and possible `id` targets, so an
approved neighborhood can work beyond the general preview's 2,000-table cap.
The targeted check retains its own explicit work limits and fails rather than
silently assuming that an old approval remains valid.

JSON remains the default projection contract. DOT is an implemented, deterministic
text rendering of exactly the same bounded nodes and links. It writes to stdout;
Graphviz is optional and not a Python dependency. If `dot` is installed, a user
can create an SVG with:

```bash
graphit graph public.orders --source erp --format dot | dot -Tsvg -o graph.svg
```

DOT quotes identifiers and labels, visibly marks truncation in the graph title,
uses a double border for the selected table and a dashed border for external
stubs, and distinguishes confirmed FKs from approved logical links using both
edge text and line/arrow style. HTML is now a static, self-contained local
viewer with an inline SVG neighborhood diagram and matching text lists for
tables, ordered column pairs, FK flags, and approval evidence. It embeds no
CDN or remote assets. One fixed inline script provides local search across
tables, columns, and constraints plus relationship-type filters. Its exact
SHA-256 is embedded in the local CSP; database metadata is escaped only into
markup/data attributes and never becomes executable code. With JavaScript
disabled, the complete SVG and accessible lists remain visible. The diagram is
an overview; long names can be shortened visually, while the lists retain exact
quoted names. Dense neighborhoods can be scrolled and are still bounded by the
projection caps. Expand/collapse, multi-hop depth, and richer layout remain
future goals.

All formats write to stdout by default. `--output PATH` explicitly creates a
UTF-8 file and refuses to overwrite an existing path; no browser is opened.
The HTML file can be opened locally in a browser without Graphviz or a server.

The separate `graphit erd --source NAME` command projects the complete latest
saved table/FK scope instead of a focus-table neighborhood. It includes every
saved table, out-of-scope FK target stubs, and every confirmed database FK,
with exact ordered column pairs in the matching accessible list. `graphit init`
creates this snapshot-named artifact automatically after its default successful
scan. This complete fact view deliberately excludes inferred/manual assertions.
It fails without creating a partial artifact above 5,000 table nodes or 100,000
FK links. The diagram uses a deterministic scrollable grid; dense databases are
primarily navigated with its local search and exact relationship list.

## Projection controls

- source and snapshot,
- schema filter,
- root table or column,
- bounded depth,
- object types,
- relationship status/origin,
- minimum confidence,
- maximum nodes and edges.

The agent-facing default remains progressive and bounded rather than returning
this whole diagram as context. The complete ERD is an explicit human artifact;
its safety limits fail closed instead of silently producing an incomplete view.

## Visual language

Nodes distinguish:

- table,
- view,
- materialized view,
- selected object.

Edges distinguish through more than color:

- explicit FK: solid line,
- approved logical relationship: solid line with approval marker,
- pending inference (future diagnostic opt-in): dashed line with confidence,
- dependency/lineage: directional alternate style.

## Interaction status

- Search tables, columns, and constraint text: implemented locally in HTML.
- Filter FK, inferred-approved, and manual-approved links: implemented.
- Expand/collapse neighborhoods: future.
- Inspect columns and keys.
- See edge origin, status, confidence, and evidence.
- Copy qualified names.
- Export the current projection.

## Accessibility

- Keyboard-reachable controls.
- Sufficient contrast.
- No meaning conveyed by color alone.
- A tabular/list representation matching the graph projection.

## Non-goals

- Required Next.js application.
- Hosted dashboard in MVP.
- Editing the source schema from the graph.
- Showing unbounded source business data.
