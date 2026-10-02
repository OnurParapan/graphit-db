# STATUS — Graphit

> Single source of truth for resuming work. Read this first at session start.
> Last updated: 2026-10-02

---

## ✅ Done

- Product direction clarified as a local-first database knowledge/context layer
  for Codex, Claude, and SQL MCPs.
- Previous required FastAPI/Next.js/PostgreSQL-metadata architecture superseded.
- All product and engineering documents rewritten for CLI + SQLite + stdio MCP.
- Uncommitted `backend/` and `frontend/` scaffolds removed.
- Phase 0 package foundation completed:
  - Python 3.11+ `src/` package,
  - Typer `graphit` console entry point,
  - `graphit version`,
  - pytest, Ruff, and strict mypy configuration,
  - two passing CLI contract tests.
- `graphit init` now discovers project roots, writes safe `graphit.toml`, creates
  `.graphit/` with a versioned SQLite store, and updates project Git ignore
  rules without agent/global settings changes. Existing config requires
  `--force`; existing store data is kept.
- SQLite schema v1 now contains sources, scans, snapshots, objects, columns,
  edges, evidence, and review decisions. Migration checksums and schema versions
  are validated; pending migrations run transactionally. Store and init tests
  cover creation, reopening, corruption/unknown schema, data preservation, and
  rollback. Full suite: 22 passed, 1 Windows symlink-permission skip; Ruff and
  mypy pass.
- `graphit source add/list/show` now stores validated PostgreSQL source metadata
  in SQLite, including only a credential environment-variable name. Duplicate
  names and missing projects/stores fail explicitly. No target connection is
  made. Full suite: 41 passed, 1 Windows symlink skip; Ruff and mypy pass.
- `graphit source test` now resolves the credential environment variable only
  when called, opens a Psycopg connection with read-only startup settings and
  connect/statement/lock/idle timeouts, runs one bounded SELECT, verifies the
  transaction is read-only, and emits sanitized error categories. No scan yet.
  Full suite: 52 passed, 1 Windows symlink skip; Ruff, mypy, and pip check pass.
- A typed scanner protocol and PostgreSQL catalog reader now return in-memory
  schemas, ordinary/partitioned tables, and live columns. Selected schema
  names are bound parameters; hard table/column limits fail closed; names keep
  exact spelling. Scans verify read-only mode and use one repeatable-read
  transaction. No SQLite snapshots or CLI scan yet. Full suite: 63 passed,
  1 Windows symlink skip; Ruff and mypy pass.
- PostgreSQL scans now also return declared primary/unique constraints and
  foreign keys, preserving composite column order, validation and partition
  inheritance flags, self-links, and explicitly marked out-of-scope targets.
  Constraint query is bounded and malformed mappings fail closed. No snapshot
  persistence yet. Full suite: 70 passed, 1 Windows symlink skip; Ruff and
  mypy pass.
- `graphit scan --source NAME` now persists each complete metadata scan as an
  immutable, versioned SQLite snapshot. Schemas, tables, columns, declared
  keys, and FK relationships are written in one transaction; local write
  failures roll back the entire new graph, while source-scan failures record a
  FAILED run without changing the latest successful snapshot. Out-of-scope FK
  targets remain explicitly partial stubs. Full suite: 78 passed, 1 Windows
  symlink skip; Ruff and mypy pass.
- `graphit search QUERY --source NAME` now reads only the latest completed
  local snapshot. It case-folds one to eight terms, requires all terms, ranks
  exact/prefix/substring name matches deterministically, returns compact
  schema/table/column details, labels out-of-scope stubs, and offers human or
  JSON output with a hard 100-result cap. Empty/no-snapshot/corrupt-store paths
  are covered. Full suite: 91 passed, 1 Windows symlink skip; Ruff and mypy pass.
- `graphit show TABLE --source NAME` now resolves PostgreSQL-style exact table
  names in the latest completed snapshot, refuses ambiguous bare names, and
  returns bounded columns, declared PK/unique keys, and outgoing FKs with
  ordered pairs, validation, inheritance, and scope labels. Human and JSON
  output share the domain query. New snapshots use `UNIQUE_KEY` edges; queries
  still accept older `UNIQUE` edges. Full suite: 103 passed, 1 Windows symlink
  skip; Ruff and mypy pass.
- `graphit relationships TABLE --source NAME` now returns bounded direct
  incoming/outgoing declared FKs from the latest local snapshot. Self-FKs
  appear in both directions; external target stubs remain partial; inferred
  and pending edges are excluded. Human/JSON output includes ordered column
  pairs, scope, validation, inheritance, and independent truncation flags.
  Full suite: 108 passed, 1 Windows symlink skip; Ruff and mypy pass.
- `graphit path FROM TO --source NAME` now finds one deterministic shortest
  route through confirmed database-declared table FK edges. Steps label forward
  or reverse traversal and preserve true ordered FK column pairs. Default/max
  hops are 4/8, with hard 500-table and 2,000-adjacency budgets; no-path and
  budget exhaustion have distinct error codes. Human/JSON CLI and tests cover
  ties, cycles, self endpoints, external stubs, inferred-edge exclusion, and
  rescan isolation. Full suite: 113 passed, 1 Windows symlink skip; Ruff and
  mypy pass.
- `graphit mcp serve --project PATH` now serves the first two read-only tools,
  `search_objects` and `get_table`, from the latest local snapshot via the
  official Python MCP SDK. Source/limit errors are explicit; output has a
  12,000-character ceiling and a single compact JSON text block (no duplicate
  structured copy). In-process and real stdio client tests pass.
  No source connection or agent configuration change is made. Full suite:
  118 passed, 1 Windows symlink skip; Ruff, mypy, and pip check pass.
- MCP now also exposes `get_relationships` and `find_path` over the existing
  local declared-FK query services. Both preserve confirmed-FK-only facts,
  direction/scope labels, truncation or hop/work budgets, and explicit errors.
  Responses remain single-copy and capped at 12,000 characters; no target
  connection or agent-setting change. Full suite: 121 passed, 1 Windows
  symlink skip; Ruff and mypy pass.
- `database_overview` now gives agents a compact first look at the latest
  completed local snapshot: separate scanned/external schema and table counts,
  scanned columns, confirmed declared table-FK count, bounded schema names,
  and bounded in-scope entry tables ranked by declared FK participation.
  The MCP result remains one capped JSON text block; no source reconnection or
  agent wiring is added. Empty/no-snapshot, inference exclusion, corruption,
  ordering, bounds, and real stdio are covered. Full suite: 125 passed,
  1 Windows symlink skip; Ruff, mypy, and pip check pass.
- `get_relevant_context` now selects a few safe schema/table/column name facts
  from up to eight de-duplicated task terms. It ranks deterministically,
  reports matched/unmatched terms and selection reasons, suggests exact-table
  follow-ups, and honors both object and serialized-response budgets. Rescans
  cannot mix snapshot versions. No LLM, semantic translation, graph expansion,
  live database call, or agent-config change is made. In-process and real stdio
  MCP coverage passes. Full suite: 131 passed, 1 Windows symlink skip; Ruff,
  mypy, and pip check pass.
