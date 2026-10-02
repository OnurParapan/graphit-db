# Graphit — Local Knowledge Model

## Goals

The SQLite model supports immutable scans, table/column/key relationships,
inference evidence, review decisions, graph traversal, and progressive context
without copying source business data.

## Stable logical identity

Snapshot rows receive local integer IDs for efficient joins. Cross-snapshot
matching uses a logical key derived from source, object type, and normalized
qualified name.

Examples:

```text
postgres:public.claim
postgres:public.claim.customer_id
postgres:public.claim:constraint:claim_pkey
```

Quoted identifiers preserve exact source spelling alongside normalized search
forms.

## PostgreSQL key and foreign-key extraction

The scanner reads declared `p` (primary key), `u` (unique), and `f` (foreign
key) constraints from `pg_catalog.pg_constraint`. It uses `conkey` for source
column order and `confkey` for the matching referenced-column order. The
columns are resolved through `pg_attribute`; missing entries or unequal pair
counts fail the scan. Constraint rows are bounded; successful scans persist
them as `CONSTRAINT` objects and `PRIMARY_KEY` / `UNIQUE_KEY` / `REFERENCES`
edges with ordered column-pair metadata.

Primary and unique constraints keep their exact source table, name, ordered
columns, and partition-inheritance flag. Foreign keys also keep the exact
target table, ordered source/target column pairs, validation flag, and whether
the target table was inside the selected scan scope. An out-of-scope target is
recorded as such; Graphit must not invent a complete local target node. An
unvalidated FK remains a declared database constraint, but its validation flag
must be surfaced in later graph/context views.

The first persistence path creates a new `snapshots.version` per source inside
one SQLite transaction. It inserts schema/table/column/constraint objects and
confirmed database edges. An out-of-scope FK target becomes a clearly labeled
external schema/table stub with no invented columns. A failed local write rolls
back the scan run, snapshot, objects, columns, and edges together. A source-scan
error may be recorded as `scan_runs.status = FAILED`, but never creates a
successful snapshot. Column `type_family` remains `UNKNOWN` until the later
type-normalization slice; this avoids inventing inference evidence now.
The first relationship candidate preview derives only a narrow set of known
PostgreSQL key-type aliases from the saved `data_type` string. It is read-only
and does not persist inferred edges or sampled values; the broader
`type_family` normalization and inferred-edge storage remain later work.
New snapshots represent declared unique-key membership with `UNIQUE_KEY`
edges. The first snapshot writer used `UNIQUE` for those edges; local table
queries still recognize both forms so existing snapshots remain readable.
The scanner also distinguishes PostgreSQL `pg_class.relkind` `r`/`p` tables,
`v` views, and `m` materialized views. Existing `objects.object_type` values
`VIEW` and `MATERIALIZED_VIEW` hold those objects without a store migration;
their live columns retain the same `COLUMN` records and parent edges. View
columns are catalog metadata, not evidence of source-table lineage. Declared
PK/unique/FK extraction remains restricted to ordinary/partitioned tables.
Search and task-context results label view kinds and can suggest a bounded
`get_view` detail follow-up. That detail returns saved columns and whether a
catalog `NOT NULL` flag is declared; absent declaration is not proof of actual
NULLs. Table-specific `show`/`get_table`, FK graph, and path queries remain
table-only.

## PostgreSQL index facts

The read-only scanner now returns an `IndexMetadata` tuple for each live index
on a selected ordinary/partitioned table or materialized view. A separate
100,000-index hard cap fails the scan rather than presenting a partial result.
It preserves ordered key positions separately from non-key `INCLUDE` columns,
the access method, and unique/primary/valid/ready/partial flags. An expression
key is represented by `None` at its position, not by a fabricated column.
The scanner checks attribute numbers against resolved names and rejects
inconsistent rows. It does **not** read, store, or display expression or
predicate SQL. Each complete scan now saves an `INDEX` object under its
in-scope table or materialized view, with safe flags and ordered key/include
names in `metadata_json`. A parent `CONTAINS` edge and positioned `INDEX_KEY`
and `INDEX_INCLUDE` edges link real columns; expression positions remain
`null` in the index object and create no invented column edge. The existing
generic `objects`/`edges` tables admit these kinds without a SQLite schema
migration. Older snapshots retain their original content. The explicit
`graphit indexes` CLI query validates saved object/edge agreement and pages
one exact table or materialized view; default search, table context, MCP,
diff, and exports still omit index facts.

