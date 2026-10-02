# Graphit — CLI Contract

## Purpose

The `graphit` CLI is the primary installation, configuration, scanning, query,
and integration interface. Commands must be scriptable, safe, and useful before
any optional visualization exists.

## Global behavior

```text
graphit --help
graphit version
graphit <command> --json
```

- Human-readable output is the default.
- `--json` produces stable machine-readable output where supported.
- Errors go to stderr and include a stable code.
- Secrets are redacted.
- Commands discover the project by walking upward for `graphit.toml`.
- `--project PATH` overrides discovery.

## Initialization

```bash
graphit init
graphit init --project path/to/repository
graphit init --force
```

Behavior:

1. Refuse to overwrite existing configuration without `--force`.
2. Create `graphit.toml` with safe defaults.
3. Create `.graphit/graphit.db` with a versioned local SQLite schema. Refuse
   unknown or tampered store versions and preserve existing store rows.
4. Ensure `.graphit/` is ignored by Git when a Git repository exists.
5. Show exactly which files were created or updated.
6. Discover PostgreSQL URL candidates from the process environment and the
   bounded project files `.env`, `.env.local`, `.env.development*`, `.env.test*`,
   and `.env.production*`. Show variable origin, host, port, database, user,
   password presence, and SSL mode without showing the URL or password.

`--force` replaces only `graphit.toml`; it preserves existing `.graphit/`
contents while applying compatible store migrations. Symlinked
configuration/state/store paths are rejected. By default, project
discovery stops at the nearest existing `graphit.toml` or Git root. `init`
does not modify agent settings or connect to a discovered database yet. Explicit
project-local Codex setup is available
with `graphit mcp setup-codex`; `graphit mcp setup-claude` similarly edits only
the project's `.mcp.json`. Connection, scan, whole-database ERD generation, and
agent setup remain subsequent init-orchestration slices. Global agent
configuration is never changed here.

Discovery recognizes `postgres://` and `postgresql://` values in conventional
`DATABASE_URL`, `POSTGRES_URL`, `POSTGRESQL_URL`, `PGURL`, and prefixed variants.
Process-environment values take precedence over matching dotenv variables;
duplicate connection identities collapse deterministically. Example/template,
symlinked, invalid UTF-8, and files larger than 1 MiB are not read. A discovered
password exists only in the transient candidate object and is never written by
`init`.

## Source management

```bash
graphit source add NAME --host HOST --database DB --username USER --credential-env ENV
graphit source list
graphit source show NAME
graphit source test NAME
graphit source remove NAME
```

`source add`, `source list`, `source show`, and `source test` are implemented.
`source remove` is planned. `source add` stores connection metadata in
`.graphit/graphit.db` and only the name of a credential environment variable;
it does not read or write the password value. Sources are not currently written
to `graphit.toml`. Source names must be unique. `--schema` is repeatable and
defaults to `public`; `--ssl-mode` defaults to `prefer`. `--project` can select
an initialized Graphit project. Only `source test` connects to PostgreSQL.
It resolves the named environment variable at call time and runs one bounded
read-only verification query. Authentication, TLS, connection timeout, and
query timeout failures have sanitized error codes and exit code 4. The default
SSL mode `prefer` can fall back to an unencrypted connection; use `require` or
`verify-full` when transport encryption or identity verification is required.

Non-interactive form:

```bash
graphit source add claims \
  --engine postgresql \
  --host localhost \
  --port 5432 \
  --database claims \
  --username graphit_reader \
  --credential-env CLAIMS_DATABASE_PASSWORD \
  --schema public
```

## Scanning

```bash
graphit scan --source claims
```

`scan --source NAME` is implemented. It reads the selected source schemas and
atomically saves a new immutable snapshot in `.graphit/graphit.db`; an identical
rescan still creates a new version. Current coverage is schemas, ordinary and
partitioned tables, views, materialized views, their columns, declared
table primary/unique constraints, and declared table foreign keys. View
definitions and lineage are not yet scanned. The shared table/view scan limit
fails closed instead of saving a partial snapshot. Failed source scans are
recorded without changing the latest
successful snapshot. A failed local write rolls back all objects and edges.
Output reports only source alias, version, and object/edge counts; it never
prints credentials. `Ctrl+C` cannot expose a partial successful snapshot.
`graphit scan` without `--source`, `--metadata-only`, and `--no-inference` are
planned for later slices.

## Inspection