- `graphit mcp setup-codex` now explicitly adds a project-local Codex MCP
  launcher. It preserves existing config bytes/comments, is idempotent, fails
  on conflicts or invalid TOML, and uses absolute installed-interpreter and
  project paths. It never changes global Codex settings or source credentials.
  Official Codex docs confirm project MCP tables are loaded only for trusted
  projects. Fresh/existing/conflicting config and real MCP subprocess tests
  pass. Full suite: 138 passed, 2 Windows symlink skips; Ruff/mypy/pip check
  pass.
- `graphit mcp setup-claude` now adds only `mcpServers.graphit` to project-root
  `.mcp.json`, retaining other settings and rejecting malformed/duplicate-key
  JSON and conflicts. It reuses the absolute local launcher and warns that
  generated paths are machine-specific before `.mcp.json` is committed.
  Official Claude Code docs confirm project scope and interactive approval;
  a real stdio subprocess test passes. Full suite: 149 passed, 3 Windows
  symlink skips; Ruff, mypy, and pip check pass.
- `graphit candidates --source NAME` now previews narrow inferred relationship
  hypotheses from the latest completed local snapshot. It matches exact
  `<table>_id` to `<table>.id` only for known identical key types and a
  single-column declared target key; existing declared FK pairs are excluded.
  Candidates remain `INFERRED`/`PENDING`, carry inspectable weighted metadata
  evidence and provisional 0.75/0.80 scores, and are never persisted or sent
  through MCP. Hard table/column/pair/output and ambiguity caps fail or skip
  explicitly. No target connection or sampling. Full suite: 155 passed,
  3 Windows symlink skips; Ruff, mypy, and pip check pass.
- A deterministic synthetic ERP challenge set now scores the actual saved-
  snapshot candidate service without changing its rule. Eight undeclared
  positives, four labeled negatives, and a declared-FK control yield 7
  candidates: 4 TP, 3 FP, 4 FN (precision 4/7, recall 4/8). It exposes a
  specific avoidable FP: a declared-FK source column still gets an inferred
  alternative target. Synonyms, non-id unique keys, and self-links are known
  misses. This is a small adversarial fixture, not production accuracy.
  Full suite: 156 passed, 3 Windows symlink skips; Ruff, mypy, and pip check
  pass.
- Metadata candidate rule v2 now suppresses every inferred alternative from
  a source column already covered by a confirmed declared FK. It reads the
  source column pairs from table-level FK metadata, covering simple,
  composite, and out-of-scope target constraints. Unrelated columns remain
  eligible. The fixed challenge set improves from 4 TP/3 FP/4 FN (v1) to
  4 TP/2 FP/4 FN (v2): precision 4/6, recall 4/8. Malformed FK metadata and
  FK work-cap errors fail closed. Candidates remain read-only, PENDING, and
  absent from MCP. Full suite: 158 passed, 3 Windows symlink skips; Ruff,
  mypy, and pip check pass.
- Metadata candidate rule v3 now abstains by default when a source column has
  two or three compatible exact-name target tables. Explicit
  `--include-ambiguous` shows all bounded alternatives for inspection, still
  PENDING; more than three are always skipped. On the unchanged synthetic ERP
  fixture, default output changes from v2's 4 TP/2 FP/4 FN to 3 TP/1 FP/5 FN
  (precision 3/4, recall 3/8). The external-account false positive remains;
  metadata cannot prove identity. Diagnostic mode restores v2's six candidate
  pairs. No persistence, source connection, or MCP exposure. Full suite:
  158 passed, 3 Windows symlink skips; Ruff, mypy, and pip check pass.
- Assessed the remaining external-account false positive: structural metadata
  cannot distinguish an external ID from a local key with the same name/type.
  A new benchmark regression blocks PostgreSQL connections during preview and
  asserts this pair remains only an `INFERRED`/`PENDING` hypothesis. ADR-031
  rules out automatic LIMIT-only/aggregate source-value probes: a row limit
  does not bound database work, and overlap cannot prove business identity.
  Future evidence must be opt-in, scoped, work-bounded, privacy-safe, and live-
  tested. Full suite: 159 passed, 3 Windows symlink skips; Ruff, mypy, and pip
  check pass.
- `graphit review reject SOURCE_COLUMN TARGET_COLUMN --source NAME
  --snapshot-version N` now records one explicit local rejection of a current
  inferred candidate. Reviews use source-prefixed exact quoted column keys,
  persist across rescans, and never suppress a different source alias. A
  SQLite write transaction rejects concurrent snapshot changes; repeated
  rejection is idempotent. Rejected targets are removed before ambiguity
  assessment, so rejecting one of two alternatives reveals the other. No
  source database access, inferred graph edge, or MCP exposure. Full suite:
  165 passed, 3 Windows symlink skips; Ruff, mypy, and pip check pass.
- `graphit review restore` now reverses a source-scoped rejection by appending
  a `RESTORED` decision rather than deleting history. Preview applies each
  pair's latest decision, so a still-eligible pair returns as PENDING; an
  ineligible pair stays absent without retaining a hidden rejection. Repeated
  restore is idempotent, re-rejection is possible, and stale snapshot versions
  or nonexistent decisions fail explicitly. Tests cover source isolation,
  rescans, ambiguity, audit sequence, CLI errors, and no source connection.
  No inferred graph edge or MCP exposure. Full suite: 169 passed, 3 Windows
  symlink skips; Ruff, mypy, and pip check pass.
- `graphit review approve` now records a human-confirmed logical relationship
  for one exact current candidate and snapshot version. Approved pairs remain
  `origin: INFERRED` with unchanged provisional metadata confidence; preview
  labels them `status: APPROVED` and ranks them ahead of pending alternatives.
  In ambiguous cases, default preview shows the approved target while
  `--include-ambiguous` preserves other PENDING alternatives. Decisions are
  source-scoped, idempotent, rescan-aware, and conflict with an un-restored
  rejection. Declared-FK graph queries, MCP, and source databases are
  unchanged. Full suite: 175 passed, 3 Windows symlink skips; Ruff, mypy,
  and pip check pass.
- `graphit review revoke` now withdraws a source-scoped human approval with
  an append-only `REVOKED` event. Preview treats the latest revocation as
  unreviewed: a still-eligible pair returns to PENDING and ambiguity
  abstention applies again. An approval can be revoked even after a rescan
  made the pair ineligible, preventing stale approval from reappearing later.
  Repeated revocation is idempotent; current revoked candidates may be
  approved or rejected again. Tests cover source isolation, stale/missing
  decisions, audit history, ambiguity, no source connection, and no inferred
  edge/MCP mutation. Full suite: 179 passed, 3 Windows symlink skips; Ruff,
  mypy, and pip check pass.
- `graphit graph TABLE --source NAME` now emits a bounded, deterministic
  one-hop JSON table graph from the latest local snapshot. Database-declared
  FKs remain `DATABASE/CONFIRMED`, while currently eligible human-approved
  links remain `INFERRED/APPROVED` with evidence and provisional confidence.
  Pending hypotheses are excluded. Incoming/outgoing FK caps (100 each) and
  approved cap (50) carry explicit truncation flags; an intervening rescan
  fails instead of mixing snapshot versions. No frontend, source connection,
  or export file is required. Full suite: 184 passed, 3 Windows symlink skips;
  Ruff, formatter, mypy, and pip check pass.
