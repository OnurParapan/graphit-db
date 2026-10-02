# Codex compact-tool exact-FK pilot evidence — 2026-10-02

This directory preserves one valid matched pair evaluating Graphit's explicit
eight-tool Codex allowlist. All database facts are synthetic. The runs used
authenticated Codex CLI 0.160.0 in separate Ubuntu WSL2 workspaces under the
same verified `bubblewrap` profile. The CLI default model was used in both
arms; its resolved server-side identifier was not emitted in JSONL.

The baseline packet SHA-256 remained
`f4de55401ea7d4190cafc535f7c32e2bfe55f315de4a8dd9bafef584991f8260`.
Prompts were byte-identical to the corresponding prompts in the earlier valid
full-profile pair. The order was reversed: baseline first, compact Graphit
second. Neither arm could read the repository, live database, or sibling arm.

The compact arm configured Codex's `enabled_tools` with exactly:

```text
database_overview
get_relevant_context
search_objects
get_table
get_view
get_relationships
get_graph_context
find_path
```

## Result

| Measure | Compact Graphit | Full-facts baseline |
|---|---:|---:|
| Exact rubric success | yes | yes |
| Context calls | 1 MCP call | 2 shell calls |
| Returned fact context | 425 UTF-8 bytes | 4,154 UTF-8 bytes |
| Wall time | 19.24 s | 16.36 s |
| Client input tokens | 49,056 | 47,401 |
| Cached input tokens | 43,520 | 39,808 |
| Non-cached input tokens | 5,536 | 7,593 |
| Output tokens | 184 | 175 |
| Reasoning-output tokens | 0 | 0 |

Both answers correctly reported `payment_customer_fk`, the
`sales.payment.customer_id` → `crm.customer.id` ordered pair, target-in-scope,
and no declared FK to `legacy.customer.id`.

Compact Graphit returned 89.8% fewer fact bytes and used 2,057 fewer non-cached
input tokens, but its client-reported **total** input remained 1,655 tokens
(3.5%) above baseline and wall time was 2.88 seconds (17.6%) higher. Cached and
non-cached token categories can have different billing treatment, so these
counts are not converted into a cost claim.

Compared across separate pairs, compact Graphit's 49,056 input tokens are
5,901 (10.7%) below the earlier full-profile Graphit run's 54,957, and the
Graphit-minus-baseline gap fell from 9,643 to 1,655 tokens. However, the
baseline itself varied from 45,314 to 47,401 tokens between pairs. This is
directional evidence that filtering tool definitions helps, not a controlled
full-versus-compact ablation or proof of causality. One compact pair is not
enough to change the default or claim general savings.

## Repository-copy SHA-256

```text
255eacafdfdf5bdcc09eb25c595970a05bcd45b45f150223e4112ab80dda290a  baseline/final.txt
22ae53ad329f5fb8398e1e93df62621143e0928ead5879ccecd4c729e05b0ea0  baseline/prompt.txt
ff2096680ceca8525e899c5d3b3b8e716f39da4834a564e5824b2b00f7ba84aa  baseline/timing.txt
cf0453cc555ef79c89530435e289a1fbe5763411b4a45ab9f55ade3e4ee40dfc  baseline/transcript.jsonl
ae210c4fa8a23d84145dcc03739379c9bcb0bcfb7515bc341e02a08f1416e680  compact/final.txt
6154cb80ad2ea312fd7c182804ac031b95e4e6bbd6b91393a33d62800b42df77  compact/prompt.txt
f803b9cae2ca3fefa81ddc0dc08ff6d7ff7713171019690a73873d13ce04e4a1  compact/timing.txt
1d3e09712c6256ac3d9cbd877239b7e00efaef05520af70c63cca1a951e230d8  compact/transcript.jsonl
```