```bash
graphit status
graphit candidates --source claims
graphit candidates --source claims --limit 10 --json
graphit candidates --source claims --include-ambiguous --json
graphit review propose '"sales"."support_ticket"."client_id"' '"crm"."customer"."id"' \
  --source claims --snapshot-version 1 --reason "ERP owner confirmed this identity" --json
graphit review proposals --source claims --limit 20 --offset 0 --json
graphit review approve-proposal '"sales"."support_ticket"."client_id"' \
  '"crm"."customer"."id"' --source claims --snapshot-version 1 --json
graphit review revoke-proposal '"sales"."support_ticket"."client_id"' \
  '"crm"."customer"."id"' --source claims --snapshot-version 1 --json
graphit review approve '"sales"."invoice"."customer_id"' '"crm"."customer"."id"' \
  --source claims --snapshot-version 1
graphit review revoke '"sales"."invoice"."customer_id"' '"crm"."customer"."id"' \
  --source claims --snapshot-version 1
graphit review reject '"billing"."invoice"."account_id"' '"finance"."account"."id"' \
  --source claims --snapshot-version 1
graphit review restore '"billing"."invoice"."account_id"' '"finance"."account"."id"' \
  --source claims --snapshot-version 1
graphit search "customer claim" --source claims
graphit snapshots --source claims --limit 20 --json
graphit diff --source claims --from-version 1 --to-version 2 --json
graphit search "customer" --source claims --limit 10 --json
graphit show public.claim --source claims
graphit show '"public"."Claim.Detail"' --source claims --limit 20 --json
graphit relationships public.claim --source claims
graphit relationships public.claim --source claims --limit 10 --json
graphit impact public.customer --source claims --limit 20 --json
graphit impact-tree public.customer --source claims --max-hops 4 --limit 20 --json
graphit impact-column public.customer.id --source claims --limit 20 --json
graphit path public.customer public.invoice --source claims
graphit path public.customer public.invoice --source claims --max-hops 3 --json
graphit context "change claim customer identifier"
```

`candidates` is a read-only preview of narrow metadata-only relationship
hypotheses from the latest successful snapshot. Results always have
`origin: INFERRED`; unreviewed pairs have `status: PENDING` and human-approved
logical pairs have `status: APPROVED`. Both retain weighted metadata evidence
and a provisional, unchanged confidence score. Neither is inserted into the
confirmed FK graph. It
requires exact `<target_table>_id` to `target_table.id` naming, an exact known
key type match, and a declared single-column target key; any source column
already covered by a confirmed declared FK is suppressed, including composite
FKs and out-of-scope targets. Default limit is 20, maximum 50. Work exceeding
2,000 tables, 20,000 columns, 20,000 declared FKs, or 2,000 candidate pairs fails with
`INFERENCE_BUDGET_EXCEEDED` (exit 7). By default, two or three matching target
tables cause Graphit to abstain for that source column; it reports the skipped
count. `--include-ambiguous` explicitly returns all such alternatives for
inspection, at most three; more than three are skipped even then. The JSON
response includes `include_ambiguous`, `skipped_ambiguous_columns`, and the
evidence components.
No source database connection, sampling, or local graph write occurs.

`review propose SOURCE_COLUMN TARGET_COLUMN --source NAME --snapshot-version N
--reason TEXT` records a *human hypothesis* when the narrow `candidates` rule
does not recognize different names such as `client_id` and `customer.id`. Use
the exact quoted column names from the latest snapshot and its current version.
Both columns must exist in that source snapshot, the target must have a
single-column key, their stored data-type names must match exactly, and the
source column must not already have a declared FK. A short single-line reason
(at most 300 characters) is required. The result is `origin: MANUAL`,
`status: PROPOSED`, and `changed`; repeating an identical reason is a no-op,
while changing the reason appends local review history. `PROPOSED` is a review
event, not an edge status or approval. This command neither connects to the
source database nor adds the unapproved pair to candidates, graph exports, or MCP context.
`review approve` still accepts only *inferred* candidates.

