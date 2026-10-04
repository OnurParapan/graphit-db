# Cerebrum

> OpenWolf's learning memory. Updated automatically as the AI learns from interactions.
> Do not edit manually unless correcting an error.
> Last updated: 2026-10-04

## User Preferences

<!-- How the user likes things done. Code style, tools, patterns, communication. -->

- Explain the intended change in detail before implementing it.
- Progress through small, coherent micro-steps and report each result clearly.
- Explicitly tell the user when the overall Graphit product goal is genuinely
  complete; do not call a completed micro-step a finished product.
- The user has granted implementation freedom within the agreed Graphit vision;
  preserve the micro-step workflow instead of batching entire roadmap phases.
- User accepted Apache-2.0 as the intended open-source release license and
  confirmed Onur Parapan as the copyright holder. Package author metadata and
  the repository/artifact `NOTICE` carry that exact attribution.
- The intended product is a widely installable local intermediary for Codex,
  Claude, and SQL MCPs, similar in adoption style to developer CLI tools—not a
  hosted database platform or mandatory web application.
- Prioritize fewer tokens, faster agent discovery, fewer irrelevant tables and
  columns, accurate relationship paths, and minimal installation friction.
- Do not require users to provision a separate read-only database account when
  Graphit itself executes only fixed metadata catalog queries. Prefer such an
  account, but accept privileged credentials with a visible warning.
- An ERD-like graph is valuable as an optional local export, not as the primary
  product surface.
- Whole-database ERDs should still be visually useful: render table columns and
  connect confirmed FK lines to the exact related source/target column rows.
- The intended zero-configuration experience is stronger than the 0.1.0 flow:
  running `graphit init` in an existing project should discover its database
  configuration, connect safely, scan structure and relationships, generate a
  whole-database ERP/ERD view, and wire compact Graphit context into Codex and
  Claude. Separate source/setup/one-hop commands may remain advanced controls,
  but must not be the ordinary first-run path.

## Key Learnings

- **Project:** Graphit
- Real monorepos may keep database URLs in bounded paths such as `backend/.env`
  and use SQLAlchemy async schemes (`postgresql+asyncpg`, `mssql+aioodbc`) or
  Oracle `service_name` query parameters; safe zero-config discovery must cover
  these without recursively reading dependency/state directories.
- SQL Server ODBC read-only mode is advisory, but this does not require blocking
  `sa` or another write-capable principal: Graphit's enforceable boundary is its
  fixed, bounded catalog-SELECT-only implementation plus rollback. Detect broad
  permission and surface `PRIVILEGED_CREDENTIAL` without preventing the scan.
- Oracle schema discovery must use `ALL_USERS.ORACLE_MAINTAINED='N'`, retain the
  current session user even when its schema is empty, and include other owners
  only when structural objects are visible. A hard-coded system-owner list over
  `ALL_OBJECTS` both leaked maintenance schemas and omitted new app users.
- Init database identity is engine/host/port/database/user, independent of the
  credential variable or dotenv file that exposed it. Multi-database init must
  keep processing after per-source failures, preserve completed artifacts, and
  return a final non-zero partial-failure result so automation remains honest.
- Product core: persistent, queryable database knowledge graph exposed to humans
  and AI coding agents through compact, progressive context.
- Current architecture: Python 3.11+ Typer CLI, project-local SQLite, PostgreSQL
  source adapter first, stdio MCP, and optional local graph exports.
- The 0.2.0 development architecture registers PostgreSQL, SQL Server, and
  Oracle behind one verification/scanner dispatcher. Downstream snapshots, ERD,
  queries, and MCP stay engine-neutral. PostgreSQL must retain its historical
  `postgres:` logical-key namespace; SQL Server and Oracle use `mssql:` and
  `oracle:` so review decisions remain stable and source-scoped.
- SQL Server's ODBC read-only mode is advisory, so Graphit warns on principals
  with effective direct database/schema/object write grants and continues using
  only fixed bounded catalog SELECTs. Oracle starts `SET TRANSACTION READ ONLY`,
  rejects SYS, and uses python-oracledb Thin mode. Explicitly gated disposable
  SQL Server 2022 and Oracle Free runs passed the full scanner-to-snapshot-to-
  relationship path; this is one-image compatibility evidence, not universal
  version or production-scale coverage.
- Release 0.1.0 contains the underlying scanner, graph, and agent-integration
  pieces, but its `init` intentionally avoids database connections, its MCP
  setup is separate, and its graph export is focus-table/one-hop. That is an
  implementation milestone, not fulfillment of the intended one-command
  onboarding and whole-database ERP/ERD product contract.
- The post-0.1.0 init path now creates a complete saved-snapshot HTML ERD after
  scanning. It includes every table node, visibly scoped external FK stubs, and
  every confirmed database FK with exact ordered column pairs. It refuses to
  truncate beyond 5,000 nodes/100,000 links; agent context remains progressive.
- The intended default init chain is now implemented on main: source discovery,
  transient URL-secret resolution, confirmed read-only verification, all-user-
  schema scan, complete local ERD, compact project Codex MCP, and project Claude
  MCP. This is unreleased post-0.1.0 behavior until a separately authorized tag.
- The user explicitly authorized publishing the completed onboarding work as
  the next compatible patch release. Version 0.1.1 is available on PyPI and its
  local isolated wheel/sdist plus installed MCP smoke passed before tagging.
- `graphit-db 0.1.1` is now public from immutable tag `v0.1.1` at commit
  `875967c6a9f9e6409b34981288a958fb27af1923`. Protected run `37034365431`
  succeeded; public-PyPI clean install and both generated MCP launchers pass.
- Repository documentation lives under `docs/`, except `README.md` and
  `AGENTS.md`.
- Hatchling's default source archive selection can include local OpenWolf and
  agent configuration; the explicit sdist allowlist in `pyproject.toml` keeps
  public artifacts limited to source, tests, docs, and required metadata.