- Graph projection now checks bounded, source-scoped latest review events
  adjacent to the selected table before running candidate inference. With no
  current approval, it skips whole-snapshot inference entirely; an FK-only
  neighborhood still works beyond the inference preview's 2,000-table cap.
  With an approval, current candidate eligibility remains mandatory. Exact
  table prefixes, revocation, source isolation, and rescan mismatch are tested.
  Full suite: 187 passed, 3 Windows symlink skips; Ruff, formatter, mypy, and
  pip check pass.
- Approved graph links now validate only bounded approved source columns and
  possible unique `id` targets rather than all snapshot columns/tables. The
  existing candidate matching/ambiguity/scoring loop remains shared. FK
  suppression inspects only relevant source tables. A 2,001-table rescan
  retains a still-eligible approval; removing its target key removes the
  logical link; adding a declared FK shows only DATABASE/CONFIRMED. Targeted
  work overflow fails explicitly. Full suite: 190 passed, 3 Windows symlink
  skips; Ruff, formatter, mypy, and pip check pass.
- `graphit graph TABLE --source NAME --format dot` now renders the same bounded
  one-hop JSON projection as Graphviz DOT text on stdout. It escapes untrusted
  identifiers/labels, uses deterministic node IDs, marks selected/external
  nodes and truncation, and visually distinguishes confirmed FKs from human-
  approved logical links without relying on color alone. PENDING or unknown
  links are rejected; Graphviz is optional, with no browser/server/network
  action. Full suite: 193 passed, 3 Windows symlink skips; Ruff, formatter,
  mypy, and pip check pass.
- `graphit graph TABLE --source NAME --format html` now emits a self-contained,
  script-free offline HTML/SVG graph with an accessible matching table/link
  list, exact column pairs, scope, provenance, evidence, and truncation. A
  deterministic scrollable one-hop layout needs no CDN, Graphviz, server,
  source reconnection, or browser automation. All graph formats now accept
  explicit `--output PATH`, which creates UTF-8 without overwriting existing
  files; stdout remains the default. Full suite: 197 passed, 3 Windows symlink
  skips; Ruff, formatter, mypy, and pip check pass.
- MCP now exposes a separate read-only `get_graph_context` tool for a compact
  one-hop table neighborhood. It reuses `table_graph` with default/hard limits
  8/20 per FK direction and approved-link category, serializes only agent-
  relevant fields, retains exact source/target columns and explicit
  DATABASE/CONFIRMED vs INFERRED/APPROVED provenance, and keeps the 12,000-
  character response ceiling. PENDING and rejected hypotheses stay out;
  `get_relationships` remains FK-only. In-process/real stdio, approval/revoke,
  no-source-connection, truncation, budget, and error tests pass. Full suite:
  199 passed, 3 Windows symlink skips; Ruff, formatter, mypy, pip check pass.
- A fixed five-question ERP-like MCP context benchmark now reuses independent
  relationship labels from the inference challenge. With the correct focus
  table supplied, a compact all-14-table structural baseline is 2,315 chars;
  individual focused graph responses are 596, 816, 250, 236, and 240 chars.
  Labeled link coverage is 2/4 positives, 0/1 negative exposed: a synonym and
  an ambiguous customer target remain missing. These are character counts on
  synthetic metadata, not tokenizer tokens or agent task success. Source
  connections are blocked and the one-step declared FK path is checked. Full
  suite: 200 passed, 3 Windows symlink skips; Ruff, formatter, mypy, pip check
  pass.
- The same five ERP questions now have an oracle-free MCP retrieval benchmark:
  task text enters `get_relevant_context`, and suggested exact table names
  drive `get_graph_context` calls. Labeled focus ranks are 3, 1, 2, 1, 1:
  top-1 is 3/5 and top-3 is 5/5. First suggested graph calls surface only the
  two already-known positives; following all three does not recover the two
  missing relationships. Selector plus first graph call is 2,476-3,223 chars
  per question, above the 2,315-char full-schema fixture baseline in all five
  cases. Four selector outputs report truncation. This is a synthetic character
  measurement, not token savings or agent task success; source connections are
  blocked. Full suite: 201 passed, 3 Windows symlink skips; Ruff, formatter,
  mypy, and pip check pass.
- `get_relevant_context` MCP serialization now omits unavailable per-object
  attributes instead of repeating `null` fields. The domain result keeps its
  typed shape, and the max-character check uses the same compact serializer as
  MCP. On the fixed five questions, selector sizes fall by 144-288 chars to
  2,111/2,119/2,180/2,179/2,092; focus ranks remain 3/1/2/1/1 and known
  link coverage stays 2/4 positives, 0/1 negative. Selector plus first graph
  remains above the 2,315-char whole-fixture baseline in every case. The
  omitted-null wire-shape change is documented in MCP.md and ADR-043. Full
  suite: 201 passed, 3 Windows symlink skips; Ruff, formatter, mypy, pip check
  pass.
- Measured progressive graph follow-ups on the fixed five-question fixture.
  Both surfaced positive links are visible on the first suggested graph call;
  later calls add no labeled link in these cases. An idealized stop once the
  labeled pair appears uses 14,488 response characters across five tasks vs
  16,382 for always reading all three, saving 1,894; actual agent recognition
  is not measured. All three suggestions remain available, and MCP guidance
  now says inspect them in order, continue if task evidence is insufficient,
  and never treat an empty one-hop graph as proof of absence. No graph fact or
  selection behavior changed. Full suite: 201 passed, 3 Windows symlink skips;
  Ruff, formatter, mypy, pip check pass.
- Added a scaled oracle-free retrieval benchmark with 100 unrelated archive
  tables/four columns each, yielding 114 tables and 420 columns. Original five
  relationship labels and approval stay fixed. Focus ranks remain 3/1/2/1/1
  (top-3 5/5), graph hits 2/4 positives and 0/1 negative. Two ID-heavy tasks
  expose the bounded 50-candidate-per-term search cap without losing focus.
  The compact all-table baseline is 24,915 chars; selector plus all three
  graph responses costs 2,591-3,993 chars per task, though these payloads are
  not equal-information substitutes. No product behavior changed. Full suite:
  202 passed, 3 Windows symlink skips; Ruff, formatter, mypy, pip check pass.
- Added an adversarial 174-table retrieval benchmark with 60 unrelated
  `customer_invoice_*` name collisions. Previously, `sales.payment` and
  `sales.invoice` vanished from top-three suggestions and only one of two
  previously surfaced positive links was reachable. A narrow exact object-
  name tie-break after term count, scope, and kind restores five-of-five
  focus top-three coverage and both known links; negative exposure stays zero.
  The current focus ranks are 3/1/2/1/2 (top-1 2/5), so billing focus moved
  behind the exact-name `finance.account` alternative. Four selections report
  per-term candidate truncation. No semantic synonym claim, source query,
  or unbounded search was added. Full suite: 204 passed, 3 Windows symlink
  skips; Ruff, formatter, mypy, pip check pass.
