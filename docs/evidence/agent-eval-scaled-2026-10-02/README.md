# Codex 114-table compact-tool exact-FK evidence — 2026-10-02

This directory preserves the first valid matched pair for the separately
specified scaled crossover experiment. All facts are synthetic. Both arms used
authenticated Codex CLI 0.160.0, the same default model setting, fresh Ubuntu
WSL2 workspaces, the named `bubblewrap` permission profile, and opposite-arm
filesystem denial. The resolved server-side model identifier was not emitted
in JSONL.

The task and prompts are unchanged from the 14-table compact experiment. The
scaled snapshot adds 100 unrelated `archive.record_*` tables: 114 tables and
420 columns total, with the original single declared FK unchanged. The complete
baseline packet was 51,256 UTF-8 bytes with SHA-256
`af7d8d09825c4ec85e4e2be4cf9d3d61b8debafdb9ea9d7f21496edd065d525c`.
Codex accepted Graphit's exact eight-tool `enabled_tools` allowlist before the
run. Order was compact Graphit first, full-facts baseline second.

## Result

| Measure | Compact Graphit | Full-facts baseline | Difference |
|---|---:|---:|---:|
| Exact rubric success | yes | yes | equal |
| Context calls | 1 MCP call | 2 shell calls | — |
| Returned/read fact context | 425 bytes | 51,256 bytes | -99.2% |
| Client input tokens | 51,139 | 53,635 | -2,496 (-4.7%) |
| Cached input tokens | 45,056 | 39,040 | +6,016 |
| Non-cached input tokens | 6,083 | 14,595 | -8,512 (-58.3%) |
| Output tokens | 174 | 182 | -8 |
| Reasoning-output tokens | 0 | 0 | equal |
| Wall time | 16.25 s | 15.59 s | +0.66 s (+4.2%) |

Both answers correctly reported `payment_customer_fk`, the ordered
`sales.payment.customer_id` → `crm.customer.id` mapping, target-in-scope, and
no declared FK to `legacy.customer.id`. Compact Graphit used only
`get_relationships`; baseline located and read all of `baseline.json`.

This first scaled pair shows the expected crossover direction for total and
non-cached input tokens, while Graphit remained slightly slower. It is one pair,
not an aggregate, billing claim, production result, SQL-MCP comparison, or
evidence about Claude. At least two more fresh matched pairs with alternating
order are required before reporting a scaled aggregate. Every further model
call requires fresh explicit approval.

[`SHA256SUMS.txt`](SHA256SUMS.txt) covers the normalized repository evidence
copies present before the checksum file itself. Empty stderr files are not
included; both source stderr logs were verified as zero bytes.
