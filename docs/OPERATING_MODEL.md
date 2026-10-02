# Graphit — Operating Model

## Installation

Primary target:

```bash
pipx install graphit-db
```

Alternative:

```bash
uv tool install graphit-db
```

Graphit should not require Node.js, Docker, PostgreSQL metadata infrastructure,
or a background service.

## Project initialization

```bash
cd my-project
graphit init
```

Initialization creates safe configuration and a versioned local SQLite store.
It discovers conventional PostgreSQL URLs in the process environment and a
bounded project-root dotenv allowlist. After sanitized confirmation—or explicit
`--yes`—it verifies each password-bearing candidate through a forced read-only
session and saves only a credential reference. `--no-connect` performs
discovery without contact. Metadata scanning, whole-database ERD generation,
and agent wiring follow in later init slices; initialization never copies source
business data.

```text
my-project/
├── graphit.toml
└── .graphit/
    ├── graphit.db
    ├── snapshots/
    ├── exports/
    └── state.json
```

## Configuration

Current `graphit.toml` stores scan, sampling, and context defaults. PostgreSQL
source definitions are stored in the project-local SQLite database by
`graphit source add`; they are not currently represented in `graphit.toml`.

Example source registration:

```bash
graphit source add claims --host localhost --database claims \
  --username graphit_reader --credential-env CLAIMS_DATABASE_PASSWORD \
  --schema public
```

Future shareable configuration might include a source entry like:

```toml
[project]
name = "claims-service"

[[sources]]
name = "claims"
engine = "postgresql"
host = "localhost"
port = 5432
database = "claims"
username = "graphit_reader"
credential_env = "CLAIMS_DATABASE_PASSWORD"
schemas = ["public"]

[scan]
include_views = true
include_indexes = true
infer_relationships = true

[sampling]
enabled = false
max_rows = 500
statement_timeout_ms = 5000

[context]
default_object_limit = 20
default_edge_limit = 50
```

Only environment variable names are persisted. Password values stay in the
environment and are not read until a future connection-test or scan command.

## First scan

```bash
graphit source test claims
graphit scan --source claims
```

The scan reads catalogs and supported definitions. Enhanced inference may issue
bounded read-only sampling queries when explicitly enabled. It never downloads
all source rows.

## Rescan

After a schema change:

```bash
graphit scan --source claims
```

A new immutable snapshot becomes latest only after a successful transaction.
MVP can rescan all selected metadata; incremental refresh is a later performance
optimization.

## Daily agent use

Once configured, Codex or Claude invokes the stdio MCP automatically for
database-dependent tasks. A typical sequence is:

```text
search_objects("claim customer")
get_table("public.claim")
find_path("public.customer", "public.claim")
```

The user should not need to repeatedly explain database naming conventions.

## Offline behavior

After a successful scan, graph queries, MCP context, and visualization exports
work without source database connectivity. Only connection tests and new scans
need the target database.

## Updates

Package upgrades must preserve project data. Internal SQLite migrations run
transactionally and record their version/checksum. Destructive store migrations
require an explicit backup and user confirmation.

### Back up and restore the local knowledge store

The project-local `.graphit/graphit.db` contains saved schema snapshots, source
definitions, and review decisions. It does **not** contain source database
passwords, but it may reveal sensitive names and business structure. Keep a
backup outside the repository and protect it with the same filesystem access
as the original store. `graphit.toml` is separate and should be backed up with
the project configuration.

Before upgrading Graphit, stop scans, MCP clients, and other processes using
the project, then copy `.graphit/graphit.db` and `graphit.toml` to a new backup
location. Do not overwrite an older backup. Do not copy a live SQLite file as
though it were a consistent snapshot; when stopping all processes is not
possible, use SQLite's online backup API with an independent operator tool.
Graphit does not yet provide a live-backup CLI command. Take this backup
**before the first Graphit command with the upgraded package**, because that
command may apply compatible migrations.

To restore, stop Graphit processes again, preserve the current database under
a separate name, put the chosen backup at `.graphit/graphit.db`, and restore
the matching `graphit.toml` if needed. Use the Graphit version that created
the backup or a newer compatible version; an older Graphit refuses a newer
store schema. Reopen the project and verify sources, snapshots, and reviewed
relationships before removing the preserved current database. A rescan can
rebuild structural facts, but cannot recreate lost human review history.
There is no previous public Graphit release yet, so a released-version upgrade
path has not been exercised.