- User accepted Apache-2.0 as the intended release license and confirmed Onur
  Parapan as the copyright holder. Package author metadata plus the exact
  repository/artifact `NOTICE` now carry that attribution.
- `graphit review propose` now records exact cross-name column hypotheses with
  a bounded human reason as local `MANUAL_PROPOSAL` / `PROPOSED` history. It
  validates the latest snapshot, columns, target key, exact type match, and
  declared-FK exclusion; repeats are idempotent and reason changes append.
  No source connection, inferred candidate, graph link, or MCP exposure is
  created. Full suite: 208 passed, 3 Windows symlink skips; Ruff, formatter,
  mypy, and pip check pass. Manual approval is not yet implemented.
- `graphit review proposals --source NAME` now lists newest-updated manual
  proposal pairs with only their latest reason, bounded `--limit`/`--offset`,
  and a current-snapshot structural eligibility label. Missing source/target
  columns, removed target key, changed type, or newly declared FK remain
  visible as stale; no proposal becomes a graph/MCP relationship. A 5,000-event
  history budget and corrupt-history checks fail explicitly. Full suite:
  211 passed, 3 Windows symlink skips; Ruff, formatter, mypy, pip check pass.
- `graphit review approve-proposal` now requires an exact existing manual
  proposal and current snapshot, rechecks structural eligibility under a
  local write transaction, and appends an idempotent `APPROVED` event that
  preserves the latest human reason. Active inferred-review conflicts and
  silent re-proposal over an approval are refused. Manual approval remains
  absent from graph/MCP and never touches the target database. Full suite:
  213 passed, 3 Windows symlink skips; Ruff, formatter, mypy, pip check pass.
- `graphit review revoke-proposal` now appends an idempotent `REVOKED` manual
  event retaining the original reason and approval history. It can withdraw
  an approval after a stale rescan; re-approval requires explicit re-proposal,
  even with unchanged reason. Human proposal listing now renders actual
  PROPOSED/APPROVED/REVOKED status rather than a fixed label. No source
  connection or graph/MCP exposure. Full suite: 215 passed, 3 Windows symlink
  skips; Ruff, formatter, mypy, pip check pass.
- Local `graphit graph` JSON/DOT/offline HTML now includes only eligible,
  latest-approved manual pairs with exact columns, human reason, and distinct
  `MANUAL`/`APPROVED` provenance. Revoked, proposed, or stale pairs remain
  absent; identical inferred-approved pairs are shown once with the manual
  reason. Manual work/output caps and `manual_truncated` prevent silent
  incompleteness. The shared graph service defaults to excluding manual links,
  so MCP output remains unchanged. Full suite: 219 passed, 3 Windows symlink
  skips; Ruff, formatter, mypy, pip check pass.
- MCP `get_graph_context` now opts into only current, eligible manual approvals.
  It emits exact columns, `MANUAL`/`APPROVED` provenance, and bounded
  `human_reason` without representing human assertions as declared FKs or
  confidence scores. `manual_truncated` appears only when that category is
  cut; existing per-category limits and 12,000-character ceiling remain.
  `get_relationships` stays FK-only. In-process/real stdio, revocation,
  stale-rescan, source-isolation, and payload-size tests pass. Full suite:
  221 passed, 3 Windows symlink skips; Ruff, formatter, mypy, pip check pass.
- The five-question synthetic ERP benchmark now measures the same agent-style
  selector-plus-three-graph path before and after one human-approved synonym.
  Labeled positive visibility rises 2/4 to 3/4, the labeled negative remains
  absent, selectors are unchanged, and 15 graph responses together add 1,487
  serialized characters. This does not establish real-agent task success or
  token savings. Full suite: 222 passed, 3 Windows symlink skips; Ruff,
  formatter, mypy, pip check pass.
- PostgreSQL metadata scanning now distinguishes ordinary/partitioned tables,
  views, and materialized views using bounded read-only catalog queries. View
  columns persist in immutable SQLite snapshots and are discoverable with
  their exact kinds through local search and task-context selection. The
  shared relation/column caps fail closed; table-only show/FK/path/graph
  contracts do not claim view lineage. Scanner, rollback, and rescan tests
  pass. Full suite: 226 passed, 3 Windows symlink skips; Ruff, formatter,
  mypy, pip check pass. Live PostgreSQL integration remains outstanding.
- Exact saved view detail is now available through shared `show_view` domain
  logic, CLI `show-view`, and MCP `get_view`. It returns explicit
  VIEW/MATERIALIZED_VIEW kind, bounded ordered catalog columns, and a careful
  `not_null_declared` flag without pretending to know a definition, FK, or
  lineage. Task context suggests the matching view tool. Ambiguity, source
  isolation, rescan disappearance, limit/truncation, and MCP size ceiling are
  tested. Full suite: 228 passed, 3 Windows symlink skips; Ruff, formatter,
  mypy, pip check pass.
- A guarded opt-in integration test now exercises the real PostgreSQL 16
  catalogs with isolated generated schemas and a source-schema read-only
  reader role. It checks tables/views/materialized views, columns, declared
  keys, ordered composite and self FKs, hard caps, schema permission denial,
  snapshot persistence, and local queries. Graphit scanner code issues no
  source DDL/DML; only the fixture admin creates and cleans test objects.
  Local full run with a disposable localhost container: 229 passed,
  3 Windows symlink skips; Ruff, formatter, mypy, pip check pass. Default run
  skips this one opt-in test. The test container was auto-removed.
- Added `.github/workflows/ci.yml` for push/PR/manual runs on Ubuntu with
  Python 3.11–3.14, an ephemeral PostgreSQL 16 service, explicit integration
  opt-in, and pip check/Ruff/format/mypy/full pytest gates. Permissions are
  read-only and checkout credentials are not persisted. Local default suite:
  228 passed, 4 skips (including opt-in PostgreSQL); other gates pass. The
  GitHub workflow was unverified at that point; it later passed remotely in
  run `37011201866` after the installed POSIX launcher fix.
- Added canonical Apache-2.0 LICENSE and package metadata; an explicit source
  archive allowlist excludes local `.wolf`, `.codex`, and `.claude` state.
  Built wheel and sdist, inspected license/entry point/contents, installed the
  wheel with dependencies into a separate virtual environment, and ran
  `graphit version`, help, `graphit init`, and pip check successfully. Local
  suite: 228 passed, 4 skipped; Ruff and mypy pass. No PyPI publication.
- Added a dedicated CI package job (separate from the Python/PostgreSQL matrix)
  to build wheel/sdist, reject private/unexpected archive members, verify
  Apache-2.0 license bytes/metadata and CLI entry point, then install and run
  the wheel in a fresh virtual environment. The checker has five unit tests;
  real local archives and a separate Windows wheel installation passed.
  Full suite: 233 passed, 4 skipped; Ruff, mypy, and pip check pass. No Git
  remote exists, so the new GitHub job has not run remotely; no publication.