`review proposals --source NAME` lists only that source's latest manual
proposal event per exact column pair, newest updated pair first. Output
includes the current successful `snapshot_version`, each pair's latest
reason, `origin: MANUAL`, `status: PROPOSED`, `APPROVED`, or `REVOKED`, and
`eligibility`. `ELIGIBLE`
means only that the pair still passes the structural checks above; it is
**not** approval or proof of a business relationship. A rescan can instead
show `SOURCE_COLUMN_MISSING`, `TARGET_COLUMN_MISSING`,
`TARGET_KEY_REMOVED`, `TYPE_CHANGED`, or `DECLARED_FK_ADDED`. Stale proposals
remain visible and are never silently promoted. `--limit` defaults to 20
and permits 1-50; `--offset` permits 0-5000. JSON includes `proposals`,
`offset`, and `truncated` for paging. More than 5,000 source-scoped proposal
events fails explicitly instead of silently omitting history. Listing reads
only project-local SQLite; it does not connect to PostgreSQL. Only current
manual `APPROVED` pairs can appear in local graph and MCP graph context.

`review approve-proposal SOURCE_COLUMN TARGET_COLUMN --source NAME
--snapshot-version N` accepts an exact existing manual proposal from the
current snapshot. It rechecks structural eligibility under a local write
transaction and refuses stale pairs, missing proposals, and an active
conflicting inferred review. It appends `APPROVED` with the latest human
reason; repeating approval is idempotent. A stale snapshot fails instead of
attaching approval to changed schema. After approval, `review propose` refuses
to replace the approved state; use `review revoke-proposal` first.
JSON returns the exact pair, reason, `origin: MANUAL`, `status: APPROVED`, and
`changed`. This approval is a human-reviewed logical claim, never a declared
database FK. A still-eligible approval can appear in local `graph` output and
MCP `get_graph_context` with explicit manual provenance; inferred-candidate
preview and target PostgreSQL are unchanged.
`PROPOSAL_NOT_FOUND`, `PROPOSAL_STALE`, and `PROPOSAL_REVOKED` use exit code 6.

`review revoke-proposal SOURCE_COLUMN TARGET_COLUMN --source NAME
--snapshot-version N` withdraws an exact manual approval by appending a
`REVOKED` event with the same human reason. It requires the current snapshot
version, but does not require the pair to remain structurally eligible: a
stale approval can still be withdrawn. Repeating revocation is a no-op;
missing or merely `PROPOSED` pairs fail. The review history is retained and
the pair remains listed as `REVOKED`. To approve it again, explicitly run
`review propose` (with a reason) and then `review approve-proposal`; even an
unchanged reason creates a new `PROPOSED` event after revocation. This is a
local review action only; it does not touch PostgreSQL. It immediately
removes the manual link from local graph and MCP graph-context output.
`MANUAL_APPROVAL_NOT_FOUND` uses exit code 6.

`review reject SOURCE_COLUMN TARGET_COLUMN --source NAME --snapshot-version N`
records an explicit local rejection of one exact candidate from snapshot `N`.
Copy the quoted column identifiers and version from `graphit candidates
--source NAME --json`; use `--include-ambiguous` first when reviewing an
ambiguous pair. A stale version, non-candidate pair, or declared FK is refused.
Re-running the same rejection is idempotent. The decision is scoped to the
source and stable qualified column pair, so it survives rescans and cannot
silence a different source with the same table names. Rejected pairs disappear
from subsequent candidate previews; rejecting one ambiguous alternative may
allow the other to appear by default. `--json` returns the pair, version,
`decision: REJECTED`, and `changed`. No source connection, source write,
inferred edge insertion, or MCP change occurs.

`review restore` accepts the same exact pair with the **current** snapshot
version, but only when that source/pair has an earlier rejection. It appends a
`RESTORED` event instead of deleting history. Repeating it is a no-op. If the
pair still qualifies as a candidate, it reappears in preview; if it no longer
qualifies after a rescan, the rejection is still cleared for future scans.
Stale versions and pairs never rejected under that source fail explicitly.
`RESTORED` is a review-history event, not a claim that a relationship exists.

`review approve` requires one exact current inferred candidate pair and its
snapshot version; a declared FK, stale pair, or rejected pair is refused (use
`review restore` before approving a rejected pair). Approval is idempotent,
source-scoped, and survives rescans while the candidate remains eligible.
Default candidate preview shows an approved target even when another target
would otherwise make the source ambiguous. `--include-ambiguous` still shows
other unreviewed alternatives as `PENDING`. In JSON and human output, the
approved pair remains `origin: INFERRED`, `status: APPROVED`; its metadata
confidence is not inflated. This is a human-reviewed logical relationship,
not a database-declared FK. No source connection, source mutation, inferred
graph edge, or MCP exposure occurs in this slice.

