# Graphit — Product Specification

## Vision

Graphit makes a database understandable to AI coding agents without repeatedly
placing the entire schema in their context windows.

It scans once, refreshes when needed, and preserves a durable project-local
knowledge graph. Humans and agents query only the slice relevant to the current
task.

## Product statement

> Graphit is a local-first database context engine for AI coding agents.

It is not primarily a schema browser, hosted catalog, SQL author, or database
runtime. Its differentiated path is:

```text
Database → Knowledge graph → MCP → Coding agent
```

## Target users

- Developers using Codex or Claude in database-backed repositories.
- Teams with legacy schemas and incomplete constraints.
- Data engineers investigating dependencies and likely joins.
- SQL MCP authors that need a reliable schema/relationship context source.

## Jobs to be done

- Initialize Graphit in an existing project in minutes.
- Discover the tables and columns relevant to a task.
- Find a join path without loading the complete schema.
- Distinguish database facts from inferred relationships.
- Explain why an inferred relationship was suggested.
- Understand direct and transitive impact before changing a database-dependent
  module.
- Open an ERD-like graph when a human needs visual exploration.

## Product principles

### Zero-friction local use

The default installation requires no Docker, daemon, hosted account, external
metadata database, or frontend build.

### Context on demand

Responses expand progressively:

```text
L0 overview
L1 matching objects
L2 selected columns and relationships
L3 paths, dependencies, and evidence
L4 bounded statistics only when explicitly needed
```

### Evidence over guesses

Inferred edges always carry confidence and inspectable evidence. They are never
silently promoted to database facts.

### AI-ready, not AI-dependent

Scanning, graph construction, search, traversal, and baseline inference are
deterministic. Optional semantic ranking may be added later.

### Read-only source access

Graphit observes target databases. It never alters them.

### Useful before beautiful

CLI and MCP correctness precede optional visualization polish.

## MVP capabilities

These are target capabilities, not a claim that every item is complete. The
current evidence and outstanding work are tracked in
[MVP and release readiness](RELEASE_READINESS.md). Agent wiring is currently
opt-in after `graphit init`; index lookup and structural transitive FK impact
exist. A controlled actual-Codex task now has separate three-pair 14-table and
114-table measurements; broader workflows and real-user usefulness remain to
be measured.

1. Installable Python package and Typer CLI.
2. `graphit init` with project-local state and agent wiring.
3. PostgreSQL, Microsoft SQL Server, and Oracle source configuration and
   connection testing.
4. Metadata scan for schemas, tables, columns, keys, constraints, indexes, and
   views.
5. SQLite-backed immutable snapshots.
6. Explicit relationship graph at table and column level.
7. Object search, neighborhood, and bounded path queries.
8. Stdio MCP with compact, progressive responses.
9. Deterministic inferred relationships with evidence and confidence.
10. Review decisions and impact analysis.
11. Local ERD-style graph export.

## Non-goals for MVP

- Hosted multi-tenant platform.
- Required web UI.
- SQL generation, optimization, or arbitrary execution.
- Schema migration or automatic FK creation.
- Full enterprise data governance.
- Cross-database lineage.
- LLM-required inference.
- Broad engine-specific features that do not map to Graphit's shared structural
  graph.

## Adoption strategy

- Support widely deployed Python versions, initially Python 3.11+.
- Install through `pipx` and `uv tool`.
- Keep default state inside the user's project.
- Detect and assist Codex/Claude configuration during initialization.
- Produce actionable errors and copy-pasteable next commands.
- Keep MCP tool names small, predictable, and composable.
- Later provide standalone binaries if packaging measurements justify them.

## Success metrics

- Time from install to first successful scan.
- Percentage of users who complete `init → scan → first MCP call`.
- Scan correctness and success rate.
- High-confidence inferred relationship precision.
- Median MCP response size and estimated tokens.
- Percentage of agent tasks completed without a full schema dump.
- Repeat scans and weekly active projects.
- Package installs, successful upgrades, and agent integrations configured.

Graphit should claim measured token reductions, never a universal fixed
percentage.