- Extended the installed-wheel CI smoke to initialize a disposable Graphit
  project, verify its SQLite schema with a closed read-only connection, and
  launch stdio MCP via the installed interpreter for a bounded client handshake
  and core tool listing. The script refuses a source-tree import in release
  mode. Local clean-wheel and full tests pass: 234 passed, 4 skipped; Ruff,
  mypy, pip check, and rebuilt artifact inspection pass. Remote CI still not run.
- Installed-wheel smoke now also seeds unrelated Codex/Claude project settings,
  runs both project-local setup commands, checks those settings survive, and
  uses each generated command for a bounded MCP tool-list handshake. The real
  installed wheel passed locally; full suite 234 passed, 4 skipped, with Ruff,
  mypy, pip check, and rebuilt archive inspection passing. This does not run
  either agent app, prove cross-machine portability, or verify remote CI.
- Added explicit `--refresh` for stale generated Codex/Claude MCP launch paths.
  Default setup still rejects conflicts; refresh accepts only exact Graphit
  Python-module launch shape with no extra fields. Codex requires its original
  generated stanza, preserving unrelated TOML bytes/comments; Claude keeps
  unrelated JSON settings. Custom/edited entries fail closed. Windows and
  POSIX stale-path tests, idempotency, failure paths, clean installed wheel,
  and archive checks pass. Full suite: 251 passed, 4 skipped; Ruff, mypy, and
  pip check pass. This is manual repair, not portable cross-machine config.
- Added pinned dev-only `tiktoken==0.14.0`/`o200k_base` text-token counts to
  unchanged 14- and 114-table ERP retrieval benchmarks. Small fixture:
  618-token all-schema baseline versus 905/1082/768/821/651 tokens for
  selector plus three graph responses. Scaled fixture: 6418 versus
  905/1082/824/821/660. This is not equal-information or real agent usage;
  source access stays blocked. Full suite: 251 passed, 4 skipped; Ruff,
  formatter, mypy, pip check, rebuilt sdist/wheel archive check pass. First
  encoding use needs a download.
- Added opt-in `max_followups=1..3` to shared task-context selector and MCP;
  default remains three. Ranking, selected objects, and snapshot safety are
  unchanged. On fixed 14-table tasks, selector plus first suggested graph
  costs 641/729/680/557/566 text tokens versus 905/1082/768/821/651 for
  all three; only the same 2/4 positive links are visible. On 114 tables,
  the one-follow-up path costs 641/729/651/557/575 tokens. This is a budget
  trade-off, not a completeness claim. Full suite: 251 passed, 4 skipped;
  Ruff, formatter, mypy, pip check, rebuilt wheel/sdist archive check pass.
- Added `graphit diff --source NAME --from-version OLD --to-version NEW` for
  exact in-scope schema, table/view/materialized-view, and column drift across
  two completed local snapshots. It compares saved column type/position/
  nullability/key flags and table partitioning, excludes external FK stubs,
  never infers renames, and explicitly excludes key/FK definitions and view
  lineage. A single read transaction, 100,000-object-per-snapshot fail-closed
  budget, 500-change output cap, total count/truncation, source/version errors,
  and no source connection are tested. Full suite: 257 passed, 4 skipped;
  Ruff, formatter, mypy, pip check, rebuilt wheel/sdist archive check pass.
- Extended `graphit diff` with declared primary/unique key and table-FK
  definition changes. Exact key kind/column order/inheritance and FK target,
  scope, ordered pairs, validation, and inheritance are compared; missing or
  conflicting FK constraint/edge records fail closed. Legacy `UNIQUE` edges
  remain compatible. JSON `scope` intentionally changes to
  `SCHEMA_RELATION_COLUMN_KEY_FK`; reviews, indexes, and view lineage remain
  excluded. Full suite: 262 passed, 4 skipped; Ruff, formatter, mypy, pip
  check, and rebuilt wheel/sdist archive checks pass.
- Added read-only MCP `compare_snapshots` using the same local diff service as
  CLI. It requires a source and ordered completed versions, preserves exact
  scope/count/truncation, returns one compact JSON text block under the MCP
  ceiling, and never reconnects to PostgreSQL. In-process and real stdio
  tests cover errors, source isolation, output bounds, and service parity;
  full pytest, Ruff, mypy, pip check, and rebuilt wheel/sdist archive checks
  pass.
- Added bounded `graphit snapshots` and read-only MCP `list_snapshots` from
  one source-scoped local service. Completed versions appear newest first with
  UTC completion times, total count, and truncation; known unscanned sources
  return an empty list. CLI/MCP parity, source isolation, non-completed runs,
  invalid limits, real stdio, response ceiling, and no PostgreSQL connection
  are tested. Full pytest, Ruff, formatter, mypy, pip check, and rebuilt
  wheel/sdist archive checks pass.
- Added explicit `offset` pagination to local `graphit diff` and MCP
  `compare_snapshots` through one shared service. A 520-change fixture proves
  access past the first 500, non-overlapping deterministic pages, stable total
  count, and a valid final empty page. Negative/oversized offsets fail safely;
  `truncated` now means more changes after the requested page, and JSON reports
  the offset. CLI/MCP parity and real stdio pass; full pytest, Ruff, formatter,
  mypy, pip check, and rebuilt wheel/sdist archive checks pass.
- Added bounded direct declared-FK impact through `graphit impact TABLE` and
  MCP `get_impact_context`, backed by one local `table_impact` service. It
  separates FK count from distinct referencing-table count, preserves ordered
  pairs/provenance/scope, includes self-FKs, and excludes inferred/manual and
  transitive relationships. Source isolation, ambiguity, truncation, no source
  connection, CLI/MCP parity, real stdio, and response ceiling are tested.
  Full pytest, Ruff, formatter, mypy, pip check, and rebuilt wheel/sdist
  archive checks pass.
- Added exact column-level direct FK impact through `graphit impact-column`
  and MCP `get_column_impact`, reusing saved table FK definitions. Quoted
  dotted identifiers, ambiguous names, composite pair position, self-FKs,
  no-FK columns, source isolation, CLI/MCP parity, real stdio, response ceiling,
  and fail-closed 5,000-incoming-FK work cap are tested. The result never
  claims application/transitive lineage or invents external-stub columns.
  Full pytest, Ruff, formatter, mypy, pip check, and rebuilt wheel/sdist
  archive checks pass.
- Expanded installed-wheel smoke to require all 12 current read-only MCP tools
  and call `get_column_impact` over a tiny local saved FK snapshot via both
  generated Codex/Claude stdio launchers. It retains the 15-second cap and
  settings-preservation checks; no PostgreSQL source connection is needed.
  A freshly built wheel was installed into a clean disposable Windows venv,
  pip check and smoke passed, and that venv was removed. Full pytest, Ruff,
  formatter, mypy, and archive checks pass.
- Audited implementation against MVP and release goals in
  `docs/RELEASE_READINESS.md`. Corrected stale README/roadmap/product claims:
  views and direct impact exist, whereas indexes, transitive/application
  lineage, interactive/deep graph browsing, public installation, and proven
  real-agent token savings do not. The repository is now public and remote CI
  passes; installed-user agent validation and publication remain external
  release gates.
  The full local pytest suite passed with the already-cached tiktoken encoding
  explicitly selected (four skips). An uncached offline run initially failed
  two benchmark tests while fetching that encoding; `docs/TESTING.md` now
  documents the offline cache setting.