`review revoke` accepts only a source-scoped pair whose latest decision is
`APPROVED` (or already `REVOKED` for idempotency) and the **current** snapshot
version. It appends a `REVOKED` event; approval history is never deleted. A
still-eligible pair returns to `PENDING`, including normal ambiguity rules.
Revocation is allowed after a rescan removed the candidate, preventing an old
approval from silently reappearing later. Missing approval and stale versions
fail explicitly. `REVOKED` is a review-history event, not a database status.
No source connection, graph-edge write, or MCP change occurs.

`search` is implemented for schema, table, view, materialized-view, and column names in the latest
successful snapshot of one required `--source`. It does not reconnect to
PostgreSQL. Search terms are case-folded (including Unicode), all terms must
occur somewhere in the qualified name, and punctuation such as `%` is literal.
Results prioritize exact/prefix object-name matches, then tables, views,
materialized views, columns, and schemas, then qualified name. Output includes quoted exact
identifiers and minimal column type/nullability/key flags; out-of-scope FK
target stubs are labeled. The default limit is 20, hard maximum 100; a
`truncated` flag indicates more matches. `--json` emits a stable object with
`source_name`, `snapshot_version`, `matches`, and `truncated`. Empty results
are successful; an unscanned source returns `NO_SNAPSHOT` (exit 6).

`snapshots --source NAME` lists completed local scan versions, newest first,
with UTC completion times and `COMPLETED` status. It does not contact the
source database. The default `--limit` is 20, maximum 100; `--json` returns
`source_name`, `snapshots`, total `snapshot_count`, and `truncated`. A known
source with no completed scan returns an empty list, while an unknown source
fails with `SOURCE_NOT_FOUND`. Use two listed versions for `diff`.

`diff --source NAME --from-version OLD --to-version NEW` compares two explicit
completed local snapshots of the same source, with `OLD < NEW`. It does not
connect to PostgreSQL or alter either snapshot. This first slice compares
exact in-scope schema, table, view, materialized-view, and column identities;
column changes include ordinal, type, nullability, primary-key membership,
and single-column uniqueness flags. Table partitioning is also compared.
Out-of-scope FK target stubs are excluded. A removal plus addition is **not**
claimed to be a rename. Declared primary/unique key definitions include kind,
ordered columns, and inheritance; table-level FK definitions include target
table/scope, ordered source-target column pairs, validation, and inheritance.
View definitions, lineage, indexes, and human review decisions are **not
compared**, so zero reported changes does not prove those facts stayed the same.
Default `--limit` is 100, maximum 500. `--offset` defaults to 0 and must be
between 0 and 200,000; advance by the number of returned changes to fetch
later pages. JSON reports total `change_count`, returned `changes`, requested
`offset`, and `truncated` (more changes after this page), plus
`scope: SCHEMA_RELATION_COLUMN_KEY_FK`. This scope value replaces the earlier
`SCHEMA_RELATION_COLUMN` value (a JSON contract change). An oversized source snapshot fails with
`DIFF_BUDGET_EXCEEDED` (exit 7) rather than returning a partial comparison.
Missing or non-completed versions return `SNAPSHOT_NOT_FOUND` (exit 6).
An empty page at or beyond `change_count` is successful; it does not erase
earlier changes. A single page is never a complete-drift claim unless its
`offset` is 0 and `truncated` is false.

`show TABLE --source NAME` is implemented. It resolves one exact table in the
latest completed snapshot. Unquoted identifiers use PostgreSQL lowercase
folding; quoted identifiers preserve case and can contain dots or escaped
double quotes. A bare table name matching multiple schemas returns
`AMBIGUOUS_TABLE`; a missing table returns `TABLE_NOT_FOUND` (both exit 6).
Views can be found by `search`, but `show` remains table-only.
It returns columns in ordinal order, declared primary/unique keys with ordered
columns, and outgoing declared FKs with ordered source/target column pairs.
Partitioned or out-of-scope tables, inherited keys/FKs, and unvalidated FKs are
marked explicitly. `--limit` applies separately to columns, keys, and FKs:
default 30, hard maximum 100, with a truncation flag for each section. `--json`
emits the same typed context for tools. Neither `search` nor `show` contacts
PostgreSQL.

