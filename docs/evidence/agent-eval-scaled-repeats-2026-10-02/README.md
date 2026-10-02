# Codex 114-table compact-tool repeated evidence — 2026-10-02

This directory preserves scaled pairs 2 and 3. Pair 1 is preserved in
[`../agent-eval-scaled-2026-10-02/`](../agent-eval-scaled-2026-10-02/README.md).
Together they meet the protocol minimum of three matched pairs. All database
facts are synthetic. Every run used authenticated Codex CLI 0.160.0, the same
default model setting, exact prompt hashes, canonical 51,256-byte packet
SHA-256 `af7d8d09825c4ec85e4e2be4cf9d3d61b8debafdb9ea9d7f21496edd065d525c`,
verified per-pair isolation, and the same eight-tool `enabled_tools` allowlist.
The resolved server-side model identifier was not emitted in JSONL.

## Per-pair results

| Pair | Order | Compact input | Baseline input | Difference | Compact time | Baseline time |
|---|---|---:|---:|---:|---:|---:|
| 1 | compact → baseline | 51,139 | 53,635 | -2,496 | 16.25 s | 15.59 s |
| 2 | baseline → compact | 49,052 | 55,713 | -6,661 | 18.33 s | 16.86 s |
| 3 | compact → baseline | 49,058 | 68,597 | -19,539 | 15.03 s | 19.58 s |

Every compact and baseline arm passed the exact rubric. Compact used one
`get_relationships` MCP call in every run. Baseline used two shell calls in
pairs 1 and 2; in pair 3 it read the complete packet twice and used three
calls. Graphit returned the same 425-byte fact context while baseline read the
complete 51,256-byte packet at least once.

## Aggregate

| Measure | Compact mean | Baseline mean | Compact minus baseline |
|---|---:|---:|---:|
| Exact success | 3/3 | 3/3 | equal |
| Total input tokens | 49,749.7 | 59,315.0 | -9,565.3 (-16.1%) |
| Cached input tokens | 41,130.7 | 45,056.0 | -3,925.3 |
| Non-cached input tokens | 8,619.0 | 14,259.0 | -5,640.0 (-39.6%) |
| Output tokens | 176.0 | 200.3 | -24.3 |
| Wall time | 16.54 s | 17.34 s | -0.81 s (-4.7%) |

Compact total and non-cached input were lower in all three pairs. This meets
the repeat threshold and demonstrates a token crossover for this exact
114-table synthetic context-delivery task. It does not establish billing
savings, a universal schema-size threshold, production behavior, a SQL-MCP
workflow, or Claude behavior.

Latency is not stable enough for a general speed claim: Graphit was slower in
pairs 1 and 2 and faster in pair 3. Pair 3's baseline read the 51,256-byte
packet twice, increasing both its tokens and time. That real agent behavior is
retained rather than dropped, but the mean latency advantage must not be
generalized.

Machine-readable run and aggregate values are in [`aggregate.json`](aggregate.json).
[`SHA256SUMS.txt`](SHA256SUMS.txt) covers the normalized repository evidence
files and aggregate present before the checksum file itself. Empty stderr
files are not included; all four repeat stderr logs were verified as zero
bytes.