- The package CI gate builds artifacts in a separate job, verifies their exact
  license/CLI metadata and private-file exclusions, and smoke-installs the
  wheel; it does not publish. A remote run is still unavailable without a Git
  remote.
- Installed-wheel smoke now checks project-local SQLite initialization and
  stdio MCP tool-list handshake through the installed interpreter, with a
  15-second cap and no source database access.
- Installed-wheel smoke also runs project-local Codex and Claude setup,
  preserves unrelated settings, and handshakes through both generated commands.
  Codex officially reads project `.codex/config.toml` only for trusted projects;
  this smoke tests the generated stdio command, not actual app trust/approval.
- Installed-wheel smoke now seeds a tiny FK snapshot with the installed package,
  asserts the complete public read-only MCP tool set, and checks an exact
  `get_column_impact` payload through each generated stdio launcher. This
  catches packaging or launcher drift that a three-tool handshake missed,
  without starting PostgreSQL or either agent application.
- MVP/release readiness is tracked separately in `docs/RELEASE_READINESS.md`:
  a working local CLI/MCP slice is not public availability or measured real-agent
  token savings. README's prior planned list was stale about view and direct
  impact support; index metadata, transitive/application lineage, and actual
  Codex/Claude task validation remain gaps. The public repository is
  `https://github.com/OnurParapan/graphit-db`; GitHub Actions run `37011201866`
  passed Python 3.11–3.14 plus the isolated distribution/MCP smoke job.
- Offline full pytest requires selecting the already-cached tiktoken encoding
  with `TIKTOKEN_CACHE_DIR=.cache/tiktoken` (resolved absolute path on Windows).
  Otherwise two benchmark tests try a blocked first-time network download;
  Graphit behavior is not the failing part.
- PostgreSQL 16 `pg_index.indkey` is an ordered `int2vector`: the first
  `indnkeyatts` positions are keys, later positions are INCLUDE-only, and a
  zero is an expression key rather than a table column. The new scanner keeps
  both attribute numbers and resolved names to fail closed on missing catalog
  mappings, while never retrieving `indexprs`/`indpred` text. The later
  snapshot slice now saves these safe `MetadataSnapshot.indexes` facts.
- The v1 SQLite `objects`/`edges` tables accept new text kinds and JSON
  metadata without migration. Index persistence uses `INDEX` objects,
  `CONTAINS`, and positioned `INDEX_KEY`/`INDEX_INCLUDE` edges; expression
  slots remain null in object metadata and never get a column edge. Existing
  query services explicitly filter object kinds. `list_table_indexes` is now
  an opt-in bounded query that validates saved metadata against positioned
  edges; default search, show, overview, and MCP context remain index-free.
- The opt-in MCP `get_index_context` reuses `list_table_indexes` with a smaller
  5-default/20-max page and existing 12,000-character one-text-block ceiling.
  The packaged smoke now checks all 13 read-only tools plus saved FK and index
  facts through both generated stdio launchers; this is not actual agent use.
- The disposable localhost PostgreSQL 16 integration fixture now tests the
  full index path through live catalog scan, immutable SQLite snapshot,
  `list_table_indexes`, CLI JSON, and MCP JSON. The fixture's composite
  expression/`INCLUDE`/partial index retained ordered key/include positions;
  this validates one PG version/fixture, not production scale or real agents.
- For controlled agent evaluation, the installed Codex executable is bundled
  in the VS Code extension (`codex-cli 0.154.0-alpha.6.2`), while no Claude
  executable is on `PATH`. The existing 14-table structural baseline omits
  FK name/provenance, so its 618 text-token count cannot support an
  equal-information Graphit-vs-baseline claim. The first fair task uses the
  fixture's declared payment→CRM customer FK and a negative legacy-FK check.
- The offline agent-evaluation builder lives under `scripts/`, imports the
  development fixture from `tests/`, and is run with `python -m` from the repo
  root. It seeds one project and emits a sorted, byte-stable all-facts JSON
  packet in a sibling arm directory; no answer-key file or model call is made.
  It validates current snapshot counts, untruncated table/relationship reads,
  expected fixture columns/keys/FK flags, and refuses existing output paths.
- The first user-approved Codex pilot stopped before model use: a fresh
  synthetic bundle was prepared, but native-Windows `codex sandbox` probes
  using both a custom workspace-only profile and built-in `:read-only` did not
  complete. Do not assume that `--sandbox read-only` alone prevents an agent
  from reading repository or sibling-arm files; verify isolation with a
  model-free command before running the comparison.
- On this machine, model-free Codex `unelevated` echo succeeds only outside
  the enclosing workspace sandbox, but a deny-other-files permission profile
  is refused because restricted read-only access requires the elevated Windows
  backend. Existing Ubuntu WSL has no Codex CLI or `bwrap`. Do not silently
  weaken the two-arm evaluation's isolation or transfer authentication.
- After explicit approval on 2026-10-02, Ubuntu WSL2 has authenticated
  standalone Codex CLI 0.160.0 and `bubblewrap` 0.9.0. A temporary named
  profile that denies root reads needs an exact read grant for Codex's
  standalone release directory; it can read its own workspace but a sibling
  marker is invisible. No Windows credential was copied.
- Never place Markdown backticks in a prompt embedded in a PowerShell command:
  PowerShell treats them as escapes and the downstream Bash layer may execute
  the quoted identifier. Prefer plain identifiers or a purpose-built prompt
  file/stdin path, checksum it, and verify the exact received prompt before
  scoring a run. Redirect Codex `--json` stdout/stderr to arm-local files so
  long event streams do not overflow the enclosing tool capture.
- The first authenticated pair is not comparable. The baseline answer was
  correct but its prompt was shell-corrupted; the Graphit run had a corrected
  prompt but ended without its requested final file after capture overflow.
  Retain both as invalid/failed evidence, do not invent missing metrics, and
  obtain explicit approval before any replacement model call.