- Added the first scanner-only PostgreSQL index slice: a bounded catalog
  SELECT returns typed in-memory index metadata with ordered key/include
  columns, expression placeholders, access method, and unique/primary/
  valid/ready/partial flags. Missing attribute mappings and cap overflow fail
  closed; no predicate/expression SQL is read or stored. Unit tests pass, and
  the opt-in disposable PostgreSQL fixture now includes a composite expression/
  INCLUDE/partial index. SQLite snapshots and all CLI/MCP context still omit
  indexes until a separate persistence/query slice. Full local pytest passes
  with the cached tokenizer (four skips); Ruff, formatter, mypy, and existing
  distribution archive checks pass. Live integration was not run locally.
- Persisted safe index facts transactionally in new SQLite snapshots without
  a schema migration: each `INDEX` object belongs to an in-scope table or
  materialized view, stores ordered key/include names and flags, and links
  actual columns through positioned `INDEX_KEY`/`INDEX_INCLUDE` edges.
  Expression positions remain null, with no invented column or source SQL.
  Tests cover rescan/history isolation, invalid parent/column rollback,
  mid-write SQLite failure rollback, source-scan failure, and unchanged default
  search. The opt-in live PostgreSQL fixture now asserts saved index presence,
  but was skipped locally. Full pytest passes with cached tokenizer (four
  skips); Ruff, formatter, mypy, pip check, and freshly rebuilt wheel/sdist
  archive checks pass. An initial sandboxed isolated build failed while
  installing the backend; a scoped elevated rebuild succeeded.
- Added explicit `graphit indexes RELATION --source NAME` over a shared local
  `list_table_indexes` service. Exact table/materialized-view resolution uses
  PostgreSQL quoting rules; pages default to 20 and cap at 100 with bounded
  offset, total count, and truncation. Saved index metadata must agree with
  parent and positioned key/include edges; corruption fails closed. JSON and
  human output include safe flags and expression placeholders, never raw
  predicate/expression SQL. Default search/show/overview and 12 MCP tools
  remain index-free. Tests cover old snapshots, rescans, source isolation,
  ambiguity, bounds, corrupted edges, CLI output, and no PostgreSQL reconnect.
  Full pytest passes with cached tokenizer (four skips); Ruff, formatter,
  mypy, pip check, and freshly rebuilt wheel/sdist archive checks pass.

- Added opt-in read-only MCP `get_index_context` over the same validated
  `list_table_indexes` service. It defaults to 5 indexes, caps at 20, supports
  bounded offset, and preserves exact relation, snapshot/version, count,
  truncation, ordered key/INCLUDE positions, and expression placeholders.
  Existing overview/table responses stay index-free; all 13 tools retain
  read-only annotations and one JSON text block under the 12,000-character
  ceiling. In-process and real stdio tests cover parity, pagination, ambiguity,
  invalid/missing relations, corruption, oversized response, and no target
  reconnection. Full pytest passed with four skips; Ruff, formatter, mypy,
  pip check, fresh wheel/sdist inspection, and a newly installed wheel smoke
  through both generated Codex and Claude stdio launchers passed. The first
  sandboxed isolated build failed while bootstrapping Hatchling; a scoped
  elevated rebuild succeeded. No actual Codex/Claude application run or live
  PostgreSQL integration run was performed.

- Ran the opt-in live PostgreSQL 16 fixture in a newly created, localhost-only,
  auto-remove disposable container. The scanner read a composite index with
  expression key, `INCLUDE` column, and partial predicate flag; the immutable
  SQLite snapshot, `list_table_indexes`, `graphit indexes --json`, and MCP
  `get_index_context` agreed on the saved ordered facts. The integration test
  was extended with CLI/MCP parity assertions. The full suite passed with
  the disposable server enabled and cached tokenizer selected (three skips);
  Ruff lint/format and strict mypy passed. The test container was stopped,
  auto-removed, and verified absent. This is one PostgreSQL 16 fixture, not
  broad version/scale or actual agent-task validation.

- Audited local agent availability without a model call: a Codex executable
  bundled with the VS Code extension reports `codex-cli 0.154.0-alpha.6.2`
  (with a nonfatal home-alias warning); no `claude` command is on `PATH`.
  Added `docs/AGENT_EVALUATION.md` for a controlled exact-FK task on the
  synthetic ERP fixture, independent answer key, two equal-fact arms,
  correctness/latency/token accounting, stop rules, and a separate future
  Graphit-before-SQL-MCP experiment. The existing 618-token all-table dump
  omits FK name/provenance and is explicitly disallowed as a fair baseline.
  Release/testing docs now point to the protocol. No actual Codex/Claude
  task, paid model call, or user/global agent config change occurred.

- Added development-only `python -m scripts.prepare_agent_eval OUTPUT` to
  seed the existing 14-table synthetic ERP fixture in `graphit-project/` and
  write a deterministic, complete `baseline-arm/baseline.json` from the same
  saved snapshot. The packet carries every table/column/key, exact confirmed
  FK name/ordered pair/scope/flags/provenance, source/version/counts, and no
  separate answer-key file. It checks fixture counts, every local query's
  truncation/version/scope, table-vs-relationship parity, and the independent
  expected FK; existing output paths are never overwritten. Focused tests
  verify stable bytes, CLI entry, missing FK/truncated rejection, no source
  connection, and overwrite refusal. Full offline suite passed with four
  environment-dependent skips; Ruff and strict mypy pass. No agent/model
  call, global setting change, or source database access occurred.

- Added bounded, deterministic transitive declared-FK table impact via
  `graphit impact-tree` and read-only MCP `get_transitive_impact` (14 tools).
  It walks incoming confirmed database FKs only, reports shortest per-table
  paths, labels structural reachability rather than application impact,
  excludes self-root/inferred links, and fails closed on 500-node/2,000-edge
  work budgets. Focused CLI/MCP/stdio/ceiling and installed-launcher smoke
  checks pass; full offline suite passes with four environment skips, Ruff,
  strict mypy, and pip check pass. Fresh wheel/sdist archive inspection and
  clean-wheel Codex/Claude launcher smoke also pass; disposable build/smoke
  artifacts were removed. No source connection or model call.

