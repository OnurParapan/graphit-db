# Graphit — Testing Strategy
## Quality priority

Graphit makes factual claims about database structure and relationships.
Correctness, safety, explainability, and bounded output take priority over UI
polish.

## Unit tests

Required for:

- configuration parsing and secret redaction,
- identifier normalization,
- type compatibility,
- relationship scoring and thresholds,
- candidate pruning,
- graph traversal and cycles,
- deterministic context ranking,
- response budgets and truncation,
- SQLite migrations and transaction rollback.

## PostgreSQL integration tests

Use disposable PostgreSQL containers in CI and opt-in locally. Fixtures include:

- simple and composite PK/FK,
- self-referencing FK,
- multiple schemas,
- quoted/mixed-case identifiers,
- views and materialized views,
- logical relationships without FKs,
- large-table statistics paths,
- insufficient permissions and timeout failures.

No integration test may issue source DDL/DML through Graphit code. Fixture setup
is isolated test infrastructure.

`tests/test_postgresql_integration.py` is opt-in. It skips in the ordinary
test run. It requires both `GRAPHIT_TEST_PG_DISPOSABLE=1` and
`GRAPHIT_TEST_PG_ADMIN_DSN` pointing to a **disposable loopback-only**
PostgreSQL database with explicit database and port. Do not point it at a
shared or production database: its admin fixture creates and then drops
uniquely named schemas and a temporary reader role. The reader receives
schema `USAGE` and table/view `SELECT`, not source write privileges; Graphit's
scanner independently enforces a read-only transaction. Only the admin
fixture executes DDL. The test exercises real catalog SQL, composite and
self FKs, quoted names, views/materialized views, scan caps, permission denial,
snapshot persistence, and local reads.
The index fixture also verifies the saved composite expression/`INCLUDE`/
partial index through the shared local query, `graphit indexes --json`, and
MCP `get_index_context`, checking that key positions and expression
placeholders survive the complete path. This was run locally against a
temporary PostgreSQL 16 container; the container was stopped and auto-removed.

For a disposable local Docker test on PowerShell, with a free local port:

```powershell
docker run --rm -d --name graphit-postgres-int -e POSTGRES_PASSWORD=graphit-test-only -p 127.0.0.1:55432:5432 postgres:16
$env:GRAPHIT_TEST_PG_DISPOSABLE = "1"
$env:GRAPHIT_TEST_PG_ADMIN_DSN = "postgresql://postgres:graphit-test-only@127.0.0.1:55432/postgres"
.\.venv\Scripts\python.exe -m pytest tests/test_postgresql_integration.py -q
docker stop graphit-postgres-int
```

The example password is only for this localhost-bound disposable container.
`docker stop` removes it because the container was started with `--rm`. The
test also cleans its generated schemas and role before the container stops.

The repository CI workflow (`.github/workflows/ci.yml`) runs on pushes and
pull requests across Python 3.11, 3.12, 3.13, and 3.14. Each Linux job starts
an ephemeral PostgreSQL 16 service, installs `.[dev]`, and runs `pip check`,
Ruff lint/format checks, strict mypy, and the full pytest suite with the
explicit disposable integration-test gate. The service password is a fixed
test-only value inside the isolated CI job; no repository or user database
credential is required. The job has read-only repository token permissions
and a 15-minute timeout. A workflow file is not proof that remote CI has run:
the first GitHub run still needs inspection.

This CI verifies a small real catalog fixture, not 1,000–5,000-table stress,
all PostgreSQL versions, package publication, or real Codex/Claude task
outcomes. Those remain separate release and product gates.