- The first valid replacement pair on 2026-10-02 passed the full rubric in
  both arms. Graphit used one `get_relationships` call and returned 425 fact
  bytes versus the 4,154-byte complete baseline, yet client input was
  54,957 versus 45,314 and latency 17.54 versus 17.03 seconds. This is evidence
  of correct agent use but no saving on the 14-table fixture. The fixed schema
  cost of 14 exposed MCP tools is a plausible cause to isolate, not a proven
  diagnosis. Two further pairs remain before the protocol minimum.
- Codex officially supports `mcp_servers.<id>.enabled_tools`. Graphit keeps all
  14 server tools and the full generated setup as default, while explicit
  `setup-codex --compact-tools` writes an exact eight-tool core allowlist.
  Model-free serialization measures 1,777 `o200k_base` proxy tokens for all
  definitions versus 976 for compact. Profile changes require `--refresh`;
  custom allowlists fail closed.
- Three compact-profile pairs are exact-correct in all six arms. Compact used
  one MCP call and returned 425 fact bytes per run; baseline used two shell
  calls and read 4,154 bytes. Compact averaged 49,748.7 total / 9,044.7
  non-cached input tokens and 17.81 seconds versus baseline's 46,014.0 /
  5,992.7 and 16.99 seconds: +8.1% total, +50.9% non-cached, and +4.8%
  latency. Pair 1's non-cached advantage did not replicate. Keep compact
  opt-in. The minimum is met for this tiny task, with no savings demonstrated;
  any larger-schema crossover test is a separate experiment.
- `scripts.prepare_agent_eval --scaled` builds that separate experiment
  offline with 100 unrelated archive tables: 114 tables, 420 columns, unchanged
  task facts, and one declared FK. Its canonical full-facts packet is 51,256
  bytes / 12,651 local `o200k_base` proxy tokens, SHA-256
  `af7d8d09825c4ec85e4e2be4cf9d3d61b8debafdb9ea9d7f21496edd065d525c`.
  This builder was prepared without model use; later scaled runs remain a
  separately authorized experiment.
- Three 114-table scaled matched pairs are exact-correct in all six arms.
  Compact Graphit averaged 49,749.7 total / 8,619.0 non-cached input tokens
  versus baseline's 59,315.0 / 14,259.0: -16.1% total and -39.6% non-cached,
  with both lower in every pair. Mean latency was 4.7% lower, but Graphit was
  slower in two pairs and pair 3 baseline read the packet twice. This is a
  repeat-supported crossover for the exact task, not universal savings; keep
  compact opt-in and preserve the negative 14-table result separately.
- Offline HTML graph exports now include local text search and relationship-
  type filters over the existing bounded one-hop projection. The only script
  is fixed and authorized by exact CSP SHA-256; metadata is escaped into
  non-executable attributes, no remote asset/server is introduced, and full
  SVG/list content remains readable without JavaScript. Multi-hop expansion
  remains future work.
- Transitive table impact follows incoming confirmed database FKs using
  deterministic shortest paths and bounded 500-table/2,000-edge work, not
  application lineage. It is available as `graphit impact-tree` and MCP
  `get_transitive_impact`; a self-FK remains visible in direct impact only.
- Project MCP setup now has explicit `--refresh` for stale absolute paths. A
  candidate must match Graphit's generated Python-module command shape with no
  extra fields; Codex also requires the exact original stanza bytes before
  replacing it. No server-name-only ownership inference or global config write.
- The pinned dev-only `tiktoken==0.14.0`/`o200k_base` benchmark counts only
  returned MCP text, not actual Codex/Claude usage. Its first encoding load
  downloads checked data; local `.cache/tiktoken` can be selected with
  `TIKTOKEN_CACHE_DIR`. The 14-table selector-plus-three path costs 651-1082
  tokens versus a 618-token all-schema dump; the 114-table path costs 660-1082
  versus 6418. Neither comparison has equal information or full task success.
- `get_relevant_context(max_followups=1..3)` now supports an opt-in navigation
  budget while retaining the default three suggestions. On the fixed ERP
  fixture, the first suggested graph alone exposes the two already visible
  labeled links; this is not a general completeness rule. Keep the default
  broad until real-agent evaluations justify narrowing it.
- `graphit diff` now compares two explicit completed local versions within a
  SQLite read transaction. It excludes external FK target stubs but includes
  declared key/FK definitions; zero changes still does not establish review,
  index, or view-lineage equivalence.
  The 100,000-object work cap fails closed; result cap is 500 with total count.
- Declared constraint drift uses constraint objects as stable identities.
  Key metadata carries ordered columns/kind/inheritance; FK target, scope,
  ordered pairs, and flags live on a separate table-level `REFERENCES` edge.
  The diff now requires exactly one matching confirmed edge per FK constraint
  and changes its JSON scope to `SCHEMA_RELATION_COLUMN_KEY_FK`. It does not
  depend on legacy `UNIQUE` versus `UNIQUE_KEY` edge spelling.
- Exact diff pagination happens after the bounded fact union is sorted, so
  changing `offset` cannot change ordering or the total count. There are at
  most 100,000 facts per saved snapshot, so a 200,000 maximum offset reaches
  any possible final page; `truncated` refers only to later changes, and the
  offset must be returned to avoid treating a later page as a full diff.
- On Windows, PowerShell execution policy blocks npm `.ps1` shims; invoke
  `npm.cmd` and `openwolf.cmd` without changing system policy.
- OpenWolf anatomy does not automatically honor `.gitignore`; local dependency
  directories such as `.venv` must also be listed in `.wolf/config.json`.
- `graphit init` now sets up a versioned SQLite schema v1 in
  `.graphit/graphit.db`; agent wiring and source scanning remain later slices.
- SQLite migrations execute one statement at a time inside `BEGIN IMMEDIATE`;
  `executescript` is unsuitable because it implicitly commits and breaks
  migration rollback.
