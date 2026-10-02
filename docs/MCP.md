# Graphit — MCP Contract

## Purpose

Graphit's stdio MCP server lets coding agents query database knowledge before
they inspect full schema dumps, invent joins, or call a SQL-capable tool.

MCP is a read-only interface to Graphit's local knowledge store. It does not
expose arbitrary SQL execution, SQL generation, or target database mutation.

## Response principles

- Default to the latest successful snapshot.
- Return qualified names.
- Distinguish explicit, approved, and pending inferred edges.
- Include confidence and evidence summaries for inference.
- Bound objects, edges, paths, depth, and approximate response size.
- Summarize truncation and explain how to request more.
- Never return the entire schema by default.

## Initial tools

The implemented server exposes `get_relevant_context`, `database_overview`,
`search_objects`, `get_table`, `get_view`, `get_relationships`, `get_impact_context`,
`get_transitive_impact`, `get_column_impact`, `get_index_context`,
`get_graph_context`, `find_path`, `list_snapshots`, and `compare_snapshots`. During
development, install this repository locally (`python -m pip install -e .`);
the `graphit-db` distribution is not published yet. Run `graphit init`,
configure a source, and complete at least one `graphit scan --source NAME`
before starting the server:

```powershell
graphit mcp serve --project C:\path\to\project
```

Configure your MCP client to launch that command as a local stdio subprocess.
The project argument may be omitted when the working directory is inside the
initialized project. Starting the server manually does not modify Codex,
Claude, or any other agent settings. Most tools read the latest completed local
SQLite snapshot; `list_snapshots` reads completed version metadata and
`compare_snapshots` reads the two explicitly requested completed
versions. No tool opens a target database connection. Each response has a 12,000-character
JSON safety ceiling; narrow the query or lower `limit` if exceeded.
Successful tool results contain one compact JSON text block, not duplicate
structured and text copies, to keep agent context smaller.

## Codex project setup (opt-in)

From an initialized Graphit project, run:

```powershell
graphit mcp setup-codex
```

The default setup exposes all 14 tools. For the common schema-navigation path,
an explicit smaller profile is available:

```powershell
graphit mcp setup-codex --compact-tools
```

It writes Codex's supported `enabled_tools` allowlist for
`database_overview`, `get_relevant_context`, `search_objects`, `get_table`,
`get_view`, `get_relationships`, `get_graph_context`, and `find_path`. Graphit
still implements every tool; Codex simply omits the six specialist impact,
index, and snapshot tools from its model context. Local serialization of the
tool definitions is approximately 976 `o200k_base` tokens for this eight-tool
profile versus 1,777 for all 14. These are schema-only proxy counts, not client
or billed tokens.

Or pass `--project PATH`. This writes only that project's `.codex/config.toml`,
adding `[mcp_servers.graphit]` with a local stdio launcher. It uses the absolute
Python interpreter running the installed Graphit package and an absolute
`--project` path, so Codex does not depend on its current working directory or
on `graphit` being on its own PATH. The command does not scan the source database,
write credentials, change global Codex settings, or modify `AGENTS.md`. Existing
Codex config bytes and comments are preserved. Re-running the same setup is a
no-op; a different Graphit entry or invalid TOML is an error requiring manual
resolution. If the Python environment or project moves, run
`graphit mcp setup-codex --refresh` from the new environment. Refresh replaces
only an exact Graphit-generated launcher stanza; it rejects custom/edited
entries for manual review and preserves unrelated TOML bytes and comments.
The `--refresh` flag is explicit because the `graphit` server name alone does
not prove Graphit owns someone else's configuration.

Use `--refresh --compact-tools` to switch a generated full entry to compact;
use `--refresh` without it to return a generated compact entry to all tools.
Graphit recognizes only its exact generated allowlist. A manually edited
`enabled_tools` list is custom configuration and refresh refuses to overwrite
it. The allowlist behavior follows Codex's official
[configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference);
other clients are unchanged.