- Added explicit `graphit mcp setup-codex --compact-tools` without changing
  the 14-tool server or existing full-profile default. It writes Codex's
  official `enabled_tools` allowlist for eight core discovery, table/view,
  relationship, graph, and path tools. Switching full↔compact requires
  `--refresh`; only the exact generated allowlist is owned, while custom lists
  fail closed. Local schema serialization measures 7,765 bytes / 1,777 pinned
  proxy tokens for all tools versus 4,215 bytes / 976 for compact. Focused
  Codex setup tests pass (17 passed, 1 platform skip); the full offline suite
  passes with four skips, and Ruff, formatter, strict mypy, and pip check pass.
  Fresh wheel/sdist build and an installed-wheel init plus Codex/Claude MCP
  smoke also pass with exact compact allowlist verification. Three matched
  compact-profile pairs are exact-correct in all six arms. Graphit always used
  one MCP call and returned 425 fact bytes; baseline always used two shell
  calls and read 4,154 fact bytes. Compact nevertheless averaged 49,748.7
  total / 9,044.7 non-cached input tokens and 17.81 seconds versus baseline's
  46,014.0 / 5,992.7 and 16.99 seconds: +8.1% total, +50.9% non-cached, and
  +4.8% latency. Keep compact opt-in; no savings claim. The two
  archive directories were removed; a
  partially cleaned wheel target remains under `.venv/releasecheck-compact-target`
  because its generated `bin` directory has an unreadable Windows ACL.

- Upgraded the bounded one-hop offline HTML ERD with local search across table,
  column, and constraint text plus relationship filters for database FKs,
  inferred approvals, and manual approvals. It remains a single self-contained
  file with no server/CDN/Node dependency. One fixed inline script is authorized
  by its exact CSP SHA-256; untrusted metadata stays escaped in markup/data
  attributes, and the complete SVG/lists remain available without JavaScript.
  Focused HTML/manual tests pass (10); the full offline suite passes with four
  environment skips, and Ruff, formatting, strict mypy, and pip check pass.
  Multi-hop depth and expand/collapse remain out of scope.
- Prepared a protected PyPI Trusted Publishing workflow at `release.yml`.
  It runs only for a published GitHub Release, requires an exact `vVERSION`
  tag, pins all actions and build/Twine tools, verifies and smoke-tests artifacts
  in a build job, and gives only a separate `pypi` publish job OIDC permission.
  The first protected publication subsequently completed as described below.
- Published `graphit-db 0.1.0` on PyPI from immutable tag `v0.1.0` at commit
  `33cef8b16c7ae9de9adb0d07baba81a2776ca5f5`. GitHub release workflow run
  `37020399874` passed build/archive/installed smoke, paused for the protected
  `pypi` environment, received explicit human approval, and uploaded through
  OIDC Trusted Publishing. PyPI exposes attestations for both artifacts. The
  wheel SHA-256 is `f79229a3edbab13dcc37249dddaf967449c9f6de8468adb0cb1145674e0c8eb2`;
  the sdist SHA-256 is
  `6a90adf14f06328f75e4f8b271c20427aa401767fc4653a0ae7c590c1515799b`.
  A separate clean public-PyPI venv passed dependency, version, init, SQLite,
  generated Codex/Claude launcher, and representative MCP checks. pipx/uv were
  unavailable locally and remain external wrapper-observation gaps. Post-release
  documentation and staged smoke diagnostics pass the full local suite (318
  passed, 5 skipped), Ruff, formatting, strict mypy, and pip check.

---

## 🚀 Next phase

**Goal:** Treat the local MVP, controlled Codex context-delivery evaluation,
copyright-attribution package gate, public GitHub repository, remote CI, and
first protected PyPI release as complete. Preserve the small/large crossover
boundary honestly. Next evidence should come from real installed-user feedback,
Claude validation, broader PostgreSQL versions/scale, or an upgrade from 0.1.0;
do not make more model calls or publish another version without fresh explicit
authority.

**2026-10-02 scaled preparation:** Added `scripts.prepare_agent_eval --scaled`
to build a deterministic 114-table / 420-column equal-facts bundle with 100
unrelated archive tables, the unchanged exact-FK task, and the single declared
FK. Tests cover direct and CLI generation, stable bytes, counts, and existing
failure paths. The full offline suite passes with four environment skips;
Ruff, formatting, strict mypy, and pip check pass. The canonical packet is
51,256 UTF-8 bytes / 12,651 local `o200k_base` proxy tokens with SHA-256
`af7d8d09825c4ec85e4e2be4cf9d3d61b8debafdb9ea9d7f21496edd065d525c`.
No model call occurred. Any scaled run still needs fresh explicit approval.

**2026-10-02 first scaled pair:** The user explicitly approved exactly two
calls. Fresh 114-table compact and baseline arms passed hash, FK, eight-tool
allowlist, own-readable, and sibling-invisible preflights. Both were
exact-correct with zero-byte stderr. Compact Graphit used one
`get_relationships` call, 51,139 total / 6,083 non-cached input tokens, 174
output tokens, and 16.25 seconds. Baseline used two shell calls, read the full
51,256-byte packet, used 53,635 total / 14,595 non-cached input tokens, 182
output tokens, and took 15.59 seconds. Graphit was 4.7% lower in total input and
58.3% lower in non-cached input, but 4.2% slower. Both authorized calls are
consumed. Raw evidence is under
`docs/evidence/agent-eval-scaled-2026-10-02/`. This is the first crossover
direction, not an aggregate or general savings claim.

**2026-10-02 scaled repeats:** The user explicitly approved four additional
calls. Two fresh pairs alternated baseline→compact then compact→baseline and
passed packet/prompt hashes, exact eight-tool allowlists, FK preflights, and
two-way sibling isolation. All four arms were exact-correct with zero-byte
stderr. Together with pair 1, both arms are 3/3. Compact total input averaged
49,749.7 versus 59,315.0 (-16.1%) and non-cached input averaged 8,619.0 versus
14,259.0 (-39.6%); both were lower in all three pairs. Mean latency was 16.54
versus 17.34 seconds (-4.7%), but Graphit was slower in two pairs and pair 3
baseline read the packet twice, so no general speed claim. All four authorized
calls are consumed. Raw repeats and aggregate are under
`docs/evidence/agent-eval-scaled-repeats-2026-10-02/`. This establishes a
repeat-supported token crossover only for the exact 114-table synthetic task.

**2026-09-17 follow-up:** Model-free `unelevated` sandbox echo worked outside
the enclosing workspace sandbox, but the required deny-other-files profile
was rejected: `Restricted read-only access requires the elevated Windows
sandbox backend`. Ubuntu WSL exists but has no Codex CLI or `bwrap`. No
external installation, Windows admin setup, credential transfer, or model
call was attempted. Continue independent local product/release slices while
strict agent-eval isolation is unavailable.

**2026-10-02 follow-up:** User approved the WSL route. Installed the official
standalone Codex CLI 0.160.0 in Ubuntu WSL2 and Ubuntu `bubblewrap` 0.9.0; no
Windows credential was transferred. A temporary named permission profile can
read its own synthetic workspace and cannot see a sibling marker, so strict
filesystem isolation is now established. Device-code login completed. The
baseline arm answered correctly after one JSON read, reporting 30,474 input
tokens (26,624 cached), 137 output tokens, 0 reasoning-output tokens, and
16.49 seconds, but PowerShell backticks corrupted its prompt, so the sample is
protocol-invalid. The Graphit arm received the corrected prompt but its output
overflowed capture and it ended without the requested final file; no durable
transcript or usage summary is available. This is a retained failed sample,
not a Graphit success or a token comparison. The one-per-arm authorization is
consumed; do not rerun either model arm without fresh explicit approval.

