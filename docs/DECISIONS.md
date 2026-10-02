# Graphit — Architecture Decision Log

## ADR-001 — Local SQLite is the default knowledge store

**Status:** Accepted; supersedes the previous PostgreSQL-metadata-store decision.

**Decision:** Store project knowledge in `.graphit/graphit.db` using SQLite.

**Reason:** Zero-service installation, portability, transactional writes,
indexed graph tables, recursive CTEs, and offline agent queries.

**Revisit when:** Benchmarks show a real local workload SQLite cannot satisfy or
a separately scoped shared-team mode is designed.

## ADR-002 — CLI and stdio MCP are the primary product surfaces

**Status:** Accepted and implemented by release `0.1.0`.

**Decision:** Build Typer CLI and stdio MCP before any web application.

**Reason:** The primary users are developers and coding agents inside existing
projects. A web stack adds installation friction without enabling the core value.

## ADR-003 — Support Python 3.11+

**Status:** Accepted; supersedes the Python-3.14-only requirement.

**Decision:** Keep syntax and dependencies compatible with Python 3.11 or newer.

**Reason:** A tool optimized for broad installation should support commonly
deployed maintained Python versions.

**Revisit when:** Dependency security or maintenance policy requires raising the
minimum.

## ADR-004 — PostgreSQL is the first source adapter

**Status:** Accepted.

**Decision:** Deliver one high-quality PostgreSQL path before other engines.

**Reason:** End-to-end correctness is more valuable than shallow adapter breadth.

## ADR-005 — No required frontend or API server

**Status:** Accepted; supersedes required Next.js/FastAPI architecture.

**Decision:** Core workflows run in-process through CLI and stdio MCP. Graph
visualization is exported locally.

**Revisit when:** User evidence supports an optional server/team product that
does not compromise the local workflow.

## ADR-006 — Source databases are read-only

**Status:** Accepted.

**Decision:** Scanners issue only metadata reads and explicitly bounded evidence
queries. Graphit never mutates sources.

## ADR-007 — Successful scans create immutable snapshots

**Status:** Accepted.

**Decision:** Do not overwrite prior scan metadata. Failed scans never become
latest.

**Reason:** Reproducibility, drift analysis, and safe recovery.

## ADR-008 — Core intelligence is deterministic

**Status:** Accepted.

**Decision:** Search, traversal, baseline relevant-context selection, and
relationship inference do not require an LLM.

**Reason:** Privacy, reproducibility, speed, cost, and explainability.

## ADR-009 — Graphit provides context, not SQL

**Status:** Accepted.

**Decision:** Do not provide SQL generation, optimization, or arbitrary SQL
execution. Codex, Claude, or dedicated SQL MCPs consume Graphit's context.

## ADR-010 — Progressive, bounded MCP responses

**Status:** Accepted.

**Decision:** MCP tools start with summaries and enforce object, edge, path,
depth, and response-size limits.

**Reason:** Token efficiency is core product behavior, not an afterthought.

## ADR-011 — Visualization is a local projection

**Status:** Accepted.

**Decision:** Start with JSON/DOT and later self-contained HTML exports generated
from application graph queries.

**Reason:** Preserve ERD value without requiring a server or Node.js runtime.

## ADR-012 — Separate distribution name from product command

**Status:** Accepted and implemented.

**Decision:** Use `graphit-db` as the PyPI distribution name while
keeping the product, Python import package, and CLI command named `Graphit`,
`graphit`, and `graphit` respectively.

**Reason:** The `graphit` PyPI name belongs to an unrelated legacy monitoring
project. `graphit-db` had no matching distribution when checked on 2026-09-16
and was secured by the successful `0.1.0` publication on 2026-10-02.

**Revisit when:** Trademark review or another material conflict requires a
broader product rename.

## ADR-013 — Initialize project state before initializing the store

**Status:** Superseded by ADR-014.

**Decision:** The first `graphit init` implementation writes safe configuration,
creates `.graphit/`, and updates the local Git ignore rule; it does not create
the SQLite file or modify agent configuration yet.

**Reason:** Keep filesystem safety and idempotency independently testable before
store migrations and client-specific integration are implemented.

**Revisit when:** SQLite migrations and agent integration are ready; extend init
through shared application services without breaking existing projects.

## ADR-014 — Version the local store and initialize it during `graphit init`

**Status:** Accepted.

**Decision:** `graphit init` creates `.graphit/graphit.db` using transactional,
checksum-verified SQLite migrations. It validates existing stores before writing
configuration and never replaces existing store rows, including with `--force`.

**Reason:** Users should have a ready-to-use local knowledge store after one
command while upgrades fail safely on unknown or modified schemas.

## ADR-015 — Persist source definitions in the local store

**Status:** Accepted.

**Decision:** `graphit source add/list/show` use SQLite for source connection
metadata. They persist an environment variable name, never a password value.
`graphit.toml` currently holds shareable scan, sampling, and context defaults,
not source records.

**Reason:** Source names need uniqueness and later scan/snapshot foreign keys;
the existing `sources` table provides these without duplicating configuration.

**Revisit when:** A portable source configuration import/export contract is
designed and its precedence against project-local state is explicit.

## ADR-016 — Use Psycopg for the first PostgreSQL adapter

**Status:** Accepted.

**Decision:** Use Psycopg 3 with a bundled binary distribution for the initial
PostgreSQL connection test. Apply read-only and timeout defaults at connection
startup, verify transaction read-only state with one SELECT, and expose only
sanitized failure categories.

**Reason:** A direct driver keeps installation simple, accepts separate
connection parameters, and supports the same adapter for later metadata reads.

## ADR-017 — Fail closed on bounded catalog scans

**Status:** Accepted.

**Decision:** Read initial PostgreSQL structure from parameterized `pg_catalog`
queries with hard table/column limits. Preserve raw identifier spelling in typed
in-memory metadata. Missing or inaccessible selected schemas and overflow are
errors, not partial successful scans.

**Reason:** Prevent unbounded metadata pulls and avoid presenting incomplete
schema context as an authoritative database graph.

## ADR-018 — Keep declared FK targets explicit even outside scan scope

**Status:** Accepted.

**Decision:** Preserve ordered source/target column pairs and actual referenced
table identity from `pg_constraint`, even when the target table is outside the
selected schemas. Mark that target as out of scope and retain the constraint's
validation and partition-inheritance flags.

**Reason:** A complete-looking invented target node or silently dropped FK
would mislead agents about join paths and database coverage.

## ADR-019 — Commit scan graphs atomically as immutable local snapshots

**Status:** Accepted.

**Decision:** Each successful `graphit scan --source` writes one new per-source
version, its scan run, objects, columns, and explicit edges in a single SQLite
transaction. Incomplete writes roll back. Failed source scans may leave a
sanitized failed-run record but cannot become the latest successful snapshot.

**Reason:** Agent context must never mix partial scans or overwrite historical
schema facts. Out-of-scope FK targets are stored only as flagged stubs.

## ADR-020 — Query only the latest complete local graph with bounded output

**Status:** Accepted.

**Decision:** The first `graphit search` requires a source alias, reads only
that source's latest completed snapshot, searches schema/table/column names
locally, and returns at most 100 deterministic matches. Human and JSON output
share one query service. Incomplete scans are never queryable.

**Reason:** Small, reproducible answers reduce agent context cost without
repeated target-database discovery. The service boundary can later be reused
by stdio MCP without duplicating CLI logic.

## ADR-021 — Resolve exact table context without guessing schema

**Status:** Accepted.

**Decision:** `graphit show` accepts PostgreSQL-style qualified identifiers,
uses only the latest completed local snapshot, and refuses ambiguous bare
table names. It returns bounded columns, declared keys, and outgoing FKs with
explicit ordering, validation, inheritance, and scope labels. Legacy `UNIQUE`
edge names remain readable; new snapshots use the documented `UNIQUE_KEY` type.

**Reason:** An agent should receive reliable facts for one table, not a broad
schema dump or a guessed match. Compatibility avoids invalidating snapshots
created before the edge-name correction.

## ADR-022 — Separate direct declared FK directions from inferred edges

**Status:** Accepted.

**Decision:** `graphit relationships` reads only the latest completed local
snapshot and returns direct incoming and outgoing table-level `REFERENCES`
edges whose origin is `DATABASE` and status is `CONFIRMED`. Each direction has
its own hard limit and truncation flag; a self-FK appears in both directions.

**Reason:** Agents need a small, trustworthy neighborhood around one table.
Declared facts must not be blended with future inferred candidates.

