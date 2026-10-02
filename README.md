# Graphit

[![CI](https://github.com/OnurParapan/graphit-db/actions/workflows/ci.yml/badge.svg)](https://github.com/OnurParapan/graphit-db/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/graphit-db)](https://pypi.org/project/graphit-db/)
[![Python](https://img.shields.io/pypi/pyversions/graphit-db)](https://pypi.org/project/graphit-db/)

**Turn your database into compact context for AI coding agents.**

Graphit is a local-first database knowledge layer for Codex, Claude, and SQL
MCPs. It scans database structure with read-only credentials, stores a durable
project-local knowledge graph, and answers small questions so an agent does not
need the complete schema in every context window.

Graphit understands the database. Other agents use that knowledge to write
code or SQL.

## The problem

Real databases contain hundreds of tables, thousands of columns, incomplete
foreign keys, inconsistent names, and knowledge that lives in developers'
heads. Coding agents often compensate by repeatedly loading schema dumps or by
guessing joins and column names.

Graphit replaces repeated discovery with a persistent graph:

```text
Target database
      │ read-only scan
      ▼
Project-local Graphit knowledge graph
      │ bounded, progressive questions
      ▼
Codex / Claude / SQL MCP
      │ grounded context
      ▼
Application code or SQL produced by the consuming tool
```

## Current local workflow

```bash
# Install the Graphit application in an isolated environment:
pipx install graphit-db
# Then switch to your application repository:
cd /path/to/my-project
graphit init
graphit source add claims --host localhost --database claims_db \
  --username reader --credential-env CLAIMS_DB_PASSWORD
graphit source test claims
graphit scan --source claims
graphit candidates --source claims
graphit search "customer" --source claims
graphit show public.customer --source claims
graphit indexes public.customer --source claims
graphit show-view public.customer_summary --source claims
graphit relationships public.customer --source claims
graphit path public.invoice public.customer --source claims
graphit graph public.invoice --source claims --format dot
graphit graph public.invoice --source claims --format html --output invoice.html
```

Set `CLAIMS_DB_PASSWORD` in your environment before `source test` or `scan`.
Run a second scan before comparing versions with `graphit diff`.

`graphit init` creates project-local configuration and a versioned SQLite store
at `.graphit/graphit.db`. The unreleased next version also discovers PostgreSQL
URLs from the current environment and supported project `.env*` files, reports
only sanitized host/database/user facts, and keeps any password transient. It
asks before connecting unless `--yes` is given, forces a bounded read-only
verification session, and saves only a non-secret reference after success;
the verification also discovers accessible non-system schemas and init scans
them into the first immutable snapshot. `--no-connect` keeps discovery-only
behavior and `--no-scan` stops after verified source persistence. Later
`graphit scan --source NAME` commands create additional immutable
structural snapshots. PostgreSQL views and materialized views are searchable
with their catalog columns through `show-view`/`get_view`; `show` and FK graph
tools remain base-table-only, and view lineage is not inferred. To connect a
trusted Codex project explicitly, run `graphit mcp setup-codex` from that
project and restart Codex. Use `graphit mcp setup-codex --compact-tools` to
expose only the eight core discovery/table/relationship/graph tools and reduce
Codex's up-front tool-definition context; the full 14-tool profile remains the
default. The public distribution is `graphit-db`; the installed command remains
`graphit`. See
[MCP setup](https://github.com/OnurParapan/graphit-db/blob/main/docs/MCP.md#codex-project-setup-opt-in).
For Claude Code, use `graphit mcp setup-claude` instead; its project `.mcp.json`
entry has machine-specific absolute paths, so review it before committing.
`graphit candidates --source NAME` now previews conservative, evidence-labeled
`PENDING` relationship hypotheses without changing the saved graph.
`graphit review reject` locally suppresses one exact candidate across rescans;
it requires the current snapshot version and never changes a declared FK.
`graphit review restore` reverses that suppression without erasing review
history; the pair reappears only if it still qualifies as a candidate.
`graphit review approve` records a human-confirmed logical relationship for a
current candidate; it remains distinct from a database-declared FK and is not
yet included in MCP confirmed-FK tools.
`graphit review revoke` withdraws that approval without deleting its history;
a still-eligible pair returns to `PENDING`.
`graphit review propose SOURCE_COLUMN TARGET_COLUMN --source NAME
--snapshot-version N --reason TEXT` records a cross-name human hypothesis
locally as `PROPOSED`; it is not approved or exposed in graph/MCP context.
`graphit review proposals --source NAME` lists those local proposals with
their latest reasons and current-snapshot structural eligibility.
`graphit review approve-proposal SOURCE_COLUMN TARGET_COLUMN --source NAME
--snapshot-version N` explicitly approves an eligible manual proposal while
preserving its reason; this is a human assertion, not a declared FK.
The local `graphit graph TABLE --source NAME` output now includes current
manually approved links with `MANUAL` provenance and their human reasons;
MCP `get_graph_context` includes the same eligible manual links with a short
human reason; `get_relationships` remains declared-FK-only.
`graphit review revoke-proposal SOURCE_COLUMN TARGET_COLUMN --source NAME
--snapshot-version N` withdraws that approval without deleting its history;
re-approval requires an explicit new proposal.
`graphit search QUERY --source NAME` now searches the latest saved snapshot
locally, with bounded results and optional `--json` output.
`graphit show TABLE --source NAME` returns one table's saved columns, declared
keys, and outgoing foreign keys, without reconnecting to PostgreSQL.
`graphit relationships TABLE --source NAME` returns incoming and outgoing
declared foreign keys from that same local snapshot.
`graphit impact TABLE --source NAME` gives a bounded direct-FK dependent view;
it does not claim application or transitive impact.
`graphit impact-tree TABLE --source NAME` follows bounded, confirmed declared
FK paths to directly and indirectly referencing tables; it does not prove
application impact.
`graphit impact-column TABLE.COLUMN --source NAME` narrows that view to exact
incoming FK column pairs, including composite pair positions.
`graphit path FROM TO --source NAME` finds one bounded shortest route through
declared FK links, showing when a link is followed backwards.
`graphit snapshots --source NAME` lists completed local versions and UTC scan
completion times before a user chooses two versions to compare.
`graphit diff --source NAME --from-version OLD --to-version NEW` compares saved
schema, relation, column, declared key, and FK facts between two completed
local scans. `--offset` pages through large results; it does not infer renames,
indexes, view lineage, or review changes.
`graphit graph TABLE --source NAME` emits a one-hop JSON graph; `--format dot`
emits the same bounded graph as Graphviz text, and `--format html` creates an
offline SVG-and-list view. All distinguish database FKs
from still-eligible human-approved logical links, and none requires a
frontend framework, server, or source reconnection. `--output PATH` creates a
UTF-8 file without
overwriting; Graphviz is optional for rendering DOT.
`graphit mcp serve --project PATH` exposes compact task-focused context,
database overview, saved object search, table context, direct FK relationships and impact,
reviewed one-hop graph context, shortest FK paths, local version discovery, and explicit snapshot
diffs through a read-only stdio
MCP server. See
[`docs/MCP.md`](https://github.com/OnurParapan/graphit-db/blob/main/docs/MCP.md)
for the current tool contract and manual setup.

Install the public release with `pipx install graphit-db` or
`uv tool install graphit-db`. Contributors can instead run
`python -m pip install -e .` in this repository. Set the named password
environment variable before `graphit source test` or `graphit scan`, and use a
read-only PostgreSQL account. Codex/Claude setup is an explicit separate step;
`graphit init` does not change agent configuration. See
[release readiness](https://github.com/OnurParapan/graphit-db/blob/main/docs/RELEASE_READINESS.md)
for verified and outstanding gates, and
[RELEASING.md](https://github.com/OnurParapan/graphit-db/blob/main/docs/RELEASING.md)
for the protected PyPI procedure.

## Structural scope

Implemented today:

- saved sources, schemas, and tables,
- columns and data types,
- primary, unique, and foreign keys,
- declared FK relationships and bounded graph paths,
- compact lexical task context from saved object names.

Also implemented: catalog columns for PostgreSQL views and materialized views,
metadata-only inferred candidate preview with evidence and human review,
and direct declared-FK table/column impact, plus bounded transitive table-FK
reachability. Safe index metadata is now saved
in new snapshots and available on demand through `graphit indexes` or MCP
`get_index_context`. Not implemented:
view definitions or lineage, application impact, semantic ranking,
or automatic proof of inferred business relationships.

## Agent workflow

Instead of asking for 500 tables, an MCP-connected agent can now call:

```text
get_relevant_context(source="claims", task="change customer claim handling")
database_overview(source="claims", limit=10)
search_objects(source="claims", query="customer claim", limit=20)
get_table(source="claims", name="public.claim", limit=20)
get_relationships(source="claims", name="public.claim", limit=20)
find_path(source="claims", from_table="public.payment", to_table="public.customer")
```

Graphit can first return a few task-term name matches and exact follow-up
suggestions, then a small snapshot overview or selected table's bounded saved
structure, direct declared FK links, and—when needed—one bounded shortest
route. The consuming agent or SQL MCP then performs its own task. The first
task selector is lexical; semantic ranking remains planned.

Graphit never provides arbitrary SQL execution and is not a SQL generator.

## Local-first storage

Default project state:

```text
my-project/
├── graphit.toml            # safe, shareable configuration
└── .graphit/               # local generated state; ignored by Git
    ├── graphit.db          # SQLite knowledge graph
    ├── snapshots/
    ├── exports/
    └── state.json
```

No Docker, daemon, web server, or separate PostgreSQL metadata database is
required for the default workflow.

## Visualization

Graphit supports bounded one-hop ERD-style local exports without a web
application:

```bash
graphit graph public.claim --source claims --format json
graphit graph public.claim --source claims --format dot
graphit graph public.claim --source claims --format html --output claim.html
```

The HTML export is self-contained and distinguishes declared FKs from current
human-approved logical links. Pending hypotheses are not shown by default;
local search and relationship-type filters work without a server or remote
assets. Deeper graph traversal and expand/collapse are not implemented.

## Scope

PostgreSQL is the first source engine. The architecture supports later adapters,
but quality of one end-to-end path takes priority over breadth.

Graphit does not:

- mutate source databases,
- generate or optimize application SQL,
- execute arbitrary SQL,
- manage migrations,
- require an LLM for core behavior,
- require a hosted control plane.

## Repository

```text
graphit/
├── src/graphit/            # installable Python package
├── tests/                  # unit and contract tests
├── docs/                   # product and engineering contracts
├── pyproject.toml
└── AGENTS.md
```

See [ROADMAP.md](https://github.com/OnurParapan/graphit-db/blob/main/docs/ROADMAP.md)
for implementation status and
[ARCHITECTURE.md](https://github.com/OnurParapan/graphit-db/blob/main/docs/ARCHITECTURE.md)
for technical boundaries.

Copyright 2026 Onur Parapan. Graphit is licensed under
[Apache License 2.0](https://github.com/OnurParapan/graphit-db/blob/main/LICENSE);
see [NOTICE](https://github.com/OnurParapan/graphit-db/blob/main/NOTICE). The package is
published under the [`graphit-db`](https://pypi.org/project/graphit-db/)
distribution name; the product, import package, and command remain Graphit,
`graphit`, and `graphit`.
