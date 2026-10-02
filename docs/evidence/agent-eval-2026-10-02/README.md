# Codex exact-FK pilot evidence — 2026-10-02

This directory preserves the first valid matched Graphit-versus-full-facts
Codex pair. All database facts are synthetic. The runs used authenticated
Codex CLI 0.160.0 in separate Ubuntu WSL2 workspaces under the same verified
`bubblewrap` permission profile. The CLI's default model was used for both
arms; the JSONL event format did not emit the resolved server-side model
identifier, which is a protocol limitation.

The shared task text was byte-identical between prompts. Arm-specific text
only identified the allowed context source. The full-facts packet SHA-256 was
`f4de55401ea7d4190cafc535f7c32e2bfe55f315de4a8dd9bafef584991f8260`.
Neither arm could read the repository, live database, or sibling workspace.

## Result

| Measure | Graphit arm | Full-facts arm |
|---|---:|---:|
| Exact rubric success | yes | yes |
| Context calls | 1 MCP call | 2 shell calls |
| Returned fact context | 425 UTF-8 bytes | 4,154 UTF-8 bytes |
| Wall time | 17.54 s | 17.03 s |
| Client input tokens | 54,957 | 45,314 |
| Cached input tokens | 46,976 | 41,216 |
| Non-cached input tokens | 7,981 | 4,098 |
| Output tokens | 177 | 182 |
| Reasoning-output tokens | 0 | 0 |

Graphit called only `get_relationships` and returned about 89.8% fewer fact
bytes than the complete packet. Despite that, its client-reported input was
9,643 tokens (21.3%) higher and it was 0.51 seconds (3.0%) slower. One likely
explanation is the fixed schema/context cost of exposing Graphit's 14 MCP
tools, but this run does not isolate that cause. It establishes **no token or
latency saving** on this small 14-table task.

Both answers correctly reported `payment_customer_fk`, the
`sales.payment.customer_id` → `crm.customer.id` ordered pair, target-in-scope,
and no declared FK to `legacy.customer.id`. This is one sample per arm; the
protocol requires at least three before an aggregate comparison.

## Repository-copy SHA-256

These hashes cover the text copies in this directory after the workspace's
Windows line-ending normalization.

```text
cb4bd2bdf2128716dae6cc2f2d1e86a5b3be26af9627fafc91f37e0853242b5f  baseline/final.txt
c49245c9dcbf6cff712ae4c9b48e75316ddff63d2c703662310a0e41fa6c9605  baseline/prompt.txt
85f4e4d6b7511dae96869ab41895bbdbd1618fdae7cfd2b3fe8666793ea88cc4  baseline/timing.txt
a7214049659e152fb2215b844b12c6e0a15c0ef7e57e64d1f09d22199fa346c4  baseline/transcript.jsonl
39ec78a253ac738d67706f0d407be8ecc4522473b611952f85edc8c63209f7c5  graphit/final.txt
8d3f67a43f02cd42c152904ac79343476db33b89ccabf568c06db690d30d667b  graphit/prompt.txt
44acc889b88749a71002563e541cd1eabe754f6e88f5f084cafd873bfe9ab0a2  graphit/timing.txt
7954707a64446fc93749bd23f22753dec649f7dcb966566370c8506939efa614  graphit/transcript.jsonl
```

The earlier shell-corrupted baseline and output-capture-failed Graphit attempts
are documented in `docs/AGENT_EVALUATION.md`; they are not silently mixed into
this valid pair.
