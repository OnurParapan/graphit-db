# Graphit — Architecture

## Style

Graphit is an installable local-first Python application with clean internal
boundaries. It is not a client/server platform by default.

```text
CLI ───────────────┐
                   │
stdio MCP ─────────┼──► application use cases ─► domain
                   │             │                 │
graph export ──────┘             ├──► SQLite store │
                                 └──► source scanner adapters
```

Dependencies point inward. CLI, MCP, and visualization contain no duplicated
scan, traversal, ranking, or review logic.

## Runtime components

### CLI

The primary human interface. Typer commands initialize projects, configure
sources, scan, inspect status, query the graph, export visualizations, and start
the stdio MCP server.

### Application services

Use cases coordinate domain rules and ports:

- initialize project,
- test source connection,
- scan metadata,
- create snapshot,
- search objects,
- find paths and neighborhoods,
- select relevant context under a budget,
- review inferred relationships,
- calculate impact,
- export a graph projection.

### Domain

Pure typed models and rules for database objects, edges, evidence, snapshots,
review status, traversal bounds, and context budgets. Domain code does not know
about Typer, MCP transports, SQLite, or PostgreSQL drivers.

### SQLite store

The default project-local knowledge store at `.graphit/graphit.db`.

SQLite provides:

- transactional snapshot writes,
- indexed node/edge lookup,
- recursive CTE support,
- FTS search where available,
- a single portable local file,
- no service lifecycle.

Internal schema migrations use a small versioned migration runner. Alembic and
SQLAlchemy are not required unless later measurements demonstrate value.

### Source scanners

Database-specific adapters implement a shared protocol. PostgreSQL is first.

Current initial interface:

```python
class DatabaseScanner(Protocol):
    def scan_metadata(self, source: SourceConfig, scope: ScanScope) -> MetadataSnapshot: ...
```

The PostgreSQL implementation reads selected schemas, ordinary and partitioned
tables, views, materialized views, their live columns, and declared table
primary/unique/foreign-key constraints from `pg_catalog`. The scanner now
also returns bounded index metadata (ordered key/include columns,
expression placeholders, uniqueness, partial/valid/ready flags). The snapshot
writer persists these safe structural facts as `INDEX` objects and positioned
column edges within the same immutable SQLite transaction. Explicit bounded
Explicit bounded CLI and MCP index lookup is available; default CLI/MCP
context does not expose an index dump. View definitions
and lineage remain later work. The existing table limit includes all four scanned
relation kinds and fails closed if exceeded.
Configured schema names are query parameters, never SQL fragments. Each table
and column query uses a hard result cap and fails on overflow instead of
returning a partial result. Scans use the same read-only startup settings and
timeouts as connection verification, plus one repeatable-read transaction for
a consistent catalog view. Adapter-specific I/O does not leak into
the domain-facing metadata types.

For an init-discovered source, the connection verification SELECT also returns
an ordered, bounded list of accessible non-system schemas. Init persists that
scope only after successful read-only verification, then invokes the existing
scan application service; it does not duplicate scanner logic in the CLI.

### MCP

The initial transport is stdio. MCP tools are thin adapters over application
services and default to the latest successful snapshot. Every response is
bounded and distinguishes explicit facts from inference.

No arbitrary SQL tool is exposed.

### Visualization

Graph projections are produced from the same application queries used by CLI
and MCP. Initial formats are JSON and DOT; a self-contained interactive HTML
ERD is added after graph correctness.

## Project state

```text
graphit.toml
.graphit/
├── graphit.db
├── state.json
├── snapshots/
└── exports/
```

`graphit.toml` is safe to commit and references credential environment variable
names, never plaintext secrets. `.graphit/` is generated local state and should
normally remain ignored by Git.

## Scan lifecycle

```text
CREATED
  → CONNECTING
  → READING_METADATA
  → NORMALIZING
  → BUILDING_EXPLICIT_GRAPH
  → GENERATING_CANDIDATES (when enabled)
  → WRITING_SNAPSHOT
  → COMPLETED
```

Any state can transition to `FAILED` with a stable error code and sanitized
message. A failed scan never becomes the latest successful snapshot.

## Snapshot semantics

Every successful scan creates an immutable logical snapshot. MVP may perform a
full selected-schema metadata scan. Correctness comes before incremental
optimization.

Queries use the latest successful snapshot unless a snapshot is specified.
Review decisions are stored independently and can be reconciled to new snapshots
through stable logical object keys.

## Agent integration

Agent integration is explicit and project-scoped. `graphit mcp setup-codex`
and `graphit mcp setup-claude` add local MCP launchers after project initialization:

- an MCP command pointing to `graphit mcp serve`,
- no automatic AGENTS/CLAUDE instruction changes in this slice,
- no global configuration mutation.

Any later agent instructions require separate scoped design and current
official client-format verification. Claude's `.mcp.json` is potentially
shareable, but the generated absolute launcher is machine-specific.

## Scalability strategy

Initial target:

- thousands of tables,
- hundreds of thousands of columns,
- bounded neighborhoods and path depth,
- no all-pairs inference,
- pagination and response budgets everywhere.

SQLite remains the default until benchmarks show a concrete local workload it
cannot satisfy. A future shared/hosted store is an optional deployment mode, not
an MVP prerequisite.

## Packaging

The package targets Python 3.11+ and exposes a `graphit` console script. Primary
installation paths are `pipx install graphit-db` and
`uv tool install graphit-db`. The installed command and import package remain
`graphit`.
Standalone binaries may follow after core behavior and release automation are
stable.