**2026-10-02 corrected pair:** The user explicitly approved one replacement
per arm. New isolated workspaces passed own-readable/sibling-invisible checks;
the equal-facts packet retained SHA-256
`f4de55401ea7d4190cafc535f7c32e2bfe55f315de4a8dd9bafef584991f8260`.
Prompts were passed by stdin and JSONL was captured to files. Both arms passed
the complete exact-FK rubric. Graphit made one `get_relationships` call,
received 425 fact bytes, took 17.54 seconds, and reported 54,957 input tokens
(46,976 cached), 177 output, 0 reasoning-output. Baseline made two shell
calls, read the complete 4,154-byte packet, took 17.03 seconds, and reported
45,314 input tokens (41,216 cached), 182 output, 0 reasoning-output. Graphit
therefore returned 89.8% fewer fact bytes but used 9,643 more input tokens
(21.3%) and was 0.51 seconds slower (3.0%). This one pair proves successful
Graphit tool use, not savings. Raw synthetic evidence is under
`docs/evidence/agent-eval-2026-10-02/`. The CLI JSONL did not emit the resolved
server-side default-model identifier.

**2026-10-02 compact pair:** After separate explicit approval, new isolated
arms reused the exact prompt and packet hashes and reversed order (baseline
first). Codex accepted the exact eight-tool allowlist. Both arms passed the
rubric. Compact Graphit made one `get_relationships` call, returned 425 fact
bytes, took 19.24 seconds, and reported 49,056 input tokens (43,520 cached),
184 output, 0 reasoning-output. Baseline made two shell calls, read 4,154 fact
bytes, took 16.36 seconds, and reported 47,401 input tokens (39,808 cached),
175 output, 0 reasoning-output. Compact's non-cached input was lower (5,536
versus 7,593), but total was 1,655 higher and latency 2.88 seconds longer.
Raw synthetic evidence is under
`docs/evidence/agent-eval-compact-2026-10-02/`. Both approved calls are consumed.

**2026-10-02 compact repeats:** The user explicitly approved four additional
calls. Two fresh isolated pairs alternated order and reused the exact prompt,
packet hash, rubric, and eight-tool allowlist. All four arms were exact-correct
with zero-byte stderr logs. Together with the first pair, compact and baseline
are each 3/3. Compact always used one `get_relationships` call; baseline always
used two shell calls. Compact mean total input was 49,748.7 versus 46,014.0
(+8.1%), mean non-cached input was 9,044.7 versus 5,992.7 (+50.9%), and mean
latency was 17.81 versus 16.99 seconds (+4.8%). Compact total input was higher
in every pair. The protocol minimum for this compact 14-table task is met and
all four newly authorized calls are consumed. Raw repeats and aggregate are
under `docs/evidence/agent-eval-compact-repeats-2026-10-02/`. This proves
correctness and smaller returned facts, not savings.

**2026-09-17 attempt:** User approved one pilot per arm. A fresh synthetic
bundle was prepared in system temp (baseline SHA-256
`f4de55401ea7d4190cafc535f7c32e2bfe55f315de4a8dd9bafef584991f8260`).
Model-free native-Windows `codex sandbox` probes with custom workspace-only
and built-in `:read-only` profiles did not complete and were interrupted.
No model call, trust change, measured comparison, or source-database access
occurred. Establish isolation in a working environment before either arm.

### Acceptance criteria

1. One valid full-profile pair, three valid small compact-profile pairs, and
   three valid scaled compact-profile pairs are complete; all authorized calls
   are consumed. Obtain fresh explicit authority for every additional model
   run. Claude CLI is absent locally.
2. Preserve the verified isolation, stdin prompt transport, file-backed JSONL
   capture, packet hash, and exact rubric for every repeat.
3. Keep the full profile default and compact explicit. The repeated 14-table
   result is negative while the 114-table result has lower token use in every
   pair; no universal table-count threshold follows from two fixture sizes.
4. The scaled protocol minimum is complete. Keep its result separate from the
   small fixture and make no billing, production, latency, SQL-MCP, or Claude
   claim beyond the measured synthetic task.

### Expected files

| Type | File | Purpose |
|---|---|---|
| inspect | Full and compact raw evidence | Honest cross-pair diagnosis |
| inspect | Scaled raw evidence and aggregate | Repeat-supported task-specific crossover |
| external | Installed-user Claude and publication workflow | Remaining public release evidence |

### Closed decisions

- Default knowledge store: project-local SQLite at `.graphit/graphit.db`.
- Product surfaces: Typer CLI and stdio MCP.
- Source adapter first: PostgreSQL.
- Visualization: optional local JSON/DOT/self-contained HTML export.
- Python support: 3.11+.
- No required server, frontend, Docker, external metadata DB, or LLM.
- Graphit provides context and never generates/executes application SQL.
- Public distribution name is `graphit-db`; product, import, and CLI remain
  `Graphit` / `graphit` / `graphit`.

### Open decisions

- The `graphit-db` distribution name is now owned through the successful
  `0.1.0` PyPI publication; future versioning and compatibility policy remain
  to be shaped by installed-user feedback.
- Claude and Codex project MCP formats were verified against current official
  documentation; portable multi-machine launcher design remains open.
- Copyright holder and package author are confirmed as Onur Parapan; exact
  `NOTICE` and Apache-2.0 `LICENSE` files are included and checked in artifacts.

---

## 📁 Active architecture

- **Runtime:** Python 3.11+, Typer, stdlib SQLite, Psycopg PostgreSQL adapter,
  official Python MCP SDK 2.x.
- **Current modules:** `graphit.cli`, `graphit.project`, `graphit.store`,
  `graphit.sources`, `graphit.snapshots`, `graphit.queries`, `graphit.scanners.protocol`,
  `graphit.scanners.postgresql`, `graphit.mcp_server`, `graphit.codex`,
  `graphit.claude`, `graphit.mcp_launcher`, `graphit.inference`,
  `graphit.review`, `graphit.snapshot_diff`, `graphit.graph_export`, `graphit.graph_dot`,
  `graphit.graph_html`, package version.
- **Patterns:** local-first, dependency-inward boundaries, immutable snapshots,
  read-only source access, deterministic inference, progressive bounded context.
- **Generated state:** `graphit init` creates `.graphit/graphit.db` (schema v1).
  Local state is ignored by Git and OpenWolf anatomy.

---

## ⚠️ External notes

- Windows PowerShell blocks some `.ps1` shims; use `.exe`/`.cmd` launchers.
- Sandbox package downloads require scoped network approval.
- Docker daemon is not required until opt-in PostgreSQL integration tests.

---

## 🔧 Useful commands

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m mypy
.\.venv\Scripts\graphit.exe version
openwolf.cmd scan
```

---

## 📚 References

- `AGENTS.md` — non-negotiable product and coding constraints
- `docs/ARCHITECTURE.md` — local-first technical boundaries
- `docs/ROADMAP.md` — ordered vertical slices
- `docs/DECISIONS.md` — accepted and superseded decisions
- `.wolf/cerebrum.md` — user preferences and learned constraints
- `.wolf/buglog.json` — environment/tooling problems and fixes