- Source definitions are persisted in SQLite rather than `graphit.toml` because
  scan/snapshot records will reference them; only the credential environment
  variable name is stored. `graphit source add/list/show` do not resolve it or
  connect to PostgreSQL.
- Psycopg 3.3.5 with binary wheel installs for the current Windows/Python 3.14
  environment. Connection tests pass separate keyword parameters, enforce
  read-only/timeout startup options, and never surface raw driver exceptions.
- The initial scanner uses `pg_namespace`, `pg_class`, and `pg_attribute` through
  bounded, parameterized catalog SELECTs. Scan results remain ephemeral and
  preserve exact raw identifiers; a repeatable-read transaction keeps the
  table/column view consistent until persistence is implemented.
- `pg_constraint.conkey` and `confkey` carry ordered source/reference attribute
  numbers. The scanner resolves each through `pg_attribute` with ordinality,
  validates pair counts, and marks out-of-scope FK targets instead of silently
  inventing complete target nodes.
- Each successful scan now creates an immutable per-source SQLite snapshot in
  one transaction. Failed source reads create a FAILED scan run but no snapshot;
  failed local writes roll back the entire new run and graph. Out-of-scope FK
  targets are stub nodes with `in_scope: false`, never claimed as fully scanned.
- A column's `unique_value` flag means a single-column declared primary or
  unique key. Membership in a composite unique key alone does not make that
  individual column unique.
- Local object search is a `graphit.queries` service used by the CLI; it
  selects only a source's latest completed snapshot and never reconnects to
  PostgreSQL. Python `str.casefold` is registered as a SQLite function to
  preserve Unicode-insensitive matching, while `instr` treats `%` literally.
- Search ranking places exact object-name matches before type preference; an
  exact schema match can precede a partial table-name match. The hard output
  cap is 100 and `limit + 1` detects truncation.
- Exact table context parses PostgreSQL-style one/two-part identifiers,
  including quoted dots and doubled quotes. Unquoted names fold lowercase;
  a bare name in multiple schemas is an error, never a guessed match.
- Table context is a bounded read of the latest completed SQLite snapshot:
  columns, PK/unique keys, and outgoing table-level FKs each have independent
  `limit + 1` truncation checks. No target database connection is opened.
- The first snapshot writer accidentally used `UNIQUE` edge type for declared
  unique keys. New snapshots use documented `UNIQUE_KEY`; readers accept both
  to keep older local snapshots queryable.
- Direct table relationships reuse exact table resolution and parse the same
  table-level FK metadata as `show`. They filter `REFERENCES` edges to
  `origin = DATABASE`, `status = CONFIRMED`, and TABLE endpoints. Incoming and
  outgoing directions each have independent 20-default/100-max limits;
  self-FKs intentionally appear in both.
- Direct structural impact reuses `_resolve_table`, `_direct_relationships`
  and `_foreign_key_detail` rather than inventing a separate FK parser.
  A read-transaction count of incoming confirmed database FK edges also
  counts distinct source table IDs, because multiple constraints from one
  child must not inflate the number of dependent tables. Self-FKs count as
  one referencing table; external target stubs remain explicitly partial.
- Column-level impact can derive exact source-to-target pair position from
  the saved table-level FK `column_pairs` while keeping scope to confirmed
  database edges. Parse the final unquoted dot to separate a 2/3-part column
  reference, then reuse table identifier semantics; external target stubs do
  not have saved target column objects and must return `COLUMN_NOT_FOUND`.
  A 5,000-incoming-FK work cap fails closed before claiming a total pair count.
- Local FK path queries use bounded breadth-first search over the same
  confirmed database table edges. Neighbor qualified name then edge ID chooses
  a deterministic tie; steps record traversal direction separately from true
  FK source/target column order. Limits: 4 default/8 max hops, 500 visited
  tables, 2,000 examined FK adjacencies.

- Official MCP SDK 2.x supports both an in-process `Client(MCPServer)` and a
  subprocess `Client(StdioServerParameters(...))`. `list_tools()` returns a
  result object with `.tools`; dictionaries appear in `structured_content`.
  Pydantic tool annotations use snake_case Python fields despite camelCase
  wire aliases. MCP adapters reuse local query services only. Returning a
  regular dict or string duplicates it into `content` and `structured_content`;
  explicitly returning `CallToolResult` with one `TextContent` avoids this.
- MCP `source` names a saved database source; path endpoints use `from_table`
  and `to_table` to avoid overloading that field. The relationship and path
  tools reuse the existing snapshot queries, preserving confirmed-FK-only
  edges, direction/scope labels, independent relationship truncation flags,
  and distinct no-path versus traversal-budget errors.
- MCP snapshot drift is a thin adapter over `compare_snapshots`; require both
  completed versions explicitly and let the shared `_result` enforce the
  12,000-character ceiling. The response preserves scope, total count, and
  truncation, and never contacts the target database.
- Snapshot version discovery reads `snapshots` joined to completed `scan_runs`
  in one SQLite read transaction, ordering by per-source version descending.
  `completed_at` comes from SQLite `CURRENT_TIMESTAMP` (UTC); expose only
  completed versions, with an empty list for a known unscanned source.
- `database_overview` aggregates only the latest completed SQLite snapshot.
  It separates scanned schemas/tables from external FK target stubs, counts
  only confirmed database-declared table FKs, and ranks bounded in-scope entry
  tables by FK participation then qualified name. This is an explainable
  navigation hint, not a semantic importance score.
- `get_relevant_context` v1 composes up to eight bounded `search_objects`
  calls for de-duplicated task terms, requiring a single snapshot version
  across them. It ranks lexical name matches deterministically and emits
  reasons, matched/unmatched terms, scope labels, truncation, and a few exact
  `get_table` suggestions under independent object/JSON-character budgets.
  Semantic translation and graph expansion remain explicit later work.