The initial scanner unit tests use a scripted driver boundary to verify query
parameters, read-only settings, hard caps, quoted identifiers, and sanitized
failure paths. These tests do not substitute for a live PostgreSQL integration
test; catalog SQL must be exercised against a disposable server before the
metadata-scan milestone is declared complete.
The view slice additionally checks `r`/`p`/`v`/`m` catalog classification,
view/materialized-view columns, shared table/view and column caps, immutable
local persistence, latest-snapshot search/context visibility, and failure
rollback. It does not test view definition parsing or lineage.
View-detail tests cover exact quoted names, view/materialized-view kind,
catalog-only `NOT NULL` wording, independent column caps, ambiguous bare
names, table rejection, source isolation, latest rescan, matching CLI/MCP
JSON, task-context follow-ups, and the MCP response ceiling.
Index-scanner unit tests check bounded catalog reads, parameterized selected
schemas, ordered key versus included columns, expression placeholders,
partial/valid/ready flags, malformed attribute mappings, and fail-closed index
caps. The disposable PostgreSQL fixture includes a real composite
column/expression/`INCLUDE` partial index, but its integration test is skipped
without an explicitly provided disposable server. Local persistence tests
verify saved index identity/flags, positioned key/include edges, expression
placeholder, materialized-view parent, rescan isolation, failed scan and
transaction rollback, and unchanged default search. The live integration
test also checks the new index is saved when explicitly enabled. Index-lookup
tests now cover ordered pages/counts, expression positions,
matview/table identity, old snapshots, rescans, source isolation, invalid
limits, corrupt edge rejection, JSON/human output, and no source connection.
MCP index tests cover one-text-block service parity, pagination, explicit
opt-in behavior, invalid/absent targets, invalid bounds, corrupt local edges,
response ceiling, no source connection, and a real stdio call. The clean-wheel
smoke calls the saved-index tool through both generated agent launchers.

## Snapshot tests

Verify:

- successful scans are immutable,
- failed scans never become latest,
- rescans expose new/changed objects,
- historical snapshots remain queryable,
- review decisions reconcile by logical key.

Snapshot-diff tests compare explicit completed versions after real local
snapshot writes, including added/removed objects, changed column type and
flags, view kind, external-target stub exclusion, identical rescans, source
isolation, output and work bounds, corrupt saved data, CLI JSON/human output,
and no target-database connection. Declared-key/FK cases additionally cover
additions/removals, ordered composite columns/pairs, FK target and scope,
validation/inheritance flags, old `UNIQUE` edge spelling, and fail-closed
missing or inconsistent table-level FK edges. The diff does not infer renames,
view lineage, indexes, or review-decision changes.
Offset tests traverse beyond the first 500 changes with deterministic,
non-overlapping pages and validate total count, final empty page, invalid
offsets, and CLI/MCP parity including real stdio.

## MCP contract tests

Direct-impact tests cover multiple FKs from one child, unique dependent-table
counts, self-FKs, exclusion of a manually inserted approved inferred edge,
transitive exclusion, external stubs, source/snapshot isolation, ambiguous
names, limits, CLI/MCP parity, stdio, and the MCP response ceiling.
Transitive-impact tests cover shortest incoming declared-FK paths, duplicate
FKs, self-links, inferred-link exclusion, external stubs, hop/list/work
budgets, no source connection, CLI/MCP parity, stdio, and response ceiling.
Column-impact tests cover quoted dotted names, ambiguity/missing columns,
composite pair position, self-FKs, no-FK columns, source isolation, bounded
incoming FK work, no source connection, CLI/MCP parity, stdio, and response
ceiling.

Snapshot-history tests verify newest-first source isolation, empty and
non-completed histories, count/truncation and limits, CLI/MCP parity, a real
stdio call, response ceiling, and no PostgreSQL connection.

Snapshot-diff MCP tests compare in-process output with the shared local diff
service, verify source/version isolation, invalid limits, one compact JSON text
block, truncation/count/scope preservation, no target connection, oversized
response rejection, and a real stdio round trip.

Every tool covers:

- valid input,
- not found,
- ambiguous name,
- limit/depth enforcement,
- latest snapshot selection,
- inferred relationship labeling,
- truncation metadata,
- absence of secrets and raw samples.

## CLI tests

