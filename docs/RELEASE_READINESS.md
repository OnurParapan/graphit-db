# Graphit — MVP and release readiness

This is an evidence ledger, not a claim that the product is finished. Last
reviewed 2026-10-02. "Verified locally" means the implemented behavior and
tests were exercised in this workspace; it does not mean a public user has
completed the workflow.
The full local test suite passed with the already-cached `tiktoken` encoding
selected explicitly; the opt-in disposable PostgreSQL test passed locally,
with four environment-dependent tests skipped in the latest offline suite.
Without that cache setting, two benchmark tests fail while attempting a
blocked first-time network fetch, not while querying Graphit.

| Goal | Current evidence | Remaining gap |
|---|---|---|
| Install and initialize | `graphit-db` wheel/sdist build and archive checks pass; package metadata names Onur Parapan as author, the exact `LICENSE` and `NOTICE` files are present, and a clean-wheel smoke initializes project-local SQLite. | Not published on PyPI; `pipx install graphit-db` and `uv tool install graphit-db` cannot be claimed as public install paths yet. |
| Scan PostgreSQL safely | Source configuration, read-only connection test, bounded catalog scanner, immutable snapshots, and local unit tests exist. A disposable local PostgreSQL 16 run verified composite expression/INCLUDE/partial index scanning, saved index facts, and CLI/MCP lookup. | The local fixture is small and covers PostgreSQL 16 only; remote CI and broad-version/scale results have not been observed here. |
| Query useful structure | CLI search, table/view/index detail, declared FK relationships/paths, local history/diff, direct table/column impact, and 14 bounded read-only MCP tools have tests. Bounded transitive declared-FK table impact is exposed through CLI and MCP. | Transitive FK reachability is a structural hint, not application lineage. View definitions and application lineage are not available. |
| Connect agents | Project-local Codex/Claude setup and generated stdio launchers pass a clean-wheel MCP tool-list, saved-FK call, and saved-index call. Authenticated Codex 0.160.0 runs in WSL2 with verified per-arm isolation. Full and compact Codex tasks each used `get_relationships` once and answered every FK requirement correctly. | No Claude executable was found on `PATH`; Claude and a real installed-user workflow remain unverified. |
| Discover logical relations | Conservative metadata-only candidate evidence, human review, and approved graph context are implemented. | A tiny synthetic ERP fixture yields 3 true positives, 1 false positive, and 5 false negatives by default; this is not production precision. No automatic business-identity proof. |
| Explore a graph | Bounded JSON, DOT, and offline HTML one-hop exports are implemented. HTML has local search and relationship-type filters, an exact-hash CSP script, escaped metadata, and accessible no-script lists. | Multi-hop depth controls, expand/collapse, and richer layout are not implemented. |
| Reduce agent cost | Deterministic progressive context and response ceilings exist. The three-pair 14-table compact aggregate and separate three-pair 114-table aggregate are preserved; all 12 arms were correct. On the scaled task, Graphit returned 99.2% fewer fact bytes and averaged 16.1% lower total / 39.6% lower non-cached input, with both lower in all three pairs. | The small aggregate remained negative, proving workload size matters. Scaled latency was inconsistent, pair 3 baseline read twice, and no billing, production, SQL-MCP, Claude, or controlled profile-ablation result exists; do not claim universal savings. |

## Public release gates

Completed locally: the user confirmed Onur Parapan as the copyright holder.
The repository and built artifacts now carry that attribution through project
author metadata and `NOTICE`, alongside the canonical Apache-2.0 `LICENSE`.
The distribution checker and installed-wheel smoke both pass.

1. Run and inspect a remote CI job across Python 3.11–3.14, including its
   disposable PostgreSQL service and isolated-wheel job. `origin` now points
   to the empty `OnurParapan/graphit-db` repository, but no commit has been
   pushed and a workflow file alone is not evidence of a run.
2. Exercise an installed Graphit release in actual Codex and Claude projects:
   initialize, configure a read-only disposable PostgreSQL source, scan, trust
   or approve the MCP server, and complete a representative task. Record
   success, failure, latency, and context/token costs against a fair baseline.
3. The cold-backup/restore and pre-upgrade procedure is documented in
   `OPERATING_MODEL.md`. Test an upgrade path from a previous released version
   when one exists; there is no prior public release yet.
4. Confirm distribution-name ownership/availability at publication time,
   build and inspect release artifacts, and publish checksums/provenance.
   No upload or name reservation has been performed here.

The opt-in MCP index lookup reuses the shared local query and passed the
disposable PostgreSQL 16 fixture end to end. This validates the fixture's
index path, not general PostgreSQL version/scale breadth. Agent task
validation and public release remain separate external gates.
