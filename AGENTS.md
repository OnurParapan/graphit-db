<!-- openwolf:begin -->
# OpenWolf

@.wolf/OPENWOLF.md

This project uses OpenWolf for context management. Read and follow .wolf/OPENWOLF.md every session. Check .wolf/cerebrum.md before generating code. Check .wolf/anatomy.md before reading files.
<!-- openwolf:end -->

# AGENTS.md — Graphit Coding Instructions

Graphit is a local-first database knowledge layer for AI coding agents. It scans
database metadata with read-only credentials, builds a durable local graph, and
serves compact context through CLI and MCP.

## Read before architectural changes

1. `README.md`
2. `docs/PRODUCT.md`
3. `docs/ARCHITECTURE.md`
4. `docs/DATA_MODEL.md`
5. `docs/RELATIONSHIP_ENGINE.md`
6. `docs/CLI.md`
7. `docs/MCP.md`
8. `docs/OPERATING_MODEL.md`
9. `docs/VISUALIZATION.md`
10. `docs/SECURITY.md`
11. `docs/TESTING.md`
12. `docs/RELEASING.md`
13. `docs/ROADMAP.md`
14. `docs/DECISIONS.md`

## Product invariant

Graphit understands databases; other tools act on that knowledge.

Graphit does not generate, optimize, or execute application SQL. It does not
modify target databases. It exists to reduce schema rediscovery, context size,
and hallucinated table/column/relationship usage by Codex, Claude, and SQL MCPs.

## Product priority

Optimize for adoption and usefulness:

- one-command installation,
- project-local initialization,
- no required daemon, Docker, web server, or external metadata database,
- useful CLI output before any optional visualization,
- compact progressive MCP responses,
- deterministic behavior without an LLM dependency.

## Required architecture

- Python `>=3.11` unless a measured dependency constraint requires otherwise.
- Typer CLI as the primary human interface.
- Project-local SQLite as the default knowledge store.
- Database-specific scanners behind protocols; PostgreSQL first.
- Stdio MCP as the primary agent integration.
- Optional self-contained HTML/DOT/JSON graph exports.
- pytest, Ruff, and mypy for quality gates.

Do not introduce FastAPI, Next.js, PostgreSQL as Graphit's required metadata
store, Neo4j, Redis, Kafka, Kubernetes, or an LLM dependency unless a later
milestone demonstrates a concrete need.

## Domain boundaries

- `domain`: objects, edges, evidence, snapshots, and context budgets.
- `application`: scan, search, traversal, relevant-context, and export use cases.
- `store`: SQLite persistence and migrations.
- `scanners`: read-only source database adapters.
- `mcp`: thin tools calling application services.
- `cli`: thin commands calling application services.
- `visualization`: graph projections and exports; no domain decisions.

Do not duplicate business logic between CLI, MCP, and visualization.

## Target database safety

- Assume credentials are read-only.
- Issue only metadata reads and explicitly bounded SELECTs.
- Apply statement timeouts.
- Avoid full-table scans.
- Never issue DDL or DML.
- Never create constraints or indexes.
- Never persist raw sampled business values by default.
- Never log credentials or connection strings containing secrets.

## Relationship rules

- Declared foreign keys are `EXPLICIT`, `CONFIRMED`, confidence `1.0`.
- Inferred relationships are `INFERRED`, start `PENDING`, and include evidence.
- Never present an inferred edge as a database fact.
- Precision is more important than candidate count.
- Expensive sampling occurs only after cheap candidate filters.

## Context rules

- Never return a full schema by default.
- Progress from overview → relevant objects → columns/relationships → paths.
- Every list and traversal must be bounded.
- MCP responses must distinguish confirmed and inferred facts.
- Context selection must remain deterministic in the core product.

## Development method

Work in small vertical slices. For each slice:

1. inspect current state,
2. identify one coherent behavior,
3. implement domain/application behavior,
4. add focused tests,
5. expose it through CLI or MCP only when required,
6. run relevant quality checks,
7. update contracts and decisions.

Do not build empty framework layers far ahead of behavior.

## Implementation sequence

1. package and CLI foundation
2. `graphit init` and project configuration
3. SQLite store and internal migrations
4. data source configuration and PostgreSQL connection test
5. PostgreSQL metadata scanner
6. immutable snapshots and repeatable scans
7. explicit table/column/key graph
8. search, neighborhood, and path queries
9. stdio MCP with progressive responses
10. inferred relationships and evidence
11. review decisions
12. impact analysis
13. local ERD-style graph export
14. lineage and additional database adapters

## Definition of done

A slice is complete only when behavior works, tests cover success and failure
paths, output is bounded, secrets remain protected, documentation is current,
and Ruff, mypy, and relevant pytest suites pass.