## Core tables

### `store_migrations`

```text
version integer primary key
applied_at text
checksum text
```

### `sources`

```text
id integer primary key
name text unique
engine text                  # postgresql initially
database_name text
host text
port integer
username text
credential_env text          # name of environment variable, never its value
credential_kind text         # password_env, url_env, or url_dotenv
credential_file text null    # allowlisted root dotenv name for url_dotenv
ssl_mode text
selected_schemas_json text
created_at text
updated_at text
```

Store migration v2 adds the two credential-reference columns. Existing v1
sources migrate to `password_env` with no credential file, preserving their
previous runtime behavior. URL and password values are never persisted.

### `scan_runs`

```text
id integer primary key
source_id integer
status text
started_at text
completed_at text null
error_code text null
error_message text null
object_count integer
edge_count integer
```

### `snapshots`

```text
id integer primary key
source_id integer
scan_run_id integer unique
version integer
schema_fingerprint text
created_at text
unique(source_id, version)
```

### `objects`

```text
id integer primary key
snapshot_id integer
parent_id integer null
logical_key text
object_type text
schema_name text null
object_name text
qualified_name text
normalized_name text
source_identifier text null
metadata_json text
unique(snapshot_id, logical_key)
```

Object types initially include:

```text
DATABASE
SCHEMA
TABLE
VIEW
MATERIALIZED_VIEW
COLUMN
INDEX
CONSTRAINT
```

### `columns`

```text
object_id integer primary key
table_object_id integer
ordinal_position integer
data_type text
type_family text
nullable integer
default_expression text null
primary_key integer
unique_value integer
statistics_json text null
```

Statistics contain only bounded aggregate evidence such as null fraction,
distinct estimate, or row-count estimate. Raw sampled values are not stored by
default.

### `edges`

```text
id integer primary key
snapshot_id integer
source_object_id integer
target_object_id integer
edge_type text
origin text
status text
confidence real
metadata_json text
created_at text
```

Initial edge types:

```text
CONTAINS
HAS_COLUMN
PRIMARY_KEY
UNIQUE_KEY
REFERENCES
LIKELY_REFERENCES
DEPENDS_ON
INDEX_KEY
INDEX_INCLUDE
```

Origins:

```text
DATABASE
INFERRED
MANUAL
LINEAGE
```

Statuses:

```text
CONFIRMED
PENDING
APPROVED
REJECTED
SUPPRESSED
```

### `relationship_evidence`

```text
id integer primary key
edge_id integer
evidence_type text
score real
details_json text
```

Evidence details may include counts and ratios, but not raw source values.

### `review_decisions`

```text
id integer primary key
source_logical_key text
target_logical_key text
relationship_kind text
decision text
comment text null
created_at text
```

Reviews use logical keys so they can be reconciled across immutable snapshots.
The implemented rejection key prefixes each exact quoted column identity with
the configured source alias. This is necessary because snapshot object logical
keys alone do not include the source and may collide across source aliases.
The latest decision for a pair wins. `REJECTED` hides a candidate;
`RESTORED` is an append-only review event that removes this suppression and
allows the candidate to return as `PENDING` if still eligible. `APPROVED`
marks an exact current candidate as a human-reviewed logical relationship;
its preview origin stays `INFERRED` and its metadata score does not change.
Repeating a decision is idempotent; a rejected pair must be restored before
approval. `REVOKED` is an append-only event that reverses approval without
erasing it; an eligible pair returns as `PENDING` and may be approved or
rejected again. An approved pair must be revoked before rejection. No inferred
edge is created by these decisions.

