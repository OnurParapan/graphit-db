# Codex compact-tool repeated exact-FK evidence — 2026-10-02

This directory preserves compact-profile pairs 2 and 3. Pair 1 is preserved in
[`../agent-eval-compact-2026-10-02/`](../agent-eval-compact-2026-10-02/README.md).
Together they satisfy the protocol's minimum of three matched compact pairs.
All facts are synthetic. Every run used Codex CLI 0.160.0, the same default
model setting, exact prompt hashes, packet SHA-256
`f4de55401ea7d4190cafc535f7c32e2bfe55f315de4a8dd9bafef584991f8260`,
verified per-arm isolation, and the same eight-tool `enabled_tools` allowlist.
The resolved server-side model identifier was not emitted in JSONL.

## Per-pair results

| Pair | Order | Compact input | Baseline input | Difference | Compact time | Baseline time |
|---|---|---:|---:|---:|---:|---:|
| 1 | baseline → compact | 49,056 | 47,401 | +1,655 | 19.24 s | 16.36 s |
| 2 | compact → baseline | 51,146 | 45,327 | +5,819 | 16.62 s | 17.19 s |
| 3 | baseline → compact | 49,044 | 45,314 | +3,730 | 17.57 s | 17.41 s |

Every compact and baseline arm passed the exact rubric. Compact used one
`get_relationships` MCP call in every run; baseline used two shell calls in
every run. Graphit returned the same 425-byte fact context versus the complete
4,154-byte baseline packet.

## Aggregate

| Measure | Compact mean | Baseline mean | Compact minus baseline |
|---|---:|---:|---:|
| Exact success | 3/3 | 3/3 | equal |
| Total input tokens | 49,748.7 | 46,014.0 | +3,734.7 (+8.1%) |
| Cached input tokens | 40,704.0 | 40,021.3 | +682.7 |
| Non-cached input tokens | 9,044.7 | 5,992.7 | +3,052.0 (+50.9%) |
| Output tokens | 185.0 | 177.3 | +7.7 |
| Wall time | 17.81 s | 16.99 s | +0.82 s (+4.8%) |

Compact total input was higher in all three pairs. Compact latency was higher
in two pairs and lower in one. Pair 1's non-cached advantage did not replicate;
compact non-cached input was higher on aggregate. Therefore the eight-tool
profile preserves correctness and materially shrinks returned facts, but it
does **not** demonstrate token or latency savings on this 14-table task.

The full-profile result and compact cross-pair reduction remain useful design
evidence, but they are not combined into this compact aggregate. A larger
schema may have a different crossover point; that requires a separately
specified experiment rather than extrapolation.

Machine-readable run and aggregate values are in [`aggregate.json`](aggregate.json).
[`SHA256SUMS.txt`](SHA256SUMS.txt) covers the normalized repository evidence
files and aggregate present before the checksum file itself.

