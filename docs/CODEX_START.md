# Graphit — Implementation Start Prompt

You are implementing Graphit, a local-first database knowledge and compact
context layer for AI coding agents.

Read `AGENTS.md`, `.wolf/STATUS.md`, and the directly relevant documents under
`docs/` before changing code.

Non-negotiable constraints:

- Python 3.11+ installable package.
- Typer CLI and project-local SQLite knowledge store.
- PostgreSQL source adapter first.
- Target database access is read-only.
- Stdio MCP is the primary agent integration.
- Responses are progressive and bounded; never dump the whole schema by default.
- Explicit and inferred relationships remain visibly distinct.
- No SQL generation, optimization, or arbitrary execution.
- No required FastAPI, Next.js, Docker, daemon, external metadata database, or
  LLM dependency.
- Work in tested vertical slices following `docs/ROADMAP.md`.

Current implementation state and exact next slice live in `.wolf/STATUS.md`.