Use Typer's test runner for command output and exit codes. Filesystem-changing
commands use temporary project directories and never the developer's real agent
configuration.
The local review suite verifies exact candidate/snapshot validation,
idempotent rejection and restore, append-only decision history, source
isolation, rescan survival and race failure, ambiguity recalculation,
inference work limits, no source connection, and no inferred graph edge write.
Approval tests also assert `INFERRED` origin with `APPROVED` status and unchanged
score, source isolation, declared-FK exclusion, conflict handling, and that
confirmed-FK graph queries remain unaffected.
Revocation tests cover append-only/idempotent history, missing and stale
approval errors, source isolation, rescan-ineligible pairs, renewed ambiguity,
and no source connection or inferred edge write.
Manual-proposal tests cover a known cross-name ERP synonym, exact snapshot and
column validation, target-key and type checks, declared-FK exclusion,
required bounded reason, idempotence and append-only reason changes, malformed
FK metadata fail-closed behavior, CLI JSON, and isolation from candidate,
graph, and source-database reads.
Proposal-list tests cover latest-reason collapse, newest-first paging,
source isolation, stale rescan visibility, explicit limit and corrupt-history
errors, and CLI JSON. The listing remains entirely offline.
Manual-approval tests cover exact existing pair/version, preserved reason,
idempotence, source isolation, stale rescans, inferred-review conflict,
re-proposal conflict, CLI JSON, and exclusion from inference, with no source
connection. Graph/MCP exposure is covered separately below.
Manual-revocation tests cover idempotent append-only history, preserved
reason, source and snapshot isolation, stale-rescan withdrawal, explicit
re-proposal before re-approval, CLI JSON/human status, immediate removal from
local graph, and no source-database mutation.
Manual graph tests verify source- and target-side one-hop projection,
`MANUAL`/`APPROVED` provenance and reason, revocation and stale-rescan
exclusion, separate truncation, inferred-pair deduplication, DOT/HTML
rendering and HTML escaping, and no source connection. HTML tests also verify
search/filter controls, typed filter metadata, exactly one fixed script, its
matching CSP SHA-256, absence of `script-src 'unsafe-inline'`, and a complete
no-script fallback.
MCP manual-link tests cover in-process and real stdio clients, provenance and
human reason fields, FK-only tool isolation, source isolation, revocation,
rescan staleness, independent manual truncation, and the 12,000-character
ceiling. A fixed fixture asserts that adding one manual link increases the
serialized response by less than 1,000 characters; this is not a tokenizer
measurement or evidence of real agent task improvement.

## Inference benchmark

Maintain a deterministic ground-truth dataset and track:

```text
precision
recall
false positives
false negatives
acceptance by confidence band
```

MVP optimization target is high-confidence precision.

The current reproducible baseline is `tests/test_inference_benchmark.py`:
run `python -m pytest tests/test_inference_benchmark.py -q`. It saves synthetic
ERP metadata through the normal snapshot writer and scores the actual
`preview_candidates` service against hand-labeled source/target column pairs.
The test asserts no output truncation, deterministic results, exact predicted
pairs, and rule v3's default 3 true positives, 1 false positive, 5 false
negatives (precision 3/4, recall 3/8). Opting into ambiguous alternatives
restores the v2 set: 4 true positives, 2 false positives, 4 false negatives
(precision 4/6, recall 4/8). V1 on the same fixed fixture had 4 true
positives, 3 false positives, 4 false negatives (precision 4/7, recall 4/8).
A declared FK is a separate control, not an undeclared
positive. See `RELATIONSHIP_ENGINE.md` for case-level interpretation. This is
an intentionally small challenge fixture, not a production accuracy claim.
The external-account regression explicitly blocks any PostgreSQL connection
during preview and verifies that this known false positive remains only an
unverified metadata hypothesis. Future sampling needs live PostgreSQL tests
for query plans/work caps, denied permissions, timeouts, and raw-value leakage
before it can influence scores.

## Focused MCP context benchmark

Run `python -m pytest tests/test_context_benchmark.py -q -s` to reproduce a
small, labeled ERP-like benchmark over the same saved metadata fixture as the
inference challenge. It uses 14 tables and 20 columns, adds one explicit human
approval, blocks source database connections, and asks five fixed questions.
The correct focus table is supplied to `get_graph_context`; table discovery,
agent prompting, SQL quality, latency, and tokenizer-specific token counts are
**not** measured in the focused-response half of the benchmark. A separate
oracle-free half now sends the same five task strings to `get_relevant_context`,
then follows its exact table suggestions with `get_graph_context` calls. The
expected focus names are used only for scoring, never as retrieval inputs.

The comparison is a compact JSON dump of every table's local columns, keys,
and outgoing FK facts plus the approved pair. That all-table structural
baseline is 2,315 characters. The five separate focused MCP responses are
596, 816, 250, 236, and 240 characters respectively, all below the 12,000-
character ceiling and none truncated. These are character counts, not token
counts. The baseline does not carry the approved link's evidence details;
the focused response does. Focused graph responses still omit some non-key
columns a later coding task might need.

