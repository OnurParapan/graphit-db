# Graphit — Relationship Discovery

## Objective

Discover declared and likely logical relationships while protecting user trust.
Precision is more important than recall.

## Explicit relationships

A source database foreign key creates a confirmed edge:

```text
origin = DATABASE
status = CONFIRMED
confidence = 1.0
edge_type = REFERENCES
```

Composite foreign keys retain ordered column-pair metadata. Self-references are
supported.

## Inferred relationships

An undeclared candidate creates a hypothesis:

```text
origin = INFERRED
status = PENDING
edge_type = LIKELY_REFERENCES
confidence = deterministic score
```

It is never labeled as an actual FK.

## Metadata-only candidate preview (implemented)

`graphit candidates --source NAME` reads only the latest completed local
snapshot and returns ephemeral hypotheses; it writes no `LIKELY_REFERENCES`
edges or review decisions. The narrow metadata rule requires a source column ending
in `_id`, an in-scope target table whose name exactly matches the prefix, a
target column named `id` with a declared single-column primary/unique key,
and the same known PostgreSQL key type on both columns (`smallint`, `integer`,
`bigint`, or `uuid`, including `int2`/`int4`/`int8` aliases). It does not infer
plural forms, abbreviations, unknown/domain types, or self-table links.
Metadata rule v2 suppresses **every** inferred target for a source column
covered by a confirmed declared database FK, not just the exact declared
pair. It reads ordered source-column pairs from table-level FK metadata, so
single-column and composite FKs are both covered even when the referenced
table is outside scan scope. Other columns on the same table remain eligible.
Metadata rule v3 abstains by default when two or three compatible targets
match the same source column; it does not choose the same-schema target solely
because of a small score preference. `--include-ambiguous` explicitly shows
all alternatives for inspection (up to three), preserving any human review
status. Unreviewed alternatives remain `PENDING`. More than
three alternatives are skipped in either mode. Skipped source columns are
counted in `skipped_ambiguous_columns`.

The metadata-only score is intentionally provisional, not a calibrated
probability: exact table stem contributes `0.30`, matching key type `0.20`,
single-column target key `0.25`, and same schema `0.05`. Thus matching
cross-schema candidates score `0.75`, same-schema candidates `0.80`; the
remaining `0.20` is not awarded without value/cardinality evidence. Every
candidate carries those four inspectable evidence components, `origin:
INFERRED`, a `PENDING` or human-`APPROVED` status, and an alternatives count.
Approved pairs sort first, then by score and exact source/target names. The
preview reads at most 2,000
tables, 20,000 columns, and 20,000 declared table FKs, evaluates at most
2,000 candidate pairs, and returns
at most 50 results; larger work fails explicitly rather than claiming an
exhaustive result. This is a precision-first baseline, not full inference.

## Fixed synthetic ERP baseline

`tests/test_inference_benchmark.py` defines a deterministic, hand-labeled
challenge set with eight undeclared logical relationships, four labeled
non-relationships, and one separately declared FK control. It includes
duplicate `customer` table names across schemas, an external account ID,
business synonyms, a non-`id` unique key, a self-reference, and a type
mismatch. The benchmark calls the real snapshot-backed preview with `limit=50`
and asserts that output is complete and stable across repeated calls.
Its metric baseline contains no human review decisions.

| Measure | v1 | v2 | v3 default |
|---|---:|---:|---:|
| Candidate pairs | 7 | 6 | 4 |
| True positives | 4 | 4 | 3 |
| False positives | 3 | 2 | 1 |
| False negatives | 4 | 4 | 5 |
| Precision | 4/7 = 57.1% | 4/6 = 66.7% | 3/4 = 75.0% |
| Recall | 4/8 = 50.0% | 4/8 = 50.0% | 3/8 = 37.5% |

The sole `0.80` (same-schema) candidate is correct in all three defaults. At
`0.75` (cross-schema), v1 had three correct of six; v2 three of five; v3 two
of three. The duplicate-customer source is omitted in v3 despite having a
genuine CRM target: this eliminates its legacy false positive but loses its
true positive. Explicit `--include-ambiguous` restores the v2 set of six
candidates (4 TP, 2 FP) for human inspection.
These tiny synthetic bands do **not**
calibrate the score as a real-world probability or establish production
precision. True negatives and overall accuracy are not reported because the
fixture does not label every possible column pair.

Two misses require domain synonyms (`client`/`customer`, `vendor`/`supplier`),
one requires a non-`id` business key (`product_sku`), and one is a self-FK-like
manager relationship. V3's remaining false positive is an external billing
account ID with a matching local name/type; metadata alone cannot distinguish
that external identity. V2's other false positive was the alternative legacy
customer. V2 removed v1's third false positive: a legacy customer
suggestion from a column that already had a declared FK to the CRM customer.
Do not expose these preview candidates to agents as facts.

### External identifiers: current evidence limit

`billing.invoice.account_id` and `finance.account.id` satisfy the same
name/type/key checks as a genuine local relationship, but the fixture labels
the billing ID as belonging to an external system. No structural metadata in
the current snapshot distinguishes those interpretations. The regression
test confirms that the candidate remains `INFERRED`/`PENDING` and that preview
never opens a source connection. Its `0.75` score is a ranking aid, not a
75% probability or permission to suggest a join as fact.

Do not add an automatic source-table probe to resolve this case. `LIMIT` caps
returned rows, not necessarily rows read by PostgreSQL; `COUNT(DISTINCT)` or
an overlap join can impose large work even in a read-only transaction. A future
opt-in evidence mode needs an explicit table/column scope, a demonstrably
bounded access path (or a separate precomputed aggregate), short timeout,
hard query and result budgets, no raw-value persistence or logging, permission
failure handling, and live PostgreSQL tests. Value overlap can support a
hypothesis but cannot by itself prove business identity. Until then, an
explicit human review decision is safer than a silent inference upgrade.