Codex loads project `.codex/config.toml` only when you trust that project.
Restart Codex after setup, then inspect its active MCP servers with `/mcp`.
Once a source has been scanned, Graphit's tools can answer from the local
snapshot. To undo setup, remove only the `[mcp_servers.graphit]` table and its
`command`/`args` lines from `.codex/config.toml`; leave other settings in place.
Do not delete `.graphit/` unless you intentionally want to discard snapshots.

The project-local format and trust requirement follow the
[official Codex MCP documentation](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)
and [configuration guide](https://learn.chatgpt.com/docs/config-file/config-basic).

## Claude Code project setup (opt-in)

From an initialized Graphit project, run:

```powershell
graphit mcp setup-claude
```

Or pass `--project PATH`. This adds `mcpServers.graphit` to the project's
`.mcp.json`; it does not write to `~/.claude.json`, modify `CLAUDE.md`, or
store database credentials. Existing JSON settings and other MCP entries are
retained. Re-running setup is a no-op; invalid JSON, duplicate keys, and a
different Graphit entry cause an error instead of an overwrite. The command
uses the same absolute installed-Python and project paths as Codex setup. After
a move, `graphit mcp setup-claude --refresh` replaces only a Graphit-shaped
stdio launcher with no extra properties; custom entries still require manual
review. Other JSON settings remain intact.

Claude Code treats `.mcp.json` as project-scoped and potentially shareable.
The generated absolute paths are **machine-specific**: review this entry before
committing `.mcp.json` to version control, and regenerate it after moving the
project or Python environment. In interactive Claude Code, review and approve
the project MCP server when prompted; check `/mcp` or `claude mcp list` for
connection status. A higher-precedence local Graphit server may override the
project entry. To undo setup, remove only the `graphit` object from
`mcpServers` in `.mcp.json`, preserving all other JSON properties. Graphit's
saved snapshots in `.graphit/` remain untouched.

Claude Code's [official MCP guide](https://code.claude.com/docs/en/mcp)
defines the project `.mcp.json` format and approval behavior. Its
[configuration troubleshooting guide](https://code.claude.com/docs/en/debug-your-config)
explains the path and approval checks.

### `database_overview` (implemented)

```json
{ "source": "claims", "limit": 10 }
```

Returns the latest completed snapshot version; in-scope schema and table
counts; separate external/partial schema and table counts; scanned column
count; confirmed declared table-FK count; and bounded `schemas` and
`entry_tables` lists with independent truncation flags. Each entry table
includes its qualified name and number of participating declared FKs.
Entry tables are ordered by that FK count descending, then qualified name;
this is a deterministic navigation hint, not a semantic importance score.
Schemas are ordered by qualified name. Both lists use the same `limit`, which
defaults to 10 and must be between 1 and 50. An empty completed snapshot
returns zero counts and empty lists; an unscanned source returns `NO_SNAPSHOT`.

### `get_relevant_context` (implemented, lexical v1)

```json
{
  "source": "claims",
  "task": "change customer identifier handling in claim ingestion",
  "max_objects": 8,
  "max_chars": 4000,
  "max_followups": 3
}
```

Returns a small set of schema/table/view/materialized-view/column name matches
from the latest local snapshot, each with `MATCHED_TASK_TERMS`, the actual matched task terms, scope,
and safe column flags when applicable. Unavailable fields (`data_type`,
`nullable`, `primary_key`, `unique_value`) are omitted from individual objects
instead of emitted as `null`; clients should treat absence as unknown/not
applicable, not `false`. It also includes the snapshot version,
used and unmatched terms, truncation flags, and up to three exact `get_table`
or `get_view` follow-up calls for selected tables or views. If there are no matches, it suggests
`database_overview`. It does not include relationships or infer semantic
relevance.

`max_followups` defaults to 3 and accepts 1–3. Setting it to 1 returns only
the highest-ranked table/view suggestion while retaining the same selected
objects, ranking, and truncation flags. This is an explicit navigation budget,
not evidence that other suggestions are irrelevant or that a relationship is
absent. A client can request more suggestions when the first result lacks
task-specific evidence.

The selector case-folds and de-duplicates words, removes a small English/Turkish
stopword list, and searches at most eight terms and 50 candidates per term.
Results rank by number of matched terms, in-scope status, object kind (table,
view, materialized view, column, schema), an exact object-name match to a task
term, then qualified name. The exact-name check breaks ties only; it is not a semantic relevance
claim. The task must be at most 300 characters.
`max_objects` defaults to 8 (range 1–20); `max_chars` defaults to 4,000 (range
500–12,000). It drops lower-ranked objects until the compact JSON fits; if even
the base response cannot fit, it returns `CONTEXT_TOO_LARGE`. A concurrent
rescan cannot mix snapshots: it returns `SNAPSHOT_CHANGED` for a retry. This
first version is lexical only; it does not translate between languages,
inspect comments/business values, or expand the graph.

### `search_objects` (implemented)

```json
{ "source": "claims", "query": "customer claim", "limit": 20 }
```

`source` is the saved local source name. All search terms must match. The result
includes `source_name`, `snapshot_version`, `matches`, and `truncated`.
`limit` defaults to 20 and must be between 1 and 50.

### `get_table` (implemented)

```json
{ "source": "claims", "name": "public.claim", "limit": 20 }
```

Returns the exact table's saved columns, declared primary/unique keys, and
outgoing declared foreign keys, each bounded by `limit` with separate truncation
flags. `limit` defaults to 20 and must be between 1 and 50. PostgreSQL name
rules apply: quote mixed-case or otherwise non-lowercase identifiers (for
example, `"public"."Customer"`). Ambiguous bare table names are rejected.

### `get_view` (implemented)

```json
{ "source": "claims", "name": "public.claim_summary", "limit": 20 }
```

Returns one exact saved `VIEW` or `MATERIALIZED_VIEW` kind and at most `limit`
catalog columns with their type and `not_null_declared` flag. `false` means no
catalog `NOT NULL` declaration, not proof that NULL values occur. The response
includes `columns_truncated`; `limit` defaults to 20 and must be 1–50. A
bare name matching multiple schemas returns `AMBIGUOUS_VIEW`; a table name
returns `VIEW_NOT_FOUND`. No live source connection, view definition, FK, or
lineage is included. The MCP response retains the 12,000-character ceiling.

### `get_index_context` (implemented)

```json
{ "source": "claims", "name": "public.claim", "limit": 5, "offset": 0 }
```

Returns an opt-in page of saved indexes for one exact table or materialized
view in the latest completed snapshot. The response includes `source_name`,
`snapshot_version`, `relation_kind`, `qualified_name`, `indexes`, total
`index_count`, `offset`, and `truncated`. Each index identifies its access
method, unique/primary/valid/ready/partial flags, ordered key positions, and
separate ordered `INCLUDE` positions. A `null` key column is an expression
placeholder, not a resolved column; Graphit does not expose its SQL expression
or partial predicate. `limit` defaults to 5 and must be 1–20; `offset` must be
0–100,000. Advance offset by the number of returned indexes while `truncated`
is true. PostgreSQL identifier quoting rules apply; ambiguous bare names and
non-indexable relations are rejected. The tool reuses `graphit indexes`' local
query, makes no source connection, and retains the 12,000-character ceiling.
Default overview and table context remain index-free.

### `get_relationships` (implemented)

```json
{ "source": "claims", "name": "public.claim", "limit": 20 }
```

Returns direct incoming and outgoing declared foreign keys. Each direction
has an independent `limit` and truncation flag. `limit` defaults to 20 and
must be between 1 and 50. Each edge includes the true source/target tables,
ordered column pairs, scope and validation flags, `origin: DATABASE`, and
`status: CONFIRMED`. Self-FKs appear in both directions. Inferred and pending
relationships are not included.

### `get_impact_context` (implemented)

```json
{"source":"claims","name":"public.customer","limit":20}
```

Returns only direct incoming confirmed declared FK links for one exact table
from the latest local snapshot. `foreign_key_count` counts those links, while
`dependent_table_count` counts distinct referencing tables; one table can have
several FKs. Each bounded `dependents` item includes exact source/target names,
ordered column pairs, validation/inheritance and `DATABASE`/`CONFIRMED`
provenance. `scope: DECLARED_FK_DIRECT` and `in_scope` prevent the response
from implying application dependencies or full knowledge of an external FK
target. `limit` defaults to 20, maximum 100; `truncated` indicates more FK
links. Self-references are included. No inferred, manual, transitive, or
application-level links are claimed. The 12,000-character MCP ceiling applies.

### `get_transitive_impact` (implemented)

```json
{"source":"claims","name":"public.customer","max_hops":4,"limit":5}
```

Walks incoming confirmed database-declared table FKs from one exact saved
table, returning each distinct dependent table's shortest route and hop count.
Each step retains the FK name, source/target, ordered column pairs,
validation/inheritance flags, and `DATABASE`/`CONFIRMED` provenance. The root
is excluded from the distinct-table count even if it has a self-FK; use
`get_impact_context` to see direct self-links. `max_hops` defaults to 4 and
allows 0–8; `limit` defaults to 5 and allows 1–20. `dependent_table_count`
counts all discovered tables within the hop bound and `truncated` marks a
shortened result list. The walk fails with `IMPACT_BUDGET_EXCEEDED` if it
would examine over 2,000 FK edges or visit over 500 tables. The 12,000-character
MCP ceiling also applies. `scope: DECLARED_FK_TRANSITIVE_TABLE` means
structural reachability, **not** proven application impact or column lineage.
Inferred and manual links are excluded; no live source connection is made.

### `get_column_impact` (implemented)

```json
{"source":"claims","name":"public.customer.id","limit":20}
```

Returns exact source columns whose confirmed declared FK pairs reference one
saved target column. Each item includes exact source/target table and column
names, FK name, ordered composite `pair_position`/`pair_count`, validation,
inheritance, and `DATABASE`/`CONFIRMED` provenance. The response labels its
scope `DECLARED_FK_COLUMN_DIRECT`, includes total `reference_count` and
`truncated`, and defaults to 20 items (maximum 100). It does not infer
application or transitive impact. A table with more than 5,000 incoming FK
edges fails closed with `IMPACT_BUDGET_EXCEEDED`; the usual 12,000-character
MCP ceiling also applies. No live source connection is made.

### `get_graph_context` (implemented)

```json
{ "source": "claims", "name": "public.claim", "limit": 8 }
```

Returns a compact one-hop table graph from the latest local snapshot. It
includes exact table nodes, ordered fully qualified column pairs, and links
with explicit `origin` and `status`. Declared FKs appear as
`DATABASE`/`CONFIRMED` with FK name and validation/scope flags. Only currently
eligible human-approved logical links appear as `INFERRED`/`APPROVED` with a
provisional `metadata_score` and compact evidence signals; this score is not
a calibrated probability or a database constraint. PENDING and rejected
hypotheses are never included. `get_relationships` remains FK-only.
Current structurally eligible, human-approved manual links also appear as
`MANUAL`/`APPROVED` with exact column pairs and a bounded `human_reason`.
This reason is a user's assertion, not measured overlap, a confidence score,
or a declared FK. `PROPOSED`, `REVOKED`, and stale manual links are omitted.
When the same pair is approved both manually and through inference, the
manual reason appears once. `get_relationships` remains declared-FK-only;
an empty one-hop neighborhood still does not prove no relationship exists.

`limit` defaults to 8 and must be 1–20; it applies independently to incoming
FKs, outgoing FKs, inferred-approved links, and manual-approved links. The
result reports `truncated`, `fk_truncated`, and `approved_truncated`, plus
`manual_truncated: true` only when manual links were cut off; absence of that
field means no manual truncation. A 12,000-character safety ceiling
still applies; lower `limit` when `CONTEXT_TOO_LARGE` occurs. No target
database connection or full-schema response is made.

### `find_path` (implemented)

```json
{
  "source": "claims",
  "from_table": "public.payment",
  "to_table": "public.customer",
  "max_hops": 4
}
```

Returns one deterministic shortest route through confirmed declared table FKs.
Traversal can go forward or reverse, and every step labels its traversal
direction separately from the FK's actual source/target and column pairs.
`max_hops` defaults to 4 and must be between 0 and 8. A traversal is also
bounded by 500 visited tables and 2,000 examined FK adjacencies. `PATH_NOT_FOUND`
and `PATH_BUDGET_EXCEEDED` are distinct errors; the latter is never reported
as proof that no path exists. Out-of-scope target stubs remain labeled partial.

### `list_snapshots` (implemented)

```json
{"source":"claims","limit":20}
```

Lists completed local snapshot versions for one source, newest first. The
response includes `source_name`, a bounded `snapshots` array with `version`,
`status: COMPLETED`, and UTC `completed_at`, plus total `snapshot_count` and
`truncated`. `limit` defaults to 20 and must be 1-100. A known but unscanned
source returns an empty list. Choose two explicit versions from this list for
`compare_snapshots`; the tool never reconnects to the target database.

### `compare_snapshots` (implemented)

```json
{
  "source": "claims",
  "from_version": 1,
  "to_version": 2,
  "limit": 100,
  "offset": 0
}
```

Compares two explicitly named completed local snapshots for schema, relation,
column, declared key, and FK definition changes. `source`, `from_version`,
and `to_version` are required; versions must be positive and ordered. `limit`
defaults to 100 and must be 1–500. The response preserves `source_name`, both
versions, `scope: SCHEMA_RELATION_COLUMN_KEY_FK`, `changes`, total
`change_count`, and `truncated`. A small `limit` does not reduce the total count;
`offset` defaults to 0 and must be 0-200,000. Advance it by the returned
`changes` length to read the next page; `truncated` means more changes after
this page, not that earlier pages have been read. An empty page beyond the
total is valid. Only offset 0 with `truncated: false` represents all changes.
The result does not claim rename detection, index drift, view lineage, or review
history. `SNAPSHOT_NOT_FOUND` covers missing or cross-source versions, and
`CONTEXT_TOO_LARGE` means the chosen page exceeds the 12,000-character MCP
ceiling; retry with a smaller `limit`. No target database connection is made.

## Planned tools (not yet available)

### `explain_relationship`

```json
{
  "source": "public.claim.customer_id",
  "target": "public.customer.id"
}
```

Returns database facts or inference evidence without raw sampled values.

## Later tools

```text
get_dependencies
get_dependents
broader impact_analysis
get_lineage
```

They are added only after their shared application behavior is tested.

## Progressive flow

Today, begin with `get_relevant_context` for a concrete task or
`database_overview` for broad orientation, then use exact follow-up tools.
Semantic ranking, general multi-hop graph expansion, and application lineage
are planned. The bounded declared-FK `get_transitive_impact` tool is available
for structural reachability, not verified application impact.
Inspect suggested exact tables in order. If a graph response contains the
specific declared or human-approved evidence needed for the task, further
suggestions need not be fetched; otherwise continue or search more narrowly.
An empty one-hop neighborhood is not evidence that a business relationship
does not exist. Graphit keeps all suggestions available rather than
automatically pruning them from one small benchmark.

```text
database_overview
  ↓ if needed
get_relevant_context / search_objects
  ↓ for selected objects
get_table / get_relationships / get_graph_context
  ↓ for a concrete route or change
find_path
```

## Agent instruction

Project wiring should add a concise instruction equivalent to:

> For database-dependent tasks, consult Graphit before requesting a full schema
> or guessing tables, columns, or joins. Start with overview/search and expand
> only when necessary. Inspect suggestions in order and stop only when the
> task-specific evidence is sufficient; do not infer absence from an empty
> neighborhood.

## Transport safety

- Stdio first.
- Protocol messages only on stdout.
- Logs only on stderr.
- Remote HTTP transport is out of MVP scope.
- No tool may accept arbitrary SQL for execution.