Against independent labels, the graph exposes the declared payment-to-customer
FK and the human-approved purchase-order-to-supplier link (2 of 4 labeled
positive relationships). It omits the true support-ticket `client_id` synonym
and the ambiguous sales-invoice customer target; one labeled external-account
negative is also omitted (0 of 1 negative exposed). The one-step declared FK
path is verified separately. This tiny fixture measures a retrieval boundary,
not production recall, precision, or end-to-end agent success. In particular,
a shorter local response does **not** establish real-world token savings.

In the oracle-free half, the labeled focus table ranks 3rd, 1st, 2nd, 1st,
and 2nd among suggested tables: top-1 focus discovery is 2/5 and top-3 is
5/5. The first suggested graph call surfaces the two already-known positive
links, and following all three suggestions does not recover either missing
positive; the labeled negative remains absent. Four of five selector responses
report truncation. Before omitting unavailable per-object fields, selector
responses were 2,327, 2,407, 2,324, 2,395, and 2,236 characters. They are
now 2,111, 2,119, 2,180, 2,179, and 2,092 characters (144-288 fewer).
Selector plus the first graph call now totals 2,706, 2,935, 2,775, 2,415,
and 2,332 characters. All five totals still exceed this fixture's
2,315-character all-table baseline. Following all three graph suggestions
costs still more: 3,542, 3,993, 3,025, 3,250, and 2,572 characters. This
does not disprove savings on larger schemas or in real tokenizers, but it
prevents claiming them from this small fixture. Further work should measure
selective navigation and actual agent task outcomes.

The oracle-free workflow also counts the exact text of each returned MCP
`TextContent` block with `tiktoken==0.14.0` and the named `o200k_base`
encoding. Each response is encoded separately, then counts are added; the
all-table JSON baseline is encoded once. This tokenizer is a pinned **dev-only**
dependency, and its encoding data may need a one-time download on first use.
For offline reruns after that download, point `TIKTOKEN_CACHE_DIR` to the
existing checked cache before running the full suite. In this workspace's
PowerShell session:

```powershell
$env:TIKTOKEN_CACHE_DIR = (Resolve-Path .cache/tiktoken).Path
.\.venv\Scripts\python.exe -m pytest -q
```

If the cache is absent, a first run still needs network access; an offline
failure at encoding load is not evidence that Graphit's query behavior broke.
On the 14-table fixture, the baseline is **618 text tokens**; selector plus
all three graph responses costs **905/1,082/768/821/651** text tokens for
the five tasks. The first graph only costs **683/774/702/599/587** tokens:
two of five are below the baseline, but none establishes complete task
coverage. This fixture does not support a general token-savings claim.

An opt-in `max_followups=1` selector call keeps the same ranked objects but
returns only the first table/view suggestion. Following that one suggestion
costs **641/729/680/557/566** text tokens on the same five questions versus
**905/1,082/768/821/651** for always following all three. The two labeled
links already visible with three graph calls are also visible in the first;
the two other true links still remain missing, and the labeled negative stays
absent. Only two of five one-follow-up totals beat the 618-token all-schema
baseline. Choosing one suggestion is an explicit budget trade-off, not proof
that later suggestions are unnecessary on other tasks.

The manual-approval variant uses that same five-question, 14-table fixture.
It sends only each task's text to `get_relevant_context`, then opens the first
three suggested tables with `get_graph_context`; the labeled pair is used
only to score responses. With the pre-existing inferred supplier approval,
the graph initially surfaces 2/4 labeled positive relationships and 0/1
labeled negative. After an explicit human proposal and approval of the
`sales.support_ticket.client_id` to `crm.customer.id` synonym, it surfaces
3/4 positives and still 0/1 negative. The new link is `MANUAL`/`APPROVED`,
with a human reason and no FK name or metadata score. The sales-invoice
customer ambiguity remains unresolved. Selector responses are byte-for-byte
unchanged. Across all 15 suggested graph responses for five tasks, serialized
graph context grows by 1,487 characters (per-task three-graph totals:
1,431/1,874/845/1,071/480 before; 1,805/1,874/1,584/1,445/480 after).
The relevant support-ticket link appears in the first suggested graph result
after approval. This establishes one synthetic retrieval gain with a measured
character cost, **not** real agent success, production precision/recall, or
token savings. The test also verifies no source-database connection occurs.