`show-view VIEW --source NAME` reads one exact saved view or materialized
view. It returns explicit `VIEW`/`MATERIALIZED_VIEW` kind and columns in
ordinal order, with type and `not_null_declared`. A false flag means the
catalog does not declare `NOT NULL`, not that actual NULL values have been
observed. `--limit` defaults to 30, hard maximum 100; `columns_truncated`
signals additional columns. Bare names in multiple schemas fail as
`AMBIGUOUS_VIEW`; a table or missing name yields `VIEW_NOT_FOUND` (exit 6).
`--json` exposes the same bounded domain result. It does not reconnect to
PostgreSQL or claim view definitions, FKs, or lineage.

`indexes RELATION --source NAME` reads saved indexes for one exact table or
materialized view, without reconnecting to PostgreSQL. It accepts the same
quoted and schema-qualified identifier syntax as `show`; a bare name matching
multiple eligible relations yields `AMBIGUOUS_INDEX_TARGET` (exit 6), and a
missing relation or ordinary view yields `INDEX_TARGET_NOT_FOUND` (exit 6).
Older snapshots without saved index facts return an empty list. The command
shows the index name/access method, ordered key and `INCLUDE` columns with
one-based positions, `null` for an expression key, and unique/primary/valid/
ready/partial flags. It never shows expression or predicate SQL.

```bash
graphit indexes public.orders --source erp
graphit indexes public.orders --source erp --limit 20 --offset 20 --json
```

The default page is 20 indexes, hard maximum 100; `--offset` is bounded to
0–100,000. JSON returns `source_name`, `snapshot_version`, `relation_kind`,
`qualified_name`, `indexes`, `index_count`, `offset`, and `truncated`. A page
past the end is empty without losing the total count. Malformed saved index
metadata/edges fail closed as `STORE_READ_FAILED`; a count beyond the scan
budget fails as `INDEX_BUDGET_EXCEEDED` (exit 7). Search, `show`, overview,
and MCP context remain index-free by default.

`relationships TABLE --source NAME` is implemented for direct incoming and
outgoing declared FKs in the latest completed snapshot. It reuses `show`'s
exact identifier and ambiguity rules. Each result includes the FK name, exact
source/target tables, ordered column pairs, validation/inheritance flags, and
whether the target was inside scan scope. JSON explicitly marks every returned
edge `origin: DATABASE` and `status: CONFIRMED`; inferred/pending edges are
excluded. A self-reference appears in both directions. `--limit` applies per
direction (default 20, hard maximum 100), with independent truncation flags.
There is no target database connection.

`impact TABLE --source NAME` shows direct dependents of one exact table through
incoming confirmed database-declared FKs in the latest local snapshot. It
reports distinct dependent-table and FK counts separately, because one child
table may have multiple FKs to the same target. Each returned FK includes its
source/target, ordered column pairs, validation/inheritance, and
`DATABASE`/`CONFIRMED` provenance. `--limit` defaults to 20, maximum 100,
with `truncated` when further FK links exist. Bare ambiguous names fail; an
out-of-scope FK target is labeled partial. Self-FKs are included. This is a
structural one-hop hint, not verified application dependencies, transitive
impact, or inferred business lineage. No source connection is made.

`impact-tree TABLE --source NAME` walks incoming confirmed declared table FKs
from one exact saved table, showing distinct dependent tables and their
deterministic shortest paths. `--max-hops` defaults to 4 (0–8); `--limit`
defaults to 20 (1–100). JSON includes `dependent_table_count`, `truncated`,
and `scope: DECLARED_FK_TRANSITIVE_TABLE`. The root is excluded even for a
self-FK; use direct `impact` for self-links. More than 2,000 examined FK edges
or 500 visited tables fails with `IMPACT_BUDGET_EXCEEDED` (exit 7) rather than
returning a partial graph. This is structural reachability, not proven
application impact or column-level transitive lineage. No source connection
is made.

`impact-column TABLE.COLUMN --source NAME` narrows that structural view to
one exact saved target column. `schema.table.column` is also accepted; quoted
PostgreSQL identifiers keep case, dots, and escaped quotes. Bare ambiguous
table names fail, and an external FK target stub without saved columns returns
`COLUMN_NOT_FOUND` (exit 6) rather than inventing a column. Each returned
source-to-target pair includes its FK name, composite `pair_position` and
`pair_count`, validation/inheritance, and `DATABASE`/`CONFIRMED` provenance.
`reference_count` is the total number of matching pairs; `--limit` defaults
to 20, maximum 100, and `truncated` marks omitted pairs. A table with over
5,000 incoming FK edges fails with `IMPACT_BUDGET_EXCEEDED` (exit 7) rather
than returning a misleading partial count. No target database connection,
inference, application lineage, or transitive impact is involved.