`MANUAL_PROPOSAL` uses the same source-scoped exact column keys but a separate
`relationship_kind` and a `PROPOSED` review event. Its required single-line
business reason is stored in `comment`. Identical repeats add no row; changing
the reason appends a new event. This is not an inferred candidate, approved
logical edge, or declared FK. Candidate and confirmed-FK queries ignore
`MANUAL_PROPOSAL`; no schema migration is needed for these local review events.
An explicit approval appends `APPROVED` under the same relationship kind,
carrying the latest human reason; a repeated approval appends nothing. The
latest event is the manual status shown by `review proposals`. Approved manual
events remain outside inferred review decisions. Current manual approvals
can be projected into the local graph and MCP graph context with
`origin: MANUAL`. `review propose` cannot silently turn an approved event back into
`PROPOSED`; reversal needs a separate explicit action.
That reversal appends `REVOKED` with the same reason and keeps all earlier
events. The latest `REVOKED` status is not an active logical approval. An
identical repeat adds no row. Re-approval requires a fresh `PROPOSED` event,
even if the reason text is unchanged, followed by explicit approval. Revocation
can clear a now-stale approval without deleting its audit trail.
Manual graph/MCP projection validates the latest event and current snapshot
structure, projects only eligible `APPROVED` pairs adjacent to the requested
table, and retains the reason. It does not write an `edges` row. `PROPOSED`,
`REVOKED`, and stale approvals remain absent. A duplicate exact inferred
approval is hidden in favor of the manual reason; declared FK facts remain
distinct. Source review-event and adjacent-pair work limits are explicit.
The local proposal-list query selects the latest event by ID for each exact
source-scoped pair, then pages newest updated pairs. It rechecks the latest
snapshot's saved columns, single-column target key, data types, and declared
source FKs to label structural eligibility; it never changes the stored
event. A hard 5,000-event read budget and 50-item page cap prevent silently
partial review history.

## Search

The compact database overview reads only the latest completed snapshot. Its
counts distinguish scanned schemas/tables from external FK target stubs, and
count confirmed database-declared table FKs rather than inferred or column-level
edges. Bounded schema names are sorted by qualified name. Bounded entry tables
are ordered by participating declared FK count, then qualified name; this is a
navigation heuristic, not a semantic importance claim.

The first task-focused context selector reuses bounded local name searches,
combines matches across up to eight distinct task terms, and ranks by matched
term count, scope, object kind, and qualified name. It returns safe object
facts and reasons, not SQL, inferred relationships, or raw business values.
It checks snapshot versions across its local searches and fails on a concurrent
rescan rather than mixing graph versions. Object count and serialized JSON
size are both bounded; graph expansion is deferred.

An FTS table may index qualified name, object name, normalized name, and safe
database comments. FTS is an optimization; exact lookup must work without it.
The first local search reads only the latest `COMPLETED` snapshot for a named
source. It searches schema, table, view, materialized-view, and column
qualified names with all case-folded user terms required,
orders deterministic relevance tiers, and fetches at most `limit + 1` rows to
report truncation. It never reads the source database or returns source values.
Exact table context likewise reads that snapshot only. Columns, key objects,
and table-level FK edges are independently capped, with explicit truncation
flags. A scope-external FK target remains a partial table stub, not a complete
table description.
Direct relationship queries traverse only table-to-table `REFERENCES` edges
with `origin = DATABASE` and `status = CONFIRMED`. Incoming and outgoing lists
are independently bounded; a self-FK appears in both. Inferred or pending
relationships cannot be accidentally presented as declared database facts.
Shortest-path queries apply bounded breadth-first search to those same edges,
allowing forward or reverse traversal but preserving the FK's true source and
target in each result step. Search budgets cap visited tables and examined FK
adjacencies; budget exhaustion is not reported as a proven missing path.

## Indexes

At minimum:

```text
objects(snapshot_id, object_type)
objects(snapshot_id, qualified_name)
objects(snapshot_id, normalized_name)
edges(snapshot_id, source_object_id)
edges(snapshot_id, target_object_id)
edges(snapshot_id, edge_type, status)
```

## Retention

Snapshots are immutable. A later configurable retention command may prune old
snapshots, but it must preserve review decisions and never run silently.

The initial snapshot-diff reader compares two explicitly selected completed
versions under one SQLite read transaction. It uses exact saved logical keys
for in-scope schemas, tables, views, materialized views, and columns; external
FK target stubs are not treated as scanned objects. It compares only stored
column structural flags, table partition status, declared primary/unique key
definitions, and confirmed database-declared table FK definitions. Keys use
their saved constraint metadata, not the legacy `UNIQUE`/`UNIQUE_KEY` edge
spelling. FKs pair one saved constraint with exactly one matching table-level
`REFERENCES` edge; missing, duplicate, or conflicting metadata fails closed.
Lineage, indexes, and review decisions remain outside its stated scope.
At most 100,000 stored objects per snapshot are inspected; a larger snapshot
or more than 100,000 table FK edges fails explicitly. Up to 500 changes are returned with a total count and
truncation flag. An exact deletion and addition does not imply a rename.