The benchmark also records an **idealized task-evidence stop**: inspect
suggested tables in order, and stop once the independently labeled pair is
visible. The two already-known positives appear in the first graph response;
the remaining three questions require all three follow-ups without gaining a
labeled link. Across five tasks, this idealized rule would use 14,488 response
characters instead of 16,382 for always reading all three graph responses, a
1,894-character reduction. The benchmark uses ground-truth labels to decide
when to stop; it does not demonstrate that an actual agent can make that
decision, nor does an empty neighborhood prove a relationship absent. Graphit
therefore keeps all three suggestions and only documents progressive use.

The scaled variant adds 100 unrelated `archive.record_*` tables with four
columns each to the same five-question fixture: 114 tables and 420 columns in
one saved snapshot. It keeps the original relationship labels and approved
decision unchanged, blocks source connections, and sends only task text to
the selector. The focus ranks stay 3/1/2/1/2 (top-3: 5/5); labeled graph hits
remain 2/4 positives and 0/1 negative exposed. The common `id` column causes
the 50-candidate-per-term search cap to be reported on two questions, without
displacing their focus tables. The full structural JSON baseline rises to
24,915 characters; selector plus all three suggested graph responses totals
3,542, 3,993, 3,198, 3,250, and 2,577 characters, each within its per-tool
response limit. The comparison is not equal-information compression: the
all-table dump contains many distractor columns, while focused responses omit
most unrelated structure and still miss the known synonym/ambiguous links.
These remain synthetic character counts, not actual agent token usage or task
success. Larger catalog sizes still need separate stress tests.

