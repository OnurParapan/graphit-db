# Graphit — Development Guide

## Prerequisites

```text
Python 3.11+
Git
Docker only for opt-in PostgreSQL integration tests
```

Node.js is not required for the core product.

## Setup

PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

POSIX shells:

```bash
python -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
```

## Commands

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m mypy
.\.venv\Scripts\graphit.exe version
```

On Windows systems where PowerShell script shims are blocked, invoke `.exe` or
`.cmd` launchers rather than changing machine execution policy.

## Layout

```text
src/graphit/
├── cli.py
├── domain/
├── application/
├── store/
├── scanners/
├── mcp/
└── visualization/
```

Create subpackages only when the corresponding behavior is implemented. Avoid
empty architecture scaffolding.

## Dependency policy

- Prefer the standard library for small, stable capabilities.
- Add runtime dependencies only for clear product value.
- Keep optional visualization and database adapters separable where practical.
- Never require Docker or Node.js for a normal installed-user workflow.

## Vertical slices

Each change should connect tested behavior to its required interface without
implementing future phases. Update `docs/DECISIONS.md` for significant choices.

## Release direction

Initial releases target PyPI and isolated installation through pipx/uv tool.
For a local, unpublished packaging check:

```powershell
.\.venv\Scripts\python.exe -m pip install build
.\.venv\Scripts\python.exe -m build
```

This creates a wheel and source archive in `dist/`. The source archive has an
explicit allowlist (`src`, `tests`, `docs`, README, LICENSE, and `pyproject.toml`)
so local agent configuration and OpenWolf state are not distributed. Check both
archives with `python scripts/check_dist.py` and install the wheel in a clean
environment before publishing. CI performs those archive and isolated-install
checks in a separate package job. That job also runs
`python scripts/smoke_installed.py` from the installed wheel environment to
check disposable-project initialization, preserved Codex/Claude project
settings, the documented MCP tool list, and saved-FK column and transitive
table-impact responses
through both generated stdio launch commands. The fixture is local-only; no
source database is contacted. These paths are installation-specific; the smoke does not run the
actual Codex or Claude application. It does not upload or publish artifacts.

Before publishing:

- verify and secure the `graphit-db` distribution name at publication time;
  availability and ownership are not established by this local checkout,
- test Python 3.11 through the latest supported version,
- publish checksums/provenance,
- document upgrade and local-store backup behavior.