- Official Claude Code docs use project-root `.mcp.json` with `mcpServers`
  entries, prompt for approval in interactive sessions, and let local-scoped
  servers override project entries. Graphit's explicit setup retains other
  JSON settings, rejects duplicate keys, and shares the absolute local Python
  launcher with Codex. Such generated paths are machine-specific even though
  Claude's project file itself may be version-controlled.
- The saved column `type_family` remains `UNKNOWN`; metadata-only inference
  therefore normalizes only a small allowlist of PostgreSQL key-type strings
  at read time. Exact `<table>_id` to a single-column-key `<table>.id` is an
  explainable provisional candidate, scored 0.75 cross-schema or 0.80 same
  schema. Missing sampling evidence is not awarded; candidates stay ephemeral
  `INFERRED`/`PENDING` and declared FK pairs are suppressed.
- The fixed synthetic ERP challenge benchmark measures metadata-only v1 at
  4 TP, 3 FP, 4 FN (precision 4/7, recall 4/8). Its strongest avoidable FP
  is an inferred legacy target suggested for a column with an existing
  declared CRM FK: v1 excludes exact declared column pairs but not all
  alternatives for that source column. The fixture is intentionally small and
  adversarial, not a real-world accuracy estimate.
- Metadata rule v2 suppresses candidates for any source column in a confirmed
  declared FK by reading ordered source-column pairs from table-level FK
  metadata. This includes out-of-scope targets, which have no column-level
  REFERENCES edge. On the unchanged tiny challenge set, v2 yields 4 TP,
  2 FP, 4 FN (precision 4/6, recall 4/8); remaining FPs are ambiguous
  duplicate target names and an external identifier with a matching local key.

## Do-Not-Repeat

<!-- Mistakes made and corrected. Each entry prevents the same mistake recurring. -->
<!-- Format: [YYYY-MM-DD] Description of what went wrong and what to do instead. -->

- [2026-10-04] Do not turn the recommendation to use read-only credentials into
  an onboarding blocker when the user expects zero-config discovery. For SQL
  Server, warn on privileged principals and retain the fixed catalog-only path.
- [2026-10-04] In PowerShell, invoke executable paths containing spaces with the
  call operator and a quoted path (`& "C:\\path with spaces\\python.exe"`).
  Quoting arguments alone does not make the executable path callable.

- [2026-10-02] Do not combine public-package behavior and recursive temporary
  environment cleanup behind silent output. Emit flushed smoke stage labels and
  keep verified cleanup separate so a slow Windows deletion cannot masquerade
  as an MCP hang.
- [2026-09-16] `openwolf init` left `AGENTS.md` with only the OpenWolf block.
  Preserve the OpenWolf block while retaining Graphit-specific coding rules.
- [2026-09-16] Do not assume documented files are at repository root; the
  architecture and product specifications are currently under `docs/`.
- [2026-09-16] OpenWolf Markdown contains Unicode headings. When patch matching
  fails because terminal output is mojibake, use UTF-8 reads or replace the
  controlled file via separate delete/add patches.
- [2026-09-16] Do not infer a server/web product from earlier repository docs
  after the user has clarified a local-first CLI/MCP objective. Explicit product
  intent supersedes stale architecture documents; update the documents first.
- [2026-09-16] Typer collapses an application with one command into a root
  command. Add a root callback when the public contract requires subcommands
  such as `graphit version`.
- [2026-09-16] On Windows sandbox, pytest's default temp path can be
  inaccessible; use a project-local ignored `--basetemp`. Windows without
  symlink privileges must skip only the symlink-creation-specific test.
- [2026-09-16] Do not import an application function named `test_*` into a
  pytest test module: pytest collects it as an extra test. Name the adapter
  function `verify_connection` instead.
- [2026-09-16] When testing search ordering, account for relevance tier before
  object type; exact schema-name hits outrank partial table hits.
- [2026-09-16] Keep graph edge writers and readers aligned with documented
  edge names; when correcting a persisted name, retain read compatibility for
  older immutable snapshots.
- [2026-09-16] MCP SDK v2 `list_tools()` is not directly iterable; inspect
  `.tools`. Use snake_case constructor names for `ToolAnnotations` to satisfy
  strict mypy, even though JSON aliases are camelCase.
- [2026-09-16] Do not return dict or string from compact Graphit MCP tools:
  SDK v2 emits both text and structured copies. Return an explicit
  `CallToolResult` with one compact JSON `TextContent` instead.
- [2026-09-16] When reading arbitrary JSON in strict mypy tests, narrow an
  `object` to `list` before iterating through path steps. Keep new test lines
  within Ruff's 100-character limit.
- [2026-09-16] Do not compare differently typed empty tuples in one chained
  mypy assertion; assert each result field separately. Run Ruff formatter on
  compact FK test fixtures instead of guessing its multiline layout.
- [2026-09-16] Markdown patches containing Unicode arrows may fail to match
  PowerShell's displayed mojibake. Patch the adjacent ASCII text in smaller
  hunks. Ruff may prefer one-per-line tuple fixtures even under 100 columns.
- [2026-09-17] Inspect both built archives, not just the wheel: Hatchling's
  default sdist included `.wolf`, `.codex`, and `.claude`. The separate smoke
  venv must live under `.venv` for scoped elevated package downloads on this
  Windows setup; `.pytest-tmp` was inaccessible to the elevated process.
- [2026-09-17] Keep source-archive and wheel member-name collections distinct
  in typed release checks; reusing one name for set and list triggered mypy.
  Run both Ruff lint and formatter because the former also enforces import
  spacing that a formatter check alone did not flag.
- [2026-09-17] `with sqlite3.connect(...) as connection` does not close the
  handle; use `contextlib.closing` for read-only smoke inspection. On Windows,
  an open SQLite file prevents disposable project cleanup with WinError 32.
- [2026-09-17] On Windows, `Path.write_text` can translate `\n` to CRLF.
  Compare the actual pre-write bytes when testing Codex's byte-preserving
  config append, not bytes re-encoded from the LF source literal.