For the unchanged 114-table scaled fixture, the all-table baseline is **6,418
text tokens** using the same encoding. Selector plus three suggested graph
responses costs **905/1,082/824/821/660** text tokens per task. These
per-task counts are below the all-table dump, but the two sides do not contain
equal information: the dump includes every column, and the focused path still
misses labeled true links. Neither side includes user prompts, tool schemas,
message boundaries, agent reasoning, or follow-up SQL-tool calls. This is a
local plain-text comparison, not measured Codex/Claude billing or end-to-end
token savings. [OpenAI Docs' token-counting guide](https://developers.openai.com/api/docs/guides/token-counting)
explicitly distinguishes local tokenizer counts from exact model input counts.
With `max_followups=1`, the same five scaled tasks cost
**641/729/651/557/575** text tokens. Labeled link visibility stays 2/4
positives and 0/1 negative in this fixture, but that does not validate a
one-follow-up default across schemas or tasks.

An adversarial variant also adds 60 `archive.customer_invoice_*` tables (174
tables, 480 columns total). Before the exact-name tie-break, both
`sales.payment` and `sales.invoice` disappeared from the top three: focus
ranks were missing/1/2/missing/1, and only one of four labeled positive
links remained reachable through the suggested graph calls. After the
tie-break, ranks are 3/1/2/1/2 (top-3: 5/5), both previously surfaced
positive links are reachable again, and the labeled negative is still absent.
Four of five task selections flag the per-term candidate cap. This is a
lexical collision fix, not a resolution of missing synonyms or ambiguous
business identity. The `billing` focus moves from first to second because
`finance.account` exactly matches the task word `account`; both remain
available as distinct suggestions.

## Controlled agent evaluation

[`AGENT_EVALUATION.md`](AGENT_EVALUATION.md) specifies a first exact-FK task,
independent answer key, equal-fact Graphit-versus-full-packet arms, correctness
rubric, latency/token accounting, and cost/trust stop conditions. The offline
builder and tests check deterministic packet bytes, complete saved facts,
fail-closed truncation/omission, no source connection, and no overwrite. Its
`--scaled` mode reuses the 100-table archive corpus to create a 114-table,
420-column equal-facts packet. The canonical scaled packet is 51,256 UTF-8
bytes / 12,651 locally counted `o200k_base` proxy tokens with SHA-256
`af7d8d09825c4ec85e4e2be4cf9d3d61b8debafdb9ea9d7f21496edd065d525c`.
This prepares a crossover experiment; it is not actual-agent evidence.

The first valid authenticated Codex pair is preserved under
[`docs/evidence/agent-eval-2026-10-02/`](evidence/agent-eval-2026-10-02/README.md).
Both arms answered the exact-FK task correctly. Graphit returned 425 fact bytes
with one MCP call versus the baseline's complete 4,154-byte packet and two
shell calls, but used 54,957 client input tokens versus 45,314 and took 17.54
seconds versus 17.03. This single pair does not demonstrate token or latency
savings and is below the protocol's three-runs-per-arm minimum. No Claude or
Graphit-before-SQL-MCP task has been run. The older fixed structural dump still
lacks FK name/provenance and is not the equal-information baseline.

A follow-up model-free schema measurement serialized the same MCP tool models
that clients list. All 14 definitions total 7,765 UTF-8 bytes / 1,777 pinned
`o200k_base` tokens; the explicit Codex compact profile's eight core tools total
4,215 bytes / 976 tokens. This supports an opt-in allowlist experiment but does
not explain the full observed client-token difference or prove savings.

The first authenticated compact-profile pair is preserved under
[`docs/evidence/agent-eval-compact-2026-10-02/`](evidence/agent-eval-compact-2026-10-02/README.md).
Two further pairs and the aggregate are preserved under
[`docs/evidence/agent-eval-compact-repeats-2026-10-02/`](evidence/agent-eval-compact-repeats-2026-10-02/README.md).
All six arms were exact-correct. Across three matched pairs, compact Graphit
used one MCP call and returned 425 fact bytes per run; baseline used two shell
calls and read all 4,154 fact bytes. Compact averaged 49,748.7 total and
9,044.7 non-cached input tokens versus baseline's 46,014.0 and 5,992.7, and
17.81 versus 16.99 seconds. Thus compact was 8.1% higher in total input, 50.9%
higher in non-cached input, and 4.8% slower on this task. The minimum compact
sample is complete, but it does not establish token, billing, or latency
savings and does not justify changing the default.

The first 114-table authenticated matched pair is preserved under
[`docs/evidence/agent-eval-scaled-2026-10-02/`](evidence/agent-eval-scaled-2026-10-02/README.md).
Pairs 2 and 3 plus the aggregate are preserved under
[`docs/evidence/agent-eval-scaled-repeats-2026-10-02/`](evidence/agent-eval-scaled-repeats-2026-10-02/README.md).
All six arms were exact-correct. Compact Graphit used one MCP call and 425 fact
bytes per run; baseline read the complete 51,256-byte packet with two, two, and
three shell calls. Compact averaged 49,749.7 total / 8,619.0 non-cached input
tokens versus 59,315.0 / 14,259.0, reductions of 16.1% and 39.6%. Both token
measures were lower in all three pairs. Mean time was 16.54 versus 17.34
seconds, but Graphit was slower in two pairs and faster only where baseline
read the packet twice. The task-specific token crossover is repeat-supported;
latency, billing, production, SQL-MCP, and Claude savings are not.

## Performance tests

Measure scan, search, path, context selection, and export against synthetic
schemas of approximately 100, 1,000, and 5,000 tables. Enforce bounded response
sizes rather than relying only on latency.

## Required local gates

```text
pytest
ruff check
ruff format --check
mypy --strict
```

Integration and release gates expand as those slices are introduced.

The CI package job builds both distributions, runs `scripts/check_dist.py` to
reject unexpected/private archive members and verify the author, exact
Apache-2.0 `LICENSE`, exact `NOTICE`, license metadata, and CLI metadata, then
installs the wheel in a fresh virtual environment. Its installed
smoke creates a temporary Graphit project and a tiny saved FK/index snapshot without
connecting to PostgreSQL, verifies the SQLite schema, launches both
project-local Codex and Claude setup commands while preserving unrelated
settings, then uses each generated stdio command to verify the documented MCP
tool set, read-only annotations, and exact `get_column_impact`,
`get_transitive_impact`, and `get_index_context` results within 15 seconds.
It does not exercise the actual
Codex/Claude UI or prove that
machine-specific absolute paths survive moving the environment. This is a
local-artifact gate, not a PyPI publication or remote CI run verification.