## Candidate funnel

Avoid all-pairs comparison. Apply cheap filters before expensive evidence:

1. compatible type family,
2. plausible source column,
3. target PK, unique key, or high uniqueness,
4. exact/canonical name similarity,
5. schema and naming-convention preference,
6. ambiguity cap per source column,
7. bounded sampling only for surviving candidates.

Large text, binary, generated, and clearly non-key columns are excluded by
default.

## Identifier normalization

Preserve raw identifiers and derive searchable forms:

```text
customer_id → customer
customerid  → customer
CustomerID  → customer
policy_no   → policy_no
```

Normalization lowercases, splits camel case, normalizes separators, and removes
well-understood ID suffixes conservatively. Abbreviation expansion is explicit
configuration, not an implicit guess.

## Evidence

- Raw and normalized name similarity.
- Type-family compatibility.
- Target primary/unique key quality.
- Bounded distinct value overlap.
- Source/target cardinality fit.
- Nullability fit.
- Joins found in supported view definitions later.

Every evidence calculator returns a normalized score plus inspectable details.

## Planned enhanced scoring model

```text
confidence =
    0.25 × name
  + 0.15 × type
  + 0.15 × target_key
  + 0.30 × bounded_overlap
  + 0.10 × cardinality
  + 0.05 × nullability
```

This sampling-based model is not implemented by the metadata-only preview.
Its weights will be configuration, versioned with the scan, and covered by tests.

Planned presentation bands:

```text
HIGH    >= 0.90
MEDIUM  >= 0.75
LOW     >= 0.60
DISCARD <  0.60
```

Only high and medium candidates appear by default.

## Sampling safety

- Disabled in metadata-only mode.
- Read-only source connection.
- Strict statement timeout.
- Configurable maximum sample size; a SQL `LIMIT` alone does not bound scan work.
- No requested locks.
- No raw sampled values persisted.
- No unbounded `COUNT(DISTINCT ...)` on large tables without safe estimates.

## Ambiguity

When a source matches multiple targets, the default preview abstains. An
explicit diagnostic option returns bounded alternatives together instead of
auto-selecting one. Agent context does not currently consume candidates.

## Review lifecycle

A separate manual-proposal lane allows a user to record a cross-name pair
(for example `client_id` to `customer.id`) with a reason and current snapshot
version. It checks both saved columns, exact stored type equality, a
single-column target key, and absence of a declared FK on the source column.
Manual events remain invisible to candidate preview. Only currently
eligible `APPROVED` pairs can enter local graph and MCP graph context; this
does not assert a database FK or change the inferred-candidate lifecycle below.
`graphit review proposals` now provides a source-scoped, bounded local list
with the latest human reason and current-snapshot structural eligibility.
An ineligible old proposal remains visible in review history, not as a graph edge.
`graphit review approve-proposal` now appends `APPROVED` to an exact existing,
currently eligible manual proposal while preserving its reason. The approval
is idempotent, source-scoped, and separate from the inferred review lane.
The local graph and MCP graph context show it as `MANUAL`/`APPROVED` with the
human reason. Approval never creates a database FK.
`graphit review revoke-proposal` appends a `REVOKED` manual event while
retaining the last reason and earlier approval. It works even if a rescan
made the pair ineligible. Repeated revocation is a no-op; an explicit new
proposal is required before a second approval. Manual revocation does not
alter inferred reviews or declared FKs. It removes the local manual graph
link immediately from both local graph and MCP graph context.

```text
PENDING → REJECTED → RESTORED (eligible for PENDING preview again)
        → APPROVED → REVOKED (eligible for PENDING preview again)
        → SUPPRESSED (planned)
```

Approved means a user confirmed a logical relationship; it still does not mean
the source database contains an FK. Suppressed candidates are not regenerated
until restored or the inference version materially changes.

The first implemented review action is `graphit review reject`. It accepts one
exact current candidate pair and the snapshot version shown by preview. The
decision is stored separately from immutable snapshots, keyed by source alias
and exact quoted source/target column names. It suppresses that pair in future
previews, including after a rescan; another source's identical column names
are unaffected. Repeating a rejection makes no new row. A rescan between
validation and commit fails rather than attaching a stale decision. Rejection
does not create or modify graph edges. `graphit review restore` appends a
`RESTORED` event for an already rejected exact pair. Preview uses the latest
decision: if the pair remains eligible, it returns as `PENDING`; if schema
changes removed the pair, the rejection is still cleared without inventing
an edge. Repeated restore is a no-op. `RESTORED` is not a relationship status.
`graphit review approve` records an exact current hypothesis as `APPROVED` in
the local review history. Preview still labels its origin `INFERRED` and keeps
its metadata confidence unchanged. One approved target can be shown by default
amid otherwise ambiguous alternatives; diagnostic mode still shows the other
`PENDING` alternatives. Approval does not create a database FK or expose the
pair through MCP's confirmed-FK tools. An earlier rejection must be restored
before approval; a changed schema can make an approval temporarily ineligible
for preview without erasing review history. `graphit review revoke` appends a
`REVOKED` event for a source-scoped approval. If the pair is still eligible,
preview shows it as `PENDING` and reapplies default ambiguity abstention; if
not, no candidate is invented. Repeating revoke is a no-op, and a revoked
current candidate can be approved or rejected again. Approval history remains
intact. `REVOKED` is not a relationship status or FK fact.

## Explainability example

```text
public.claim.customer_id → public.customer.id
confidence: 0.94
status: PENDING

name similarity       1.00
type compatibility    1.00
target uniqueness     1.00
sampled overlap       0.91
cardinality fit       0.96
```