- [2026-09-17] Refreshing a parsed Codex TOML entry by text replacement must
  also reparse the resulting content and verify the intended entry changed;
  a lookalike snippet in a comment is not sufficient evidence of success.
- [2026-09-17] A constraint named `order_external_fk` is an in-scope declared
  fact even when its target table is an out-of-scope stub. Test stub exclusion
  by target qualified-name prefix, not by the substring `external` anywhere.
  Exact diff ordering is by qualified name, so key and FK kinds may interleave.
- [2026-09-17] `asdict` retains tuple-valued diff changes, whereas an MCP JSON
  round trip yields lists; normalize the expected dataclass through JSON in
  parity tests. Isolated builds still need scoped network access for Hatchling;
  `--no-isolation` does not work when Hatchling is not installed locally.
- [2026-09-17] A context-managed `sqlite3.connect` commits/rolls back but does
  not close the connection. Use `closing(...)` around a transaction in Windows
  tests that reopen or clean up temporary stores. Run Ruff formatter checks
  after adding small MCP adapters; it may collapse wrapped signatures.
- [2026-09-17] Ruff formatter may collapse annotated CLI options and JSON
  parity expressions but expand a long assertion into a parenthesized form;
  follow its check output exactly before final verification.
- [2026-09-17] When adding a many-argument `ForeignKeyMetadata` fixture,
  run Ruff formatter on the test file rather than hand-packing arguments;
  Ruff expands each argument and may also adjust short assertions.
- [2026-09-17] New MCP imports must follow Ruff's alphabetical ordering,
  including `ColumnImpactContext` before `DatabaseOverview` and
  `column_impact` before `database_overview`; format the new test fixture
  mechanically before final gates.
- [2026-09-17] Keep release smoke aligned with the full documented MCP tool
  set, not a stale subset; bound both tool listing and a real saved-fact call
  under the same stdio timeout. Ruff's 100-character check also applies to
  smoke assertions, even when formatter check passes.
- [2026-09-17] Do not equate passing code tests with MVP or publication
  readiness; check README/roadmap claims against actual scanner scope and
  separate local package smoke from remote CI and real agent tasks. Set the
  existing tiktoken cache path for offline full-suite runs.
- [2026-09-17] Scripted scanner tests filter result rows by selected schema
  before parser validation; use an in-scope schema with a missing relation to
  test fail-closed parsing. When adding an index row to the fixture, keep both
  ordered name and attribute-number arrays aligned and run Ruff formatter.
- [2026-09-17] Python loop variables share function scope for mypy: a later
  `str | None` index key must not reuse an earlier `str` variable name.
  Also run isolated build and archive inspection as separate commands; a
  later successful check can mask a failed build's shell exit code.
- [2026-09-17] `StoredSnapshot` uses `.version`, while local query results use
  `.snapshot_version`; do not interchange them in parity tests. Run Ruff
  formatter after new list comprehensions/CLI options before final gates.
- [2026-09-17] An isolated `python -m build` may fail while fetching Hatchling
  in the sandbox and then fail to decode pip output; rebuild with scoped
  elevation, then inspect fresh archives in a separate command. Ruff format
  can still collapse short async MCP calls after tests pass.
- [2026-09-17] On this Windows sandbox, normal `docker info` can report
  config/pipe access denied even when Docker Desktop is running. Use a scoped
  elevated read-only daemon check before concluding Docker is unavailable;
  inspect exact container name/port before starting a disposable fixture and
  verify `--rm` auto-removal afterward.
- [2026-09-17] `codex --version` can print a nonfatal "Could not find home
  directory" PATH-alias warning in this sandbox; treat version output as
  evidence of the binary only, not proof of authenticated model execution.
  `Get-Command claude*` returns no client here; do not silently substitute an
  MCP handshake for an actual Claude task.
- [2026-09-17] `database_overview` has a 50-item maximum even though
  `show_table` and `table_relationships` permit 100. Use each service's own
  published limit in offline packet builders; do not reuse one page constant.
  In strict mypy, keep numeric counters separate from `dict[str, object]`
  packet values and use typed monkeypatch wrappers for imported functions.

## Decision Log

<!-- Significant technical decisions with rationale. Why X was chosen over Y. -->

- SQL Server write-capable credentials are accepted rather than rejected. The
  adapter still issues only fixed bounded catalog SELECTs, requests ODBC
  read-only mode, rolls back, and reports `PRIVILEGED_CREDENTIAL`; Graphit never
  exposes an application SQL, DDL, or DML execution surface.

- Graphit supplies database context but does not generate, optimize, or execute
  application SQL; SQL-capable agents and dedicated MCPs consume its context.
- Maintain an evidence ledger for MVP/release claims. Prioritize bounded index
  metadata as the next self-contained engineering slice, while keeping actual
  agent-task evaluation and publication as independent external gates.
- Evaluate agent task success with equal answer-bearing facts before claiming
  token or time savings. Keep the first context-delivery comparison distinct
  from a later Graphit-before-SQL-MCP workflow test; require explicit approval
  before model invocations that may incur cost or trust prompts.
- Scan indexes as bounded structural facts only. Do not decompile or persist
  expression/predicate SQL by default; represent expression key positions
  explicitly and separate them from INCLUDE columns. The explicit CLI index
  lookup may page those saved facts, but default agent context stays small;
  MCP exposure remains a separate opt-in slice.
- MVP uses PostgreSQL for Graphit metadata and PostgreSQL as the only source
  engine. Neo4j and additional infrastructure are deferred until measurements
  justify them.
- Superseded: PostgreSQL is no longer Graphit's required metadata store. SQLite
  is the project-local default; PostgreSQL remains the first source adapter.
- Superseded: FastAPI and Next.js are not required architecture. CLI and stdio
  MCP are primary; visualization is an optional generated local projection.