## ADR-023 — Use bounded breadth-first search for declared FK paths

**Status:** Accepted.

**Decision:** `graphit path` searches only the latest completed local snapshot,
traverses confirmed database-declared table-level FK edges in either direction,
and returns one deterministic shortest route. Each step preserves its actual
FK direction and ordered column pairs. Hop, visited-table, and examined-edge
budgets fail explicitly; inferred/pending edges never participate.

**Reason:** A short verified connection path is useful agent context, while an
unbounded graph search or a falsely reported missing path would mislead users.

## ADR-024 — Keep the first MCP overview factual and deterministic

**Status:** Accepted.

**Decision:** `database_overview` reads only the latest completed local
snapshot. It separates scanned schema/table counts from external target stubs,
counts confirmed declared table FKs, and returns bounded schema names and
in-scope entry tables. Entry tables rank by participating declared FK count,
then qualified name; the count is shown, and the ranking is not presented as
semantic importance. CLI exposure is deferred until there is a clear human
workflow for it.

**Reason:** Agents need a small, trustworthy orientation before search. A
deterministic graph fact is explainable without an LLM or source reconnection.

**Revisit when:** Task-aware relevance ranking is implemented and measured
against this simple navigation baseline.

## ADR-025 — Start task context with explainable lexical selection

**Status:** Accepted.

**Decision:** The first `get_relevant_context` tool uses local object-name
matches for up to eight de-duplicated task terms. It ranks by the number of
matched terms, scan scope, object type, then qualified name; reports matched
terms, truncation, and exact-table follow-up calls; and enforces object and
serialized-response budgets. It neither performs graph expansion nor claims
semantic understanding. If a rescan changes the latest snapshot between
searches, the request fails explicitly and can be retried.

**Reason:** This gives agents useful compact context and a measurable baseline
without an LLM dependency, source reconnection, or invented relationships.

**Revisit when:** Real task evaluations show that semantic/synonym matching,
column-to-table grouping, or bounded FK expansion improves precision and
token efficiency over this lexical baseline.

## ADR-026 — Make Codex wiring an explicit project-local command

**Status:** Accepted.

**Decision:** `graphit mcp setup-codex` appends only a Graphit MCP server table
to the initialized project's `.codex/config.toml`. It preserves existing
content, is idempotent for the same launcher, and refuses conflicts. The
launcher uses the absolute interpreter of the installed Graphit package and
the absolute project path. Neither `graphit init` nor this command changes
global Codex settings or stores source credentials.

**Reason:** Official Codex configuration supports trusted-project MCP tables.
An explicit command avoids surprise agent-settings changes, while absolute
paths make stdio startup independent of Codex's PATH and working directory.

**Revisit when:** Codex project configuration semantics change, or portable
team-shared wiring becomes a measured requirement.

## ADR-027 — Keep Claude wiring explicit and warn about portability

**Status:** Accepted.

**Decision:** `graphit mcp setup-claude` adds only `mcpServers.graphit` to the
initialized project's `.mcp.json`, retaining all other JSON settings. It
rejects malformed/duplicate-key JSON and conflicting Graphit definitions.
The launcher reuses Graphit's absolute installed-interpreter and project paths.
It never writes user/global Claude configuration or source credentials.

**Reason:** Claude Code's official project MCP format is `.mcp.json`; an
explicit command keeps agent wiring under user control. Absolute paths make
local startup robust but are machine-specific, so the CLI and docs warn before
the file is shared through version control.

**Revisit when:** A portable cross-machine launcher is needed and can be made
reliable without assuming the agent's PATH or current working directory.

## ADR-028 — Preview conservative inferred relationships without persistence

**Status:** Accepted.

**Decision:** First inference reads the latest completed local snapshot and
previews only exact `<table>_id` → `<table>.id` matches with a declared
single-column target key and an exact known PostgreSQL key type. Already
declared FK column pairs are excluded. Every candidate is `INFERRED` and
`PENDING`, has weighted metadata evidence and a provisional confidence score,
and is bounded by table/column/pair/output limits. No source connection,
sampling, SQLite edge write, or MCP exposure occurs in this slice.

**Reason:** The saved `type_family` is still `UNKNOWN`, and metadata alone
cannot prove a logical relationship. A narrow read-only preview establishes
precision and explainability before adding persistence or agent consumption.

**Revisit when:** Ground-truth evaluation and safe optional sampling support
broader names, type families, and calibrated scores.

## ADR-029 — Suppress inference from columns with declared FKs

**Status:** Accepted.

**Decision:** Metadata candidate rule v2 treats any source column covered by
a confirmed database-declared FK as resolved. It suppresses all inferred
alternatives from that column, using the table-level FK's ordered source
column pairs to cover composite and out-of-scope target cases. Unrelated
source columns remain eligible. The preview remains read-only and `PENDING`.