`path FROM TO --source NAME` is implemented for one deterministic shortest
route through confirmed database-declared table-level FKs in the latest
snapshot. Traversal may follow an FK forward (source to referenced target) or
reverse; every step labels this direction while column pairs always retain
the FK's source-to-target order. The default maximum is 4 hops, configurable
from 0 to 8. The traversal stops after at most 500 visited tables or 2,000
examined FK adjacencies. A tied route is chosen by neighbor qualified name,
then edge insertion order. Identical endpoints return a zero-hop route.
`PATH_NOT_FOUND` (exit 6) means no route within the requested hops;
`PATH_BUDGET_EXCEEDED` (exit 7) means the search was cut short, not that no
route exists. `--json` returns exact identifiers and step metadata. Inferred
and pending edges are excluded. `status` and broader `context` remain planned.
All future inspection commands must keep output bounded.

## Visualization

```bash
graphit graph public.claim --source erp
graphit graph public.claim --source erp --format dot
graphit graph public.claim --source erp --format html --output claim.html
```

The implemented `graph` command writes compact JSON by default or DOT with
`--format dot`, or a standalone HTML/SVG view with `--format html`, to stdout
for the exact focus table's one-hop neighborhood in the latest local snapshot.
It contains bounded table nodes, confirmed database FK links, currently
eligible inferred approvals, and current manually approved links as distinct
`origin`/`status` combinations. Manual links carry the latest human reason but
no invented confidence score. Unreviewed or revoked proposals are omitted.
If an exact pair has both inferred and manual approval, one MANUAL link with
the human reason appears. FK, inferred-approved, and manual-link truncation
are flagged separately (`fk_truncated`, `approved_truncated`,
`manual_truncated`). Manual projection scans at most 5,000 source review
events and 200 adjacent approved pairs; overflow fails explicitly. The
command never opens a browser or contacts PostgreSQL. MCP `get_graph_context`
uses the same eligibility and provenance rules with a smaller response cap;
its payload is documented in `MCP.md`.
DOT and HTML use the same bounded projection and escape database identifiers.
Graphviz is optional and can render the DOT stream separately. HTML has an
accessible text list matching its inline SVG, searchable table/column/
constraint text, and filters for database FKs, inferred approvals, and manual
approvals. It loads no external assets and embeds one fixed script authorized
by its exact CSP SHA-256; database metadata is escaped into HTML/data
attributes and never interpolated into executable code. JavaScript-disabled
browsers retain the complete graph and lists without filtering. `--output
PATH` creates a UTF-8 file for any format but refuses to overwrite an existing
file. Wider depth controls, expand/collapse, and automatic browser launch
remain unimplemented.

## MCP

```bash
graphit mcp serve
graphit mcp inspect
```

`serve` uses stdio by default and writes protocol logs only to stderr. It never
prints banners or progress messages to stdout. `graphit mcp setup-codex
--project PATH` explicitly adds a project-local Codex MCP launcher while
preserving existing settings; it does not touch global configuration. An
existing incompatible Graphit entry is an error, not an overwrite. Add
`--compact-tools` to write Codex's supported `enabled_tools` allowlist for the
eight core discovery, table, relationship, path, and graph tools. This changes
only which Graphit tools Codex sees; the local server still implements all 14.
`graphit mcp setup-claude --project PATH` adds a project-local Claude Code
stdio launcher to `.mcp.json` with the same safety behavior. Its absolute
launcher paths are machine-specific; review before version-control commit.
Both setup commands accept explicit `--refresh` to replace an old launcher
matching Graphit's generated Python-module command shape. Without it, a
different existing entry remains an error. Refresh rejects custom entries,
extra settings, and edited Codex stanzas rather than silently rewriting them.
For Codex, `--refresh --compact-tools` switches a generated full profile to
compact, while `--refresh` switches a generated compact profile back to full.
A manually changed tool allowlist is treated as custom and is never replaced.

## Exit codes

```text
0 success
1 unexpected internal error
2 invalid command/configuration
3 project not initialized
4 source connection failure
5 scan failure
6 object not found or ambiguous
7 safety policy violation
```

Stable JSON errors:

```json
{
  "error": {
    "code": "PROJECT_NOT_INITIALIZED",
    "message": "Run graphit init in the project root.",
    "details": {}
  }
}
```