- Python 3.11+ replaces the Python-3.14-only constraint to support wider adoption.
- PyPI `graphit` is occupied by an unrelated legacy project; the user confirmed
  `graphit-db` as the distribution name while keeping the `graphit`
  command/import name.
- Apache-2.0 is approved and included in package metadata/artifacts. Onur
  Parapan is the confirmed copyright holder and package author.
- `graphit-db 0.1.0` was published on PyPI on 2026-10-02 from protected GitHub
  workflow run `37020399874` after explicit environment approval. Both artifacts
  have PyPI attestations; clean public-PyPI install, init, SQLite, generated
  Codex/Claude launchers, and MCP calls pass. Future releases must keep the same
  separated build/publish and human-approval boundary.
- `graphit-db 0.2.1` was published on 2026-10-04 from tag `v0.2.1` at commit
  `b9832afe8ca4b827f0567fde6c7a84ec54c33dfc`. CI `37218730682` and protected
  publish run `37218891628` passed; both PyPI artifacts have GitHub provenance,
  and a fresh public install passed dependency, version, SQLite, and generated
  Codex/Claude MCP smoke checks.
- Stale project MCP paths are repaired only with an explicit `--refresh` flag;
  default setup still rejects any differing Graphit entry. This is safe
  regeneration, not a portable launcher for every machine.
- Snapshot drift now compares declared key/FK definitions from paired saved
  facts, with exact order/scope and fail-closed inconsistency. The changed
  JSON scope label is a documented compatibility change; lineage, indexes,
  and human review changes are still separate future slices.
- Source definitions use the SQLite `sources` table; the project TOML retains
  shareable scan defaults until a separate import/export contract is designed.
- Psycopg 3 is the first PostgreSQL driver. Startup options enforce a read-only
  transaction default and bounded statements; source test uses one SELECT and
  sanitized driver errors.
- Catalog scans fail on missing/inaccessible selected schemas and table/column
  limit overflow; they never present a truncated graph as complete.
- Declared PK/unique/FK constraints are bounded and kept with exact column
  order; unvalidated and inherited constraints remain visibly labeled.
- First local search requires `--source`, returns bounded human/JSON results
  from the latest completed snapshot, and leaves the source database untouched.
- Exact table lookups use PostgreSQL identifier semantics and reject ambiguous
  bare names. Table context has bounded columns, keys, and outgoing declared
  FKs; older `UNIQUE` edges remain readable after the `UNIQUE_KEY` correction.
- Relationship inspection exposes direct confirmed database FK facts only;
  inferred/pending edges are excluded and external target stubs stay labeled.
- Shortest-path inspection treats confirmed declared FK connectivity as
  undirected for traversal, but labels every forward/reverse step and fails
  distinctly on hop-limited no-path versus node/edge budget exhaustion.
- The first MCP slice uses official SDK 2.x, exposes read-only snapshot search
  and table context with a 12,000-character ceiling, and leaves agent settings
  untouched. Manual stdio wiring is documented; remaining tools are later slices.
- The first overview uses deterministic declared-FK degree to orient agents,
  visibly separates out-of-scope stubs, and is MCP-only until a distinct CLI
  workflow is justified. Revisit the ranking after task-aware selection can
  be measured against it.
- The first task-context tool is deliberately lexical and bounded: no LLM,
  translation, FK graph expansion, or live database access. It is a baseline
  for later measured relevance improvements and uses fail-on-rescan semantics
  to avoid mixing immutable snapshots.
- Current official Codex docs allow `[mcp_servers.<name>]` in trusted-project
  `.codex/config.toml`. `command` and `args` launch stdio tools; project layers
  are skipped for untrusted workspaces. Graphit wires this only through the
  explicit `graphit mcp setup-codex` command, preserving existing bytes and
  using the active installed Python interpreter plus absolute project path.
- Claude's project `.mcp.json` can be shared, but this generated Graphit entry
  uses absolute local paths for reliable startup; warn before committing it.
- Initial relationship inference deliberately previews a narrow metadata-only
  rule through CLI. Do not persist or expose these candidates as confirmed
  graph facts before ground-truth precision evaluation.
- Keep synthetic ground-truth labels separate from algorithmic name rules;
  report TP/FP/FN and score bands, but do not extrapolate tiny-fixture rates
  as production precision or report overall accuracy without all negatives.
- For inference suppression, inspect table-level declared FK metadata, not
  only column-level edges: out-of-scope FK targets lack column-level edges.
- For ambiguous exact-name targets, default to abstention; expose up to three
  alternatives only through explicit CLI diagnostic opt-in. Synthetic v3
  precision rose to 3/4 while recall fell to 3/8; do not treat this as
  production calibration or let the same-schema bonus silently choose.
- External and local identifiers can be structurally indistinguishable.
  Preview remains metadata-only; SQL LIMIT does not by itself bound source
  scan work, and value overlap cannot prove business identity. Favor an
  explicit user review decision over automatically escalating the candidate.
- The first review action is explicit rejection keyed by source alias and
  exact quoted column pair, not snapshot row IDs. It persists across rescans,
  is idempotent, and filters before ambiguity calculation. Restore must follow
  before approval; never relabel a reviewed hypothesis as a database FK.
- `RESTORED` is an append-only review event, not a relationship state. Preview
  uses the latest decision; restore is allowed even when a later scan removed
  the candidate, so a stale rejection does not silently reapply if it returns.
  Re-rejection is explicit and preserves the audit trail.
- Human APPROVED is a local review decision on a current inferred pair, not
  DATABASE/CONFIRMED. Keep `origin: INFERRED` and metadata score unchanged;
  default preview may surface an approved choice amid ambiguity, while
  diagnostic mode retains other PENDING alternatives. MCP FK tools remain
  fact-only until explicit reviewed-context design.
- `REVOKED` is an append-only reversal of APPROVED, separate from RESTORED
  (reversal of REJECTED). The latest event controls preview; revocation can
  clear a stale approval even when the current schema no longer yields the
  candidate. Keep history and source identity intact.