**Reason:** The fixed synthetic ERP challenge exposed a false legacy-customer
alternative for a column with a declared CRM FK. On that tiny fixture, v2
reduces false positives from 3 to 2 while keeping 4 true positives and 4
false negatives (precision 4/6 versus v1's 4/7). This is not a production
accuracy estimate.

**Revisit when:** A verified schema has multiple intentional FKs from one
column and users need those alternatives represented separately from inferred
hypotheses.

## ADR-030 — Abstain on ambiguous metadata-only target matches by default

**Status:** Accepted.

**Decision:** Rule v3 omits a source column from the default candidate preview
when two or three type-compatible, exact-name target tables exist. The
same-schema score bonus does not break the ambiguity. The explicit
`--include-ambiguous` diagnostic mode returns all alternatives (maximum three)
as unverified `PENDING` hypotheses. More than three are skipped in either mode.
The preview remains local, read-only, and absent from MCP.

**Reason:** On the fixed synthetic ERP challenge, abstention removes one false
legacy-customer candidate but also hides the genuine CRM-customer candidate:
default precision rises from v2's 4/6 to 3/4, while recall drops from 4/8
to 3/8. The external-account false positive remains; metadata cannot prove
identity. These tiny fixture rates do not estimate production accuracy.

**Revisit when:** Additional trustworthy evidence or a larger labeled corpus
can distinguish cross-schema duplicates without guessing from schema position.

## ADR-031 — Do not use unbounded source-value probes for external-ID inference

**Status:** Accepted.

**Decision:** Keep candidate preview metadata-only and offline. Do not add an
automatic `LIMIT`-based sample, `COUNT(DISTINCT)`, or value-overlap join to
resolve the external-account false positive. Any future source-value evidence
must be opt-in, explicitly scoped, read-only, demonstrably bounded in source
work as well as result size, timeout-protected, and tested on live PostgreSQL.
Never persist or log raw sampled business values, and never promote an
inferred edge to a database fact from overlap alone.

**Reason:** An external identifier and a local foreign key can have identical
structural metadata. A result `LIMIT` does not guarantee that PostgreSQL
avoids reading a large table, and matching values need not share business
identity. The present `0.75` account candidate is therefore an unverified
`PENDING` false positive on the fixed synthetic fixture, not a fact.

**Revisit when:** An opt-in evidence design has index/work-budget guarantees,
privacy review, failure-path handling, and live PostgreSQL tests; or a user
review decision can explicitly reject the candidate without source reads.

## ADR-032 — Scope explicit candidate rejections by source and exact column pair

**Status:** Accepted.

**Decision:** The first review action is an explicit `graphit review reject`
for a current `INFERRED` candidate. It requires the preview's snapshot version
and exact qualified source/target columns. Store a `REJECTED` decision in the
existing local `review_decisions` table using source-prefixed column keys.
Latest-snapshot validation and insertion occur under a SQLite write
transaction; a concurrent rescan fails closed. Repeated rejection is a no-op.
Preview filters rejected pairs before applying ambiguity rules, but does not
write inferred edges or expose them through MCP.

**Reason:** External identifiers cannot always be disambiguated from schema
metadata, so a human must be able to remove a known false hypothesis without
querying source values. Snapshot object keys omit source identity; the review
key must include the configured alias to prevent cross-source suppression.
Separate decisions survive immutable rescans without rewriting history.

**Revisit when:** Approval and restoration are implemented, including a
clear precedence rule for multiple decisions and a way to inspect review
history. Do not equate human approval with a database-declared FK.

## ADR-033 — Restore rejections through append-only review history

**Status:** Accepted.

**Decision:** `graphit review restore` requires a current snapshot version and
an exact source-scoped pair whose latest decision is `REJECTED` (or already
`RESTORED` for idempotency). It appends `RESTORED` rather than deleting rows.
Candidate preview applies only the latest decision for each pair. Restore is
allowed even if the pair is no longer a current candidate, so a retired schema
object does not leave a permanent hidden rejection if it later returns.
Re-rejecting a restored current candidate appends `REJECTED`. Neither action
creates a graph edge or accesses source values.

**Reason:** A mistaken review must be reversible without destroying its audit
trail. Keeping latest-decision semantics makes replay deterministic across
immutable rescans and maintains source isolation.

**Revisit when:** Review history is surfaced to users or an approval flow is
added; define how approved logical relationships coexist with restored
hypotheses without presenting either as database-declared FKs.

## ADR-034 — Keep human-approved hypotheses separate from database FKs

**Status:** Accepted.

**Decision:** `graphit review approve` accepts only an exact current inferred
candidate and snapshot version. It appends `APPROVED` to the source-scoped
review history; repeating approval is idempotent. A rejected pair must first
be restored. Candidate preview presents a still-eligible approved pair as
`origin: INFERRED`, `status: APPROVED`, with unchanged provisional metadata
score. If one of multiple target alternatives is approved, default preview
shows that choice while diagnostic mode retains the unreviewed alternatives
as `PENDING`. Declared-FK graph queries and MCP tools remain unchanged.

**Reason:** A human can resolve business identity that schema metadata alone
cannot prove, but their decision is not a constraint in PostgreSQL. Keeping
review status separate from origin and score avoids presenting a logical join
as a database fact or a calibrated probability.

**Revisit when:** Adding an explicit approval-reversal workflow, a reviewed
graph projection, or agent-facing logical relationships with provenance and
clear opt-in semantics. Do not silently merge approved and declared edges.

## ADR-035 — Revoke approval with an append-only, source-scoped event

**Status:** Accepted.

**Decision:** `graphit review revoke` requires an exact source-scoped pair with
a prior `APPROVED` decision and the current snapshot version. It appends
`REVOKED` rather than deleting approval history. Repeated revocation is a
no-op. Candidate preview treats a latest `REVOKED` as unreviewed: an eligible
pair returns to `PENDING`, and ambiguity rules apply again. Revocation is
allowed even when a rescan made the pair temporarily ineligible, so a later
schema return cannot silently revive the old approval. A revoked current
candidate can be approved or rejected again.

**Reason:** Human decisions can be mistaken or become outdated. An explicit
reversal preserves the audit trail while preventing stale approval from being
presented as a current logical relationship. It remains entirely local and
does not alter declared-FK graph or MCP behavior.

**Revisit when:** Review history and reviewed logical graph projections are
exposed; retain event ordering and clear provenance in those new views.

## ADR-036 — Begin graph visualization with bounded local JSON

**Status:** Accepted.

**Decision:** `graphit graph TABLE --source NAME` projects a one-hop table
neighborhood as JSON on stdout. It composes the existing declared-FK query and
current-candidate preview, including only `DATABASE/CONFIRMED` FKs and
`INFERRED/APPROVED` logical links. PENDING candidates never appear by default.
The exact focus and latest snapshot version are explicit; FK directions have
independent 100-link caps and approved candidates a 50-link cap, with truncation
flags. A snapshot change between the two reads fails rather than mixing scans.

**Reason:** JSON gives a testable, renderer-independent ERD foundation without
requiring a frontend or exporting the entire schema. Approval remains distinct
from a database constraint, and local query limits prevent accidental hairball
output.

**Revisit when:** Adding multi-hop, DOT/HTML, or MCP projection. Review whether
to take a single SQLite read transaction for both FK and approval reads and
whether a byte-size budget is needed for agent-facing output.

## ADR-037 — Skip inference for FK-only graph neighborhoods

**Status:** Accepted.

**Decision:** Before projecting approved links, read bounded, source-scoped
review events adjacent to the exact focus table and apply latest-event
semantics. If none is currently `APPROVED`, omit the expensive full-snapshot
candidate preview. If one exists, retain the existing candidate rule to check
its present eligibility; an old approval alone never invents a link. Compare
snapshot versions across FK and review reads and fail on a rescan.

**Reason:** An FK-only neighborhood must remain available even when the whole
snapshot exceeds the inference preview's table/work caps. Local review event
lookup is cheaper and does not weaken approval provenance or include PENDING
hypotheses.

**Revisit when:** Large numbers of review events warrant an indexed lookup or
per-table candidate validation. Keep review-change concurrency explicit if
the projection becomes an agent-facing contract.

## ADR-038 — Validate approved graph pairs with targeted metadata reads

**Status:** Accepted.

**Decision:** For approved-only table graph projection, derive bounded approved
source-column names from the current source-scoped review decisions. Read those
columns plus possible unique `id` targets from the latest local snapshot, and
inspect declared FKs only on their source tables. Feed this subset into the
same name/type/key, ambiguity, rejection, score, and status loop used by the
general candidate preview. Cap approved source columns, relevant columns,
declared FKs, evaluated pairs, and output; fail explicitly on work overflow.

**Reason:** A large snapshot should not make a small approved neighborhood
unusable. Reusing the candidate decision loop avoids treating review history
as proof of a currently valid relationship. A newly declared FK suppresses
the old inferred link and is displayed as the database fact instead.

**Revisit when:** Other inference rules add non-`id` targets or value-based
signals; the targeted read must remain behaviorally equivalent to the general
preview for approved pairs. Consider a shared SQLite transaction for all
projection reads before exposing reviewed links through MCP.

## ADR-039 — Render DOT from the bounded graph projection

**Status:** Accepted.

**Decision:** `graphit graph TABLE --source NAME --format dot` renders the same
one-hop projection as default JSON to stdout. A pure renderer quotes all
untrusted identifiers and labels, uses stable node IDs, marks scope and
truncation, and distinguishes `DATABASE/CONFIRMED` from `INFERRED/APPROVED`
with text and edge style. PENDING and unsupported link states are rejected.
Graphviz remains an optional external renderer, not a runtime dependency.

**Reason:** DOT gives a usable ERD-style artifact without introducing a
frontend, server, browser, network call, or second graph-query implementation.

**Revisit when:** Adding self-contained HTML or output files. Keep both formats
driven by the same projection contract and avoid treating review approval as
a database-declared FK.

## ADR-040 — Ship a static offline HTML graph before interactive UI

**Status:** Accepted.

**Decision:** `graphit graph TABLE --source NAME --format html` renders the
same bounded projection into a script-free document with inline SVG, a
matching accessible table/relationship list, escaped metadata, local CSP, and
visible scope/provenance/truncation labels. The layout is deterministic and
scrollable, not a new graph-query engine. All graph formats continue to use
stdout by default; `--output PATH` explicitly creates a UTF-8 file with
exclusive-create semantics and never overwrites an existing path. No browser
launch, server, CDN, Graphviz, or JavaScript is required.

**Reason:** A useful, directly openable ERD view should not make frontend
infrastructure mandatory. Text details remain complete when a dense SVG is
hard to read, while exclusive file creation avoids accidental data loss.

**Revisit when:** Adding search/expansion, multi-hop layout, or browser opening.
Keep the HTML output offline and preserve an accessible non-graph equivalent.

## ADR-041 — Offer reviewed graph context as a separate MCP tool

**Status:** Accepted.

**Decision:** Add read-only `get_graph_context` for one exact table. It reuses
the same local, bounded graph projection as CLI/visualization but serializes
only compact agent-relevant fields. Default/hard per-category limits are 8/20
for incoming FKs, outgoing FKs, and approved links. Links retain explicit
`DATABASE`/`CONFIRMED` or `INFERRED`/`APPROVED` provenance; approved links
carry a provisional metadata score and compact evidence. PENDING links are
excluded. Existing `get_relationships` stays confirmed-FK-only, and the MCP
12,000-character response ceiling remains in force.

**Reason:** Agents need a useful neighborhood without repeatedly reading
full schemas, but a human approval must never be represented as a database
constraint. A separate tool prevents changing the meaning of an established
fact-only API while avoiding duplicate graph-query logic.

**Revisit when:** Measuring token savings and answer quality on real ERP
tasks, or adding multi-hop/impact context. Keep source and review decision
changes observable and fail explicitly on work or response-budget overflow.

## ADR-042 — Treat focused context size as a labeled baseline, not a token claim

**Status:** Accepted.

**Decision:** Maintain a deterministic MCP context benchmark on the existing
hand-labeled ERP-like metadata fixture. Supply the correct focus table as an
oracle, compare each actual compact `get_graph_context` response with one
compact all-table structural JSON baseline, and report both observed labeled
relationship coverage and character counts. Keep known misses and a labeled
negative visible in the benchmark; do not tune labels to the implementation.

**Reason:** Graphit's core promise is less rediscovery with trustworthy facts.
A size-only comparison can conceal missing relationships or assume that the
agent chose the right table. The first result is 2/4 positive links present,
0/1 labeled negative exposed, and 236–816 characters per focused response
versus 2,315 for the all-table baseline. It is not a token or task-success
measurement.

**Revisit when:** Testing focus-table selection, multi-tool task flows, real
ERP schemas, or tokenized agent traces. Keep source data private and avoid
claiming generalized savings from this synthetic set.

**2026-09-16 measurement:** The oracle-free continuation sends the same five
tasks to `get_relevant_context` and follows its suggested exact tables into
`get_graph_context`. Expected focus is top-1 for 3/5 and top-3 for 5/5, but
the selector plus even one graph call exceeds the 2,315-character all-table
baseline in every case. Known relationship coverage remains 2/4 positives
and 0/1 negative exposed. This motivates measuring a smaller selector or
more selective follow-up before claiming token efficiency.

## ADR-043 — Omit unavailable object attributes from task-context MCP JSON

**Status:** Accepted.

**Decision:** `get_relevant_context` keeps its object ranking, names, matched
terms, reasons, scope labels, follow-up calls, and truncation fields, but omits
per-object fields whose value is unavailable (`null`) in MCP JSON. The domain
result remains typed; its character-budget check uses the same serializer as
the MCP response. Consumers must treat an absent column attribute as unknown
or not applicable, never as `false`.

**Reason:** The five-question fixture showed repeated `null` column fields on
tables and schemas were pure serialization overhead. Their removal saved
144-288 characters per selector response without changing top-3 focus
discovery or known relationship coverage. This is a wire-shape change for
clients that explicitly expected `null`; it is documented in `MCP.md`.

**Revisit when:** A stable versioned MCP schema is introduced or a real client
requires explicit-null compatibility. Do not weaken the hard response budget
or remove uncertainty flags to chase smaller outputs.

## ADR-044 — Progressive graph follow-ups without automatic pruning

**Status:** Accepted.

**Decision:** Keep up to three exact table suggestions in task context, ordered
by current lexical ranking. Guide MCP clients to inspect them one at a time
and stop only when task-specific evidence is sufficient. Never interpret an
empty one-hop graph as proof that a relationship is absent.

**Reason:** In the five-question synthetic fixture, both surfaced positive
links appear on the first graph call; the next two calls add no labeled link.
An idealized label-aware stop would save 1,894 response characters across the
five tasks versus always reading all three, but an agent's ability to recognize
the relevant evidence is unmeasured. Removing other suggestions would also
hide alternatives that might matter on different tasks.

**Revisit when:** Real agent traces and larger schemas show whether progressive
guidance is followed and whether it improves task success, cost, and latency.

## ADR-045 — Break lexical context ties with exact object names

**Status:** Accepted.

**Decision:** After matched-term count, scope, and object kind, rank an object
whose own name exactly matches a task term ahead of partial qualified-name
matches. Keep the existing candidate, object, and character caps. Do not infer
semantic identity from this lexical preference.

**Reason:** In a 174-table synthetic fixture with 60 unrelated
`customer_invoice_*` tables, alphabetical tie-breaking hid `sales.payment`
and `sales.invoice` from all three suggestions, dropping reachable labeled
positive links from two to one. The exact-name tie-break restores both
focuses to the top three and restores the second known link. On the smaller
fixture it moves `billing.invoice` from first to second behind the equally
plausible exact-name `finance.account`; both remain offered. This is a
measured recall/ordering trade-off, not semantic ranking.

**Revisit when:** Real tasks reveal different tie behavior, or per-term search
caps exclude exact relevant objects before final ranking. Keep exact case and
quoted identifier handling under test.

## ADR-046 — Record cross-name human proposals without graph exposure

**Status:** Accepted.

**Decision:** `graphit review propose` records an exact source-scoped column
pair and a bounded human reason as a local `MANUAL_PROPOSAL` / `PROPOSED`
review event. It requires the latest snapshot, two existing distinct columns,
a single-column target key, exact stored type equality, and no declared FK on
the source column. An identical repeat is idempotent; a changed reason appends
history. Manual proposals do not enter the inferred-candidate preview,
approved graph, or MCP context. The existing `review approve` does not accept
them.

**Reason:** The measured `support_ticket.client_id` to `customer.id` gap is a
semantic synonym that metadata naming rules cannot safely infer. A user can
record their knowledge without teaching Graphit to present it as a database
fact or incurring source queries.

**Revisit when:** Adding an explicit manual-approval/revoke workflow with
provenance and evidence visible to agents. Keep legacy inferred
review events and confirmed FKs separate.

## ADR-047 — List latest manual proposals with structural staleness

**Status:** Accepted.

**Decision:** `graphit review proposals` reads source-scoped manual proposal
events from local SQLite, collapses append-only reason history to the latest
event per pair, and pages newest-updated pairs with a 50-item cap. It labels
each visible pair against the latest successful snapshot as structurally
`ELIGIBLE` or with a specific missing-column, removed-key, changed-type, or
new-declared-FK reason. A 5,000-event source budget fails explicitly. The
listing does not approve, hide, or expose proposals through graph/MCP.

**Reason:** A human needs to inspect what was proposed and why before a
separate approval action. Rescans must not silently turn stale knowledge into
an agent-visible link, and older reason events must remain auditable without
appearing as duplicate active proposals.

**Revisit when:** A growing history needs indexed/cursor pagination, or manual
approval requires richer evidence and provenance. `ELIGIBLE` remains only a
structural precondition, never semantic validation.

## ADR-048 — Approve manual proposals as separate local review events

**Status:** Accepted.

**Decision:** `graphit review approve-proposal` requires an exact existing
source-scoped manual proposal and current snapshot version, rechecks saved
column/key/type/FK eligibility under a SQLite write transaction, and appends
an `APPROVED` manual review event carrying the latest human reason. Repeats
are idempotent. Active conflicting inferred review decisions block approval;
`review propose` cannot silently overwrite a manual approval. Manual approvals
remain absent from graph and MCP projection until a separate provenance-safe
exposure design is implemented.

**Reason:** The user's business knowledge can resolve a name-synonym gap, but
recording that knowledge and treating it as an agent-visible relationship
must be separate, auditable actions. Local approval is not a database FK and
must not be conflated with inference's metadata confidence.

**Revisit when:** Adding a reviewed graph/MCP projection. Preserve the human
reason, current-snapshot eligibility checks,
and distinct `MANUAL` versus `DATABASE` provenance.

## ADR-049 — Revoke manual approval without losing proposal history

**Status:** Accepted.

**Decision:** `graphit review revoke-proposal` appends a source-scoped
`REVOKED` manual event retaining the latest reason. It requires the current
snapshot version but permits an ineligible pair, so stale approvals can be
withdrawn. Repeated revocation adds no event. A revoked pair remains listed
and cannot be approved directly; the user must explicitly re-propose it,
even with the same reason, then approve again. Human proposal listing must
render the actual latest `PROPOSED`/`APPROVED`/`REVOKED` status.

**Reason:** An approval is a user assertion that must be reversible without
erasing who asserted what. A later schema change must not trap an obsolete
approval, and rerunning a proposal must not silently restore withdrawn trust.

**Revisit when:** Manual approvals become visible in MCP context; revocation
already removes them from local graph output and must also remove them from
future agent projections while keeping confirmed database FKs separate.

## ADR-050 — Project eligible manual approvals into local graph only

**Status:** Accepted.

**Decision:** The `graphit graph` CLI explicitly includes current,
structurally eligible `MANUAL`/`APPROVED` links in the one-hop projection,
carrying exact columns and the latest human reason without an invented score.
`PROPOSED`, `REVOKED`, and stale approvals are omitted. An identical inferred
approved pair is shown once, preferring the manual reason; declared FKs remain
separate facts. Manual review reads cap source history at 5,000 events and
adjacent approved pairs at 200, with a 50-link output cap and its own
`manual_truncated` flag. DOT and offline HTML label the provenance and escape
untrusted reasons. The shared graph service defaults to excluding manual
links; only the local CLI opts in, so the existing MCP payload stays unchanged.

**Reason:** The user needs an ERD-like view of reviewed cross-name knowledge,
but agent-facing MCP exposure requires its own provenance, payload-size, and
task-grounding review. Silent reuse of the shared graph service would have
changed MCP behavior in this slice.

**Revisit when:** Real agent traces show whether manual links improve task
grounding or inflate context. Keep `get_relationships` and path queries
confirmed-FK-only.

## ADR-051 — Expose reviewed manual links in bounded MCP graph context

**Status:** Accepted.

**Decision:** `get_graph_context` opts into the same current manual projection
as the local graph command. A manual link has `origin: MANUAL`,
`status: APPROVED`, exact column pairs, and `human_reason`; it has no
metadata score or FK name. The existing per-category 1-20 `limit` now also
caps manual links. `manual_truncated: true` is emitted only when that category
is cut, preserving the earlier payload shape otherwise. The global 12,000-
character ceiling still rejects oversize output. `get_relationships` and FK
path tools remain declared-FK-only; proposed, revoked, and stale manual
events are never returned.

**Reason:** The known ERP synonym miss cannot help Codex or Claude until an
agent-facing tool can retrieve the reviewed relation. Reusing the tested
projection avoids a second eligibility rule while explicit provenance and
a short human reason prevent presenting the assertion as a database fact.

**Revisit when:** Real Codex/Claude traces and larger-schema benchmarks can
measure task success, response characters, tokens, and latency. The fixed
synthetic test only proves that one additional manual link adds fewer than
1,000 serialized characters and respects the hard response ceiling; it does
not establish production token savings. A later five-question agent-style
synthetic benchmark showed labeled positive-link visibility rise from 2/4 to
3/4 after one explicit synonym approval, at 1,487 additional serialized
characters across 15 graph responses; the selector did not change. This is
still not a real-agent outcome or tokenizer measurement.

## ADR-052 — Persist PostgreSQL view kinds without inventing lineage

**Status:** Accepted.

**Decision:** The PostgreSQL scanner includes `pg_class.relkind` `v` and `m`
alongside ordinary and partitioned tables in the existing bounded relation
and column reads. The shared `max_tables` cap covers all four kinds. Typed
scan metadata carries `TABLE`, `VIEW`, or `MATERIALIZED_VIEW`; immutable SQLite
snapshots use the corresponding already-defined object types and keep view
columns under their parent. Local search and task selection return these
explicit kinds. `show`/`get_table`, declared-FK traversal, graph context,
and path tools remain table-only; no view definition or dependency edge is
inferred from column names.

**Reason:** Agents should discover that a named view exists and see its
catalog columns without mistaking it for a base table or inventing lineage.
Using existing object types avoids an unnecessary store migration, while
retaining read-only source and bounded-response behavior.

**Revisit when:** A separate definition/lineage slice can provide verified
source dependencies. Bounded view-detail follow-ups were added in ADR-053.
Live PostgreSQL integration remains required before claiming complete scanner
coverage.

## ADR-053 — Separate bounded view detail from table/FK facts

**Status:** Accepted.

**Decision:** Add `show-view` and MCP `get_view` as thin adapters over one
snapshot-only `show_view` query. It resolves exact quoted view/materialized-
view names, rejects ambiguous bare names and tables, and returns only the
explicit kind, ordered catalog columns, a `not_null_declared` flag, and a
column-truncation flag. The task selector can suggest `get_view` for selected
views while retaining `get_table` for tables. CLI and MCP have independent
bounded limits; MCP retains its global response ceiling. Existing table,
FK, graph, and path contracts are unchanged.

**Reason:** A discovered view must be inspectable by agents without implying
that it is a base table or that Graphit knows its definition, dependencies,
or actual result nullability. `not_null_declared: false` is a catalog absence,
not proof that NULL values occur.

**Revisit when:** Verified view definitions or lineage can be offered within
safe response and source-read budgets. End-to-end real PostgreSQL integration
is still required before claiming this behavior is production-validated.

## ADR-054 — Opt-in real PostgreSQL scanner integration

**Status:** Accepted.

**Decision:** Keep the default test suite service-free, and add an explicit
`GRAPHIT_TEST_PG_DISPOSABLE=1` plus loopback admin-DSN gate for a real
PostgreSQL integration test. A separate admin fixture creates unique schemas,
catalog objects, and a temporary source-schema read-only reader role, then removes only
its own generated objects. Graphit's connection verification, scanner, and
snapshot path run as that reader with their normal read-only session settings.
The test covers real table/view catalog kinds, columns, ordered composite and
self FKs, permissions, hard caps, and local snapshot queries.

**Reason:** Scripted-driver tests cannot validate PostgreSQL catalog SQL or
role behavior. Explicit opt-in and a disposable server guard prevent fixture
DDL from silently targeting a normal user database. No container runtime is
added to Graphit's runtime dependency set.

**Revisit when:** CI can provision the disposable PostgreSQL service and run
this test on each supported Python version. Add further integration fixtures
for partitions, timeout paths, and catalog privilege variations before
claiming exhaustive PostgreSQL coverage.

## ADR-055 — Run guarded PostgreSQL integration in the Python CI matrix

**Status:** Accepted.

**Decision:** Add a single GitHub Actions workflow for `push`, `pull_request`,
and manual runs. On an Ubuntu runner, each Python 3.11–3.14 matrix job uses an
ephemeral PostgreSQL 16 service and the existing explicit disposable-test
gate. It runs installation, dependency consistency, Ruff lint/format, strict
mypy, and all pytest tests. Repository token permission is `contents: read`,
checkout credentials are not persisted, and a 15-minute job timeout applies.
The PostgreSQL password is a local test fixture value, not a secret from a
user database.

**Reason:** The real catalog test should protect every supported Python
version continuously. A service container exists only during CI and does not
add Docker or PostgreSQL to the normal Graphit installation.

**Revisit when:** The first remote workflow run reveals platform-specific
failures or CI cost. Locked dependencies, vulnerability scanning, package
artifact checks, and real-agent utility evaluations remain separate gates.

## ADR-056 — Apache-2.0 package license and explicit source archive scope

**Status:** Accepted.

**Decision:** Include the canonical Apache License 2.0 text and a concise
`NOTICE` naming copyright holder Onur Parapan at repository root. Declare
`Apache-2.0`, `LICENSE`, `NOTICE`, and author Onur Parapan in Python package
metadata. Allowlist only source code, tests, public documentation, README,
LICENSE, NOTICE, and `pyproject.toml` in the source distribution. Keep wheel
contents limited to the Graphit package and required distribution metadata.
Do not publish to PyPI as part of this local packaging check.

**Reason:** The user approved Apache-2.0. Explicit archive selection prevents
local OpenWolf state and agent configuration from leaking into a release while
retaining the files needed to rebuild and inspect the package.

**Revisit when:** A release needs additional public files or attribution
changes. Verify the chosen PyPI distribution name and complete
release/provenance checks before publication.

## ADR-057 — CI gate for distribution contents and isolated install

**Status:** Accepted.

**Decision:** Add a dedicated, read-only CI package job that builds source and
wheel archives, validates their allowed contents, exact author, Apache-2.0
metadata, exact license and notice bytes, and the `graphit` console entry point,
then installs the wheel with runtime dependencies in a clean virtual
environment. Keep this separate
from the PostgreSQL/Python test matrix and do not add a publish permission or
upload step. Unit tests cover acceptance and representative archive regressions.

**Reason:** A successful build alone did not reveal that Hatchling's original
source archive included local agent state. One focused package job catches
that class of leak and installation breakage on each CI run without making all
four test matrix jobs repeat the same pure-Python artifact build.

**Revisit when:** The first remote CI run is available, when package versions
become dynamic, or when release signing/provenance and publication are designed.

**2026-10-02 evidence:** GitHub Actions run `37011201866` passed all four
Python/PostgreSQL matrix jobs and the separate Python 3.14 distribution job.
Two earlier package-job failures exposed that resolving a POSIX venv's Python
symlink escaped the installed environment; preserving the absolute symlink path
fixed the installed Codex/Claude MCP launcher smoke.

The installed-wheel smoke also creates a disposable project and verifies its
SQLite store, then runs Codex/Claude project setup and checks both generated
stdio commands with MCP client handshakes/tool listings. It preserves seeded
unrelated project settings, uses the installed interpreter, and closes its
read-only SQLite inspection connection before removing the temporary project.
The absolute interpreter path deliberately preserves a POSIX virtual
environment's `python` symlink; resolving it would escape to the base Python
and lose the installed package. The commands contain absolute interpreter and
project paths, so this check does not establish portability after moving
either location or actual agent application loading/approval.

## ADR-058 — Explicit refresh of stale project MCP launchers

**Status:** Accepted.

**Decision:** Keep Codex and Claude setup conflict-safe by default. Add an
explicit `--refresh` option that accepts only an existing entry with Graphit's
exact absolute Python `-m graphit mcp serve --project` launch shape and no
extra fields. Codex refresh additionally requires the original generated
stanza bytes so unrelated TOML content and comments remain untouched; custom
or edited stanzas fail closed. Claude preserves unrelated JSON values. Do not
touch global agent settings or infer ownership from the `graphit` name alone.

**Reason:** Absolute interpreter and project paths become stale after moves,
but silently replacing an existing named MCP server could overwrite a user's
custom configuration. Explicit refresh makes the common generated case
recoverable without weakening the default conflict boundary.

**Revisit when:** A portable install/launcher contract is designed, or an
official Codex/Claude configuration format change requires different fields.

## ADR-059 — Pin a local text-token benchmark without claiming agent cost

**Status:** Accepted.

**Decision:** Add `tiktoken==0.14.0` only to the development extra and count
each existing ERP benchmark MCP text response independently with `o200k_base`.
Compare their summed text tokens with the separately encoded all-schema JSON
baseline on unchanged 14- and 114-table fixtures. Keep the labeled retrieval
outcomes and the small-fixture counterexample beside the counts.

**Reason:** Character counts alone cannot test Graphit's token-efficiency
goal. A pinned encoding makes the text measurement reproducible without
transmitting schema context to a remote model or changing runtime/source
access. It does not count message/tool overhead or establish equal-information
compression, actual agent usage, or successful SQL tasks.

**Revisit when:** Real-agent traces and consented model-specific token counts
can be measured with comparable information and task-success criteria.

## ADR-060 — Make the selector follow-up count an explicit budget

**Status:** Accepted.

**Decision:** Keep three suggestions as the default, but allow
`get_relevant_context(max_followups=1..3)` to return fewer ranked table/view
follow-ups without changing the selected objects or ranking. Do not interpret
one returned suggestion as an exhaustive search or an absent relationship.

**Reason:** On the fixed ERP benchmark, opening only the first suggested graph
preserves the two already visible labeled links and reduces response text
tokens, but a single suggestion is not generally complete. An opt-in limit
lets clients control navigation cost without silently lowering the default
discovery breadth. The small 14-table all-schema baseline remains cheaper
than this one-follow-up path for three of five tasks.

**Revisit when:** Real-agent evaluations show when a client should request
additional suggestions and whether a narrower default improves task success
per token without hiding important relationships.

## ADR-061 — Start snapshot drift with exact in-scope structural facts

**Status:** Accepted.

**Decision:** Add a read-only `graphit diff` command for two explicitly chosen,
completed source snapshot versions. Compare exact saved identities for in-scope
schemas, tables, views, materialized views, and columns, plus stored table
partition and column structural properties. Exclude external FK stubs, and
label the result `SCHEMA_RELATION_COLUMN`. Do not infer renames or claim that
unchanged output covers keys, FKs, reviews, or view lineage. Bound source
object reads and returned changes, failing rather than silently partially
scanning an oversized snapshot.

**Reason:** Users need trustworthy drift evidence from immutable local scans
without reconnecting to PostgreSQL. Starting with facts directly persisted in
the existing schema gives a small, testable CLI slice and avoids presenting an
incomplete constraint comparison as a full-schema diff.

**Revisit when:** Key/FK comparisons are added as a separately tested fact
category, or measured large-catalog workloads require streaming/pagination
instead of the current 100,000-object fail-closed work cap.

## ADR-062 — Compare declared constraint definitions in local drift

**Status:** Accepted; extends ADR-061.

**Decision:** Enrich the existing exact snapshot diff with declared
primary/unique key kind, ordered columns, and inheritance from saved
constraint objects. Pair every saved FK constraint with exactly one confirmed
database table-level `REFERENCES` edge and compare its target table/scope,
ordered column pairs, validation, and inheritance. Reject missing, duplicate,
or inconsistent pairs as corrupt local data rather than reporting a partial
diff. Keep external target stubs themselves excluded. Change the JSON `scope`
value to `SCHEMA_RELATION_COLUMN_KEY_FK`; this is an intentional contract
change from the earlier `SCHEMA_RELATION_COLUMN` value. Review decisions,
indexes, and view lineage remain outside scope.

**Reason:** Key and FK definitions can change while table and column names stay
identical. The writer stores key facts in constraint metadata but FK target and
column pairs in table edges, so comparing only one representation would miss
or misstate drift. Using the constraint object as stable identity and checking
its edge keeps provenance exact, including legacy unique-edge spellings.

**Revisit when:** Index metadata, verified view dependencies, or review-history
diffs are added with separate provenance and bounds; larger catalogs may need
streaming rather than the current fail-closed work limit.

## ADR-063 — Expose exact local snapshot drift through MCP

**Status:** Accepted.

**Decision:** Add one read-only `compare_snapshots` MCP tool requiring a source
and two explicit completed versions. Route it through the same
`compare_snapshots` service as `graphit diff`, including the same scope, total
change count, truncation, and error codes. Serialize one compact JSON text
block through the existing 12,000-character MCP response ceiling; callers can
reduce `limit` when the selected page is too large. Never connect to the
target database or auto-select comparison versions.

**Reason:** Agents need trustworthy, bounded schema drift without repeating
catalog discovery or receiving a different definition of change from CLI users.

**Revisit when:** A measured workflow needs cursor-based diff pagination while
preserving stable snapshot identity and response bounds.

## ADR-064 — Discover completed local snapshot versions explicitly

**Status:** Accepted.

**Decision:** Expose a source-scoped, newest-first completed snapshot listing
through one local service, `graphit snapshots`, and read-only MCP
`list_snapshots`. Return version, completion timestamp, `COMPLETED` status,
total count, and truncation under bounded output. A known source with no
completed scan returns an empty list; failed or unfinished runs are omitted.
Do not auto-select the endpoints of a diff.

**Reason:** Agents and users need discoverable version numbers before making
an explicit drift request, without source connectivity or a full scan log.

**Revisit when:** More scan-run diagnostics or cursor-based history traversal
has a demonstrated workflow need.

## ADR-065 — Page exact snapshot drift with an explicit offset

**Status:** Accepted.

**Decision:** Add a nonnegative bounded `offset` to the shared local snapshot
diff and its CLI/MCP adapters. Keep the same exact version pair, deterministic
qualified-name ordering, full change count, and 500-change page cap. Report
the requested offset and make `truncated` mean more changes after that page.
An offset at or beyond the count returns an empty page. The maximum offset is
200,000, matching the maximum distinct facts from two bounded 100,000-object
snapshots. The MCP response retains its separate 12,000-character ceiling.

**Reason:** A previously truncated diff could not be read beyond its first
page, so large source changes were not fully inspectable. Explicit offsets
avoid silently auto-selecting versions or claiming a partial page is complete.

**Revisit when:** Measured catalog sizes justify streaming change enumeration
instead of sorting the bounded fact union in memory.

## ADR-066 — Direct structural impact is declared-FK-only

**Status:** Accepted.

**Decision:** Add a bounded one-hop `table_impact` service, exposed as
`graphit impact` and MCP `get_impact_context`, over incoming confirmed
database-declared table FKs in the latest completed local snapshot. Reuse
exact table resolution and FK detail parsing. Report distinct referencing
table count separately from FK count, preserve provenance and target scope,
and label the result `DECLARED_FK_DIRECT`. Do not include approved inferred
or manual links, transitive reachability, view lineage, or application code.

**Reason:** A reliable first answer to “what might reference this table?” is
useful, but calling it full impact analysis would misrepresent the saved
catalog graph. Multiple FK constraints from one table must not inflate the
number of dependent tables.

**Revisit when:** Verified view/code lineage or bounded multi-hop structural
impact can be added with distinct provenance and completeness labels.

## ADR-067 — Narrow structural impact to exact declared FK column pairs

**Status:** Accepted.

**Decision:** Add `column_impact` behind `graphit impact-column` and MCP
`get_column_impact`. Resolve an exact saved target column using PostgreSQL
identifier rules, then inspect bounded incoming confirmed database FK
definitions and return only pairs whose target column matches. Preserve the
pair's position within a composite FK and label the result
`DECLARED_FK_COLUMN_DIRECT`. Fail closed above 5,000 incoming FK edges; do not
claim columns for external target stubs or include inferred/manual edges.

**Reason:** Table-level impact can overstate the relevance of a change to one
column. Exact pair matching gives agents a smaller, auditable answer without
inventing application lineage or treating every FK on the table as affected.

**Revisit when:** Indexed column-level FK lookup can be proven consistent with
table-level composite metadata and improves measured large-catalog workloads.

## ADR-068 — Exercise current MCP facts from a clean wheel

**Status:** Accepted.

**Decision:** Keep the package smoke's expected MCP tool-name set aligned with
the documented public surface, verify every listed tool is marked read-only,
and call `get_column_impact` over a tiny synthetic snapshot through each
generated Codex/Claude stdio launcher. Seed the snapshot using the installed
package without source credentials or a live PostgreSQL connection. Keep the
existing 15-second handshake cap and unrelated-settings preservation checks.

**Reason:** A three-tool name handshake could pass even if newer agent-facing
tools were omitted or unusable in a built wheel. A compact real response tests
the installed route from local snapshot to MCP payload without a running agent
application.

**Revisit when:** A stable external compatibility suite can replace the
hard-coded public tool contract or when packaging targets expand.

## ADR-069 — Start index support with bounded, non-decompiled catalog facts

**Status:** Accepted.

**Decision:** Read live `pg_index`/`pg_class`/`pg_am` metadata for indexes on
selected tables and materialized views in the existing read-only repeatable-
read scan. Preserve key order separately from included columns, marking an
expression key position without retrieving its SQL. Keep uniqueness,
primary, validity, readiness, partiality, and access method as typed facts.
Bound index rows independently and reject incomplete attribute mappings.
Do not persist index definitions or expose them through default MCP context
in this first scanner-only slice.

**Reason:** PostgreSQL's [`pg_index` catalog](https://www.postgresql.org/docs/16/catalog-pg-index.html)
defines `indnkeyatts`, `indkey` expression zeroes, and validity/readiness;
[`pg_class`](https://www.postgresql.org/docs/16/catalog-pg-class.html) identifies
ordinary and partitioned index relations. Returning decompiled expression or
predicate SQL would expand context and could reveal business logic without
being needed to identify the index's structural role.

**Revisit when:** Exact bounded lookup is ready; ADR-070 records that
transactional persistence used the existing schema without a migration.
Then validate a real disposable PostgreSQL scan and keep default agent
responses free of index dumps.

## ADR-070 — Persist index structure using existing snapshot objects and edges

**Status:** Accepted.

**Decision:** Store each scanned index as an `INDEX` object beneath its table
or materialized view, with safe flags and ordered key/include names in its
metadata. Use `CONTAINS` for the parent and positioned `INDEX_KEY` /
`INDEX_INCLUDE` edges only for real saved columns. Expression positions are
`null` placeholders, never invented columns. Reject a missing relation/column
or malformed index in the same transaction that writes the snapshot. Add no
SQLite migration: schema v1 already stores extensible object/edge kinds and
JSON metadata. Preserve existing CLI/MCP defaults, which do not read indexes.

**Reason:** This fits the durable graph without a redundant table or migration,
keeps old snapshots readable, and makes future exact index lookup possible
without carrying index lists in every agent response.

**Revisit when:** Bounded index lookup is implemented; inspect the saved
metadata contract, actual PostgreSQL integration result, and any need for
index-specific validation or migration before publication.

## ADR-071 — Keep index context opt-in and page exact local facts

**Status:** Accepted.

**Decision:** Add a shared `list_table_indexes` query for one exact saved
table or materialized view and expose it first as `graphit indexes`. Use
PostgreSQL identifier semantics, a 20-item default/100-item hard page,
bounded offset and attribute count, total count, and explicit truncation.
Validate each returned index object's safe metadata against its parent and
positioned column edges; fail closed on corruption. Keep search, table
detail, overview, and the existing MCP tools index-free by default.

**Reason:** Indexes are useful when diagnosing data access but a complete
index dump would consume agent context without helping most relationship
questions. An explicit page provides inspectable facts and a reusable core
for a later compact MCP tool.

**Revisit when:** MCP index lookup is added and actual-agent retrieval
measurements indicate whether the page budget or follow-up guidance needs
adjustment.

## ADR-072 — Expose saved index context through an opt-in MCP tool

**Status:** Accepted.

**Decision:** Add read-only `get_index_context` as a thin adapter over
`list_table_indexes`. Use a smaller 5-item default and 20-item hard MCP page,
the service's bounded offset, one compact JSON text block, and the shared
12,000-character response ceiling. Keep existing overview/table responses
index-free. Verify the tool through in-process and stdio clients and both
generated installed-wheel launchers.

**Reason:** Agents can request index facts only when a task needs them, without
paying an index-token cost during normal relationship discovery. One shared
query keeps CLI and MCP facts consistent; the ceiling makes dense pages fail
closed instead of flooding context.

**Revisit when:** Real Codex/Claude tasks measure retrieval utility and token
cost, or dense index pages frequently hit the response ceiling.

## ADR-073 — Keep transitive impact structural and fail closed on graph budgets

**Status:** Accepted.

**Decision:** Add an application-level traversal of incoming, confirmed,
database-declared table FKs from one exact saved table. Return each distinct
dependent table's deterministic shortest path from the root, bounded by hops,
visited tables, examined FK edges, and returned items. Exclude the root from
the distinct-table count even for a self-FK; the existing direct-impact query
continues to show that self-link. Ignore inferred and manual links. Fail rather
than silently returning an incomplete traversal when node/edge work budgets
are exceeded. Label this a structural FK reachability hint, not a proof of
application dependencies or column-level transitive lineage.

**Reason:** The graph can show plausible multi-hop blast-radius paths without
contacting the source database, but an FK chain alone cannot establish that an
application change propagates along that path. Bounded deterministic output
keeps the CLI/MCP adapters within a clear response budget.

**Revisit when:** Actual-agent tasks measure whether multi-hop paths help, or
when adding non-FK lineage evidence.

## ADR-074 — Add an explicit compact Codex tool profile without narrowing the server

**Status:** Accepted.

**Decision:** Keep all 14 read-only tools implemented and keep the existing
full Codex setup as the default. Add `graphit mcp setup-codex --compact-tools`
to generate Codex's supported `enabled_tools` allowlist for eight core
discovery, table/view, declared-relationship, reviewed-graph, and path tools.
Switch profiles only through explicit `--refresh`; accept only the exact
Graphit-generated allowlist as refreshable and reject edited/custom lists.
Do not alter Claude setup until that client has an equivalent verified filter.

**Reason:** The first valid actual-Codex pair returned 89.8% fewer fact bytes
through Graphit but still used 21.3% more client input tokens. Locally
serialized tool definitions measure 1,777 proxy tokens for all 14 versus 976
for the eight-tool profile. A reversible opt-in profile lets us test fixed
tool-context reduction without deleting capabilities, changing MCP responses,
or breaking existing generated setups based on a single sample.

**Revisit when:** A separately specified larger-schema crossover experiment
or controlled full-versus-compact ablation changes the measured result, or
Codex supports verified deferred MCP loading for this local stdio setup.

**2026-10-02 evidence:** Three compact pairs were exact-correct in all six
arms. Compact used one MCP call and returned 425 fact bytes per run versus two
shell calls and 4,154 bytes for baseline. Compact nevertheless averaged
49,748.7 total / 9,044.7 non-cached input tokens and 17.81 seconds versus
baseline's 46,014.0 / 5,992.7 and 16.99 seconds: +8.1% total input, +50.9%
non-cached input, and +4.8% latency. Pair 1's non-cached advantage did not
replicate. Keep compact opt-in; this small task proves correct use and response
compression, not token or latency savings.

Three separately specified 114-table pairs were exact-correct in all six arms.
Compact Graphit averaged 49,749.7 total / 8,619.0 non-cached input tokens
versus baseline's 59,315.0 / 14,259.0: -16.1% total and -39.6% non-cached,
with both lower in every pair. Mean latency was 4.7% lower, but Graphit was
slower in two pairs and pair 3 baseline read the packet twice. Keep compact
opt-in: the repeated crossover validates this task, not a universal default.

## ADR-075 — Add bounded offline HTML search with a hash-authorized script

**Status:** Accepted.

**Decision:** Keep the same one-hop `GraphProjection` and add client-side
search across table/column/constraint text plus FK, inferred-approved, and
manual-approved relationship filters to the self-contained HTML export. Embed
one fixed script and authorize only its exact SHA-256 through CSP. Escape all
database and review metadata into markup/data attributes; never generate code
from metadata. Keep the complete SVG and accessible lists visible when
JavaScript is disabled. Add no server, browser launch, CDN, Node.js runtime, or
new graph query.

**Reason:** The bounded ERD becomes materially easier to inspect without
changing Graphit's local-first installation or duplicating domain logic. An
exact script hash is narrower than `script-src 'unsafe-inline'`, while the
no-script representation preserves accessibility and reviewability.

**Revisit when:** Multi-hop projection has a measured use case. Expansion must
remain bounded and must not turn the local viewer into a required frontend.

## ADR-076 — Publish through a protected PyPI Trusted Publisher

**Status:** Accepted.

**Decision:** Prepare a dedicated `release.yml` that runs only for a published
GitHub Release, requires its `vVERSION` tag to match `pyproject.toml`, and keeps
build/verification separate from publication. Pin every action and release tool.
Pass only verified wheel/sdist artifacts to a `pypi` environment whose publish
job alone receives `id-token: write`. Use PyPI Trusted Publishing, short-lived
OIDC credentials, and default PEP 740 attestations; do not create a long-lived
PyPI token or publish from a developer machine. Require a human deployment
approval and keep the workflow inert until Onur Parapan registers the exact
pending publisher and protected GitHub environment.

**Reason:** PyPI publication is irreversible for a version and has a different
trust boundary from ordinary CI. Separate jobs prevent build dependencies from
running with publishing authority, full action hashes reduce mutable-action
risk, and the environment approval preserves an explicit human decision at the
only step that can upload. Trusted Publishing avoids copying a reusable secret.

**Outcome:** Release `0.1.0` succeeded through the protected workflow run
`37020399874`. PyPI published the wheel with SHA-256
`f79229a3edbab13dcc37249dddaf967449c9f6de8468adb0cb1145674e0c8eb2` and the
sdist with SHA-256
`6a90adf14f06328f75e4f8b271c20427aa401767fc4653a0ae7c590c1515799b`.
Both files expose GitHub Trusted Publishing attestations for
`OnurParapan/graphit-db`, `release.yml`, and environment `pypi`. A clean install
from public PyPI passed dependency, version, init, SQLite, generated
Codex/Claude launcher, and representative MCP checks.

**Revisit when:** A publisher identity, environment, artifact-transfer boundary,
or release trigger must change.

## ADR-077 — Discover connection URLs without persisting secrets

**Status:** Accepted; discovery and verified source persistence implemented.

**Decision:** During `graphit init`, inspect only the process environment and a
fixed project-root `.env*` allowlist for conventional PostgreSQL URL variable
names. Parse valid URLs into transient candidates whose password field is
excluded from representation. Print only origin, variable name, host, port,
database, username, password presence, and SSL mode. Give the process
environment precedence, collapse duplicate non-secret identities, refuse
symlinked or oversized dotenv files. Request sanitized confirmation unless
`--yes` is explicit, verify through a bounded read-only session, and persist
only the non-secret credential reference after success. `--no-connect` retains
discovery-only behavior. On later use, reread the reference and fail closed if
the URL's non-secret connection identity changed.

**Reason:** One-command onboarding requires Graphit to understand configuration
already present in a project, including URL-contained passwords. A narrow,
deterministic reader avoids executing framework code or recursively harvesting
secrets. Transaction-level read-only enforcement protects a target even when
the project's account itself has broader privileges, while confirm-before-I/O
and persist-after-success keep failed or unwanted candidates out of local state.

**Revisit when:** Split `PG*`/`DB_*`, Docker Compose, framework-specific
discovery, automatic scan orchestration, or broader credential providers are
added.

## ADR-078 — Let init scan all accessible bounded user schemas

**Status:** Accepted and implemented.

**Decision:** Extend the single read-only connection-verification SELECT with an
ordered subquery for namespaces on which the current role has `USAGE`. Exclude
`pg_catalog`, `information_schema`, toast, and temporary schemas. Fail closed if
none or more than 100 are returned. Persist that exact scope on the verified
source, then have init call the shared `scan_source` application service by
default. `--no-scan` stops after source persistence; scan failure retains the
verified source but no successful snapshot.

**Reason:** Defaulting an automatically discovered ERP database to `public`
would silently omit real tables in multi-schema systems. Reusing one bounded
verification result and the existing scanner preserves read-only safety,
avoids CLI-specific graph logic, and prevents a truncated graph from appearing
complete.

**Revisit when:** PostgreSQL installations with more than 100 intentional user
schemas require an explicit paged scope-selection workflow.

## ADR-079 — Generate a complete saved-snapshot ERD after init scanning

**Status:** Accepted and implemented.

**Decision:** After each successful default init scan, project every saved
`TABLE` node (including visibly out-of-scope FK target stubs) and every
`DATABASE/CONFIRMED` table foreign key into a snapshot-named, self-contained
HTML file under `.graphit/exports/`. Do not mix inferred or manual assertions
into this whole-database fact view. Reuse the offline CSP, escaping, accessible
relationship list, and local search behavior. Refuse overwrite and fail without
an artifact above 5,000 nodes or 100,000 FKs instead of silently truncating.
Keep `--no-erd` as an explicit init escape and expose `graphit erd --source`
for regeneration from the latest local snapshot.

**Reason:** The intended first-run experience includes a direct answer to
“which tables are connected?” across the scanned database, while Graphit's
agent context must remain progressive rather than shipping the full schema to
every prompt. A generated local document supplies human ERD value without a
frontend, daemon, Graphviz, network request, or second database read. Showing
only declared FKs in this complete view avoids presenting hypotheses as facts.

**Revisit when:** Measured large-schema use requires hierarchical layout,
schema-specific complete exports, or an explicit reviewed-logical overlay.
Those additions must retain completeness labels and fail-closed limits.

## ADR-080 — Make project-local agent wiring part of default init

**Status:** Accepted and implemented.

**Decision:** Once init has at least one configured source, reuse the existing
safe setup services to add Graphit to project-local Codex and Claude
configuration. Choose Codex's exact eight-tool compact allowlist in the init
path because first-run use prioritizes progressive database navigation and low
tool-definition overhead. Keep standalone Codex setup's historical full-profile
default. `--no-agents` skips both; `--refresh-agents` refreshes only recognized
generated launchers. Preserve unrelated settings, refuse symlinks and edited or
custom Graphit entries, and never write global configuration.

**Reason:** Discovery, scanning, and ERD generation alone do not cause Codex or
Claude to consult Graphit. Project-local automatic wiring completes the normal
first-run path while retaining a visible opt-out and the established conflict
boundary. Compact Codex setup supports the user's token goal, but it is not by
itself a universal token-savings claim.

**Revisit when:** Claude exposes a stable project-level tool allowlist or a
portable launcher eliminates machine-specific interpreter paths.

## ADR-081 - Normalize SQL Server and Oracle through the existing scanner port

**Status:** Accepted and implemented for the 0.2.0 development line.

**Decision:** Register `mssql` and `oracle` beside `postgresql` behind one
verification/scanner dispatcher. Discover URL and SQLAlchemy URL forms without
persisting secrets. Include pyodbc and python-oracledb in the base distribution;
require Microsoft ODBC Driver 18/17 externally for SQL Server and use Oracle
Thin mode by default. SQL Server requests advisory ODBC read-only mode and
fails verification when the principal has direct write permissions. Oracle
starts `SET TRANSACTION READ ONLY` and rejects SYS. Both adapters issue only
fixed, bounded catalog reads and normalize schemas, tables/views, columns,
declared keys/FKs, and safe indexes into the existing `MetadataSnapshot`.
Preserve the historical `postgres:` logical-key namespace while adding
`mssql:` and `oracle:` namespaces. Reuse unchanged SQLite snapshot, ERD, CLI
query, MCP, and agent-setup layers.

**Reason:** Multiple database engines are useful only if they keep Graphit's
compact local graph and one-command onboarding contract. Adapter-specific
stores or MCP tools would fragment agent behavior. SQL Server's ODBC read-only
attribute is advisory, so permission rejection is required rather than claiming
transactional enforcement equivalent to PostgreSQL or Oracle.

**Validation:** Explicitly gated, loopback-only disposable tests now exercise
both real catalog implementations end to end. SQL Server 2022 validates ODBC
connection safety, permission rejection, tables/views, composite PK/FK facts,
a filtered included-column index, snapshot persistence, and local relationship
queries. Oracle Free validates Thin connectivity, the read-only transaction,
separate owner/reader users, cross-schema composite FK facts, tables/views,
indexes, persistence, and relationship queries. The containers were stopped
and auto-removed after the passing runs.

**Revisit when:** CI can carry both service images without unacceptable runtime
or storage cost, or compatibility must be expanded across older versions,
managed services, wallets/integrated authentication, or production-scale
catalogs.