- Cross-name manual proposals use a separate `MANUAL_PROPOSAL` review kind;
  `PROPOSED` is a local event, not an edge or approval. Existing inferred
  review and graph readers filter by relationship kind, so proposal creation
  must not affect them. Validate exact snapshot columns and declared source
  FKs before recording a bounded human reason; malformed FK metadata fails
  closed. Listing and manual approval remain separate steps.
- Manual proposal listing collapses latest reason events by exact source-scoped
  pair, pages newest-updated pairs, and rechecks only saved snapshot structure.
  `ELIGIBLE` is not semantic approval. Preserve stale proposal history visibly
  after rescans; never surface it as an agent-visible relationship by default.
- Explicit manual approval is a separate source-scoped event under
  `MANUAL_PROPOSAL`, retaining the latest human reason. Recheck current saved
  structure and snapshot in one SQLite write transaction; do not let
  `review propose` silently replace `APPROVED`. Manual approval remains
  graph/MCP-invisible until a dedicated projection slice.
- Manual revocation is a `REVOKED` event retaining the last reason. It must
  work after a stale rescan and be idempotent. Re-approval requires explicit
  re-proposal, even when the reason is unchanged; list output must use the
  latest status instead of a fixed PROPOSED label.
- The local graph command explicitly opts into eligible manual approvals;
  `table_graph` remains manual-excluding by default because MCP reuses it.
  Manual graph links have a reason but no metadata confidence, and duplicate
  exact inferred-approved links yield to the manual reason. Keep a separate
  truncation flag and HTML/DOT provenance; preserve existing legend wording.
- The first graph projection is exact-table, one-hop JSON on stdout, not an
  unbounded whole-schema dump or a required frontend. Render database FKs and
  current human-approved logical links with separate origin/status; never
  include PENDING by default. Flag bounded-output truncation and rescan races.
- FK-only graph neighborhoods must not depend on a full-snapshot inference
  pass. Check current adjacent approval events first; only validate candidates
  when an approval might be shown. A stale/revoked approval creates no link.
- Approved graph links can be checked with a bounded subset of approved source
  columns plus possible id targets, while retaining the shared inference
  matching/ambiguity/scoring loop. New declared FKs suppress old logical links.
- DOT is an optional stdout renderer of the existing bounded JSON projection,
  not a second graph query or a required Graphviz dependency. Quote all source
  identifiers; visibly label origin, status, scope, and truncation without
  relying on color alone.
- The first HTML graph is static and offline: inline SVG plus an exact textual
  equivalent, no scripts/CDN/server. Keep stdout default; opt-in output file
  uses UTF-8 exclusive create and never overwrites existing data.
- Agent graph context is a separate MCP tool, not a change to fact-only
  get_relationships. Keep exact columns, clear FK versus approved logical
  provenance, small per-category limits, and a hard response ceiling. A
  shorter serialized fixture is not yet proof of real token savings.
- The first fixed ERP-like retrieval benchmark uses an oracle-selected focus
  and only character counts. Report known missing synonyms/ambiguous targets
  alongside any size reduction; never infer production token savings or task
  success from the tiny synthetic set.
- The oracle-free continuation feeds task text to `get_relevant_context` and
  uses only suggested exact table names for graph calls. Focus ranks are
  3/1/2/1/1 (top-3 5/5), but first and top-three graph follow-ups surface only
  the same 2/4 labeled positives. Selector-plus-first-graph character totals
  all exceed the tiny 2,315-character all-table baseline, so optimize selector
  payload/navigation before claiming an end-to-end efficiency improvement.
- Task-context domain dataclasses can retain optional fields while the MCP
  payload omits unavailable per-object attributes. The query budget must use
  that same payload serializer or it will drop objects earlier than the wire
  limit requires. On the fixed fixture, omitting repeated `null` fields saves
  144-288 characters per selector call without changing focus ranks.
- In the five-question fixture, the two surfaced positive links are already
  visible in the first suggested graph response. A label-aware stop would
  save 1,894 response characters over always reading three suggestions, but
  this uses oracle labels and is not an actual-agent result. Keep all options,
  guide progressive inspection, and never infer relationship absence from an
  empty one-hop neighborhood.
- Adding 100 unrelated archive tables with four columns each (114 tables,
  420 columns total) leaves the five task focus ranks at 3/1/2/1/1. The common
  `id` column makes two per-term candidate searches report truncation without
  displacing those focuses. Selector plus three graph responses is 2,591-3,993
  chars versus a 24,915-char all-table structural dump, but they are not
  equivalent-information payloads or evidence of real agent token savings.
- Final task-context ranking must retain per-term search's exact object-name
  preference when resolving equal hit counts. Sixty `customer_invoice_*`
  distractors otherwise hid `sales.payment` and `sales.invoice` from all three
  suggestions. An exact-name tie-break restores top-three focus 5/5 and both
  known links on the 174-table fixture; current focus ranks are 3/1/2/1/2,
  with `finance.account` correctly available ahead of `billing.invoice` for
  an `account` task. This is lexical ordering, not business identity proof.

- Whole-database ERD projection is a local human artifact, not an agent payload:
  default init writes it under ignored `.graphit/exports`, while `graphit erd`
  regenerates from the latest snapshot. Only declared confirmed FKs appear;
  reviewed logical assertions remain in the focus-table graph.
- Default init agent wiring deliberately selects Codex's measured compact
  eight-tool allowlist while standalone setup retains its full-profile default.
  Both Codex and Claude writes stay project-local; no-agents is a full opt-out
  and refresh-agents accepts only recognized generated Graphit launchers.
- Default init deduplicates existing sources by non-secret database identity,
  not credential-reference location. It isolates each candidate's operation
  failures, finishes later candidates and agent setup, preserves successes, and
  only then returns the first applicable failure code with a partial-failure
  summary.
