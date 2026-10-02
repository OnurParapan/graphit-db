# Graphit — controlled agent evaluation protocol

This protocol isolates whether an agent can find exact saved database facts
with less context and time when Graphit is available. The initial authenticated
attempts produced no valid comparison; the replacement pair below is valid but
shows no saving on the small fixture. The protocol does not test Graphit-before-
SQL-MCP behavior, live application SQL, or production adoption.

## Current local capability

On 2026-10-02, an authenticated standalone Codex CLI 0.160.0 was available in
Ubuntu WSL2 with a verified `bubblewrap` filesystem boundary. No `claude`
executable was found on `PATH`. Codex was invoked for the first two pilot
attempts recorded below, but they do not form a valid pair. The existing
installed-wheel smoke proves that generated Codex/Claude stdio commands work
with an MCP client; it does not by itself prove agent trust, tool choice, or
task success.

## First task and independent answer key

Use the repository's synthetic 14-table ERP fixture in
`tests/test_inference_benchmark.py`, seeded by `_save_fixture` with no human
review decisions. Its one database-declared FK is independent of name-based
inference and is saved in the immutable snapshot. Give both arms this exact
task, without the answer key:

> In the saved `erp` schema, identify the database-declared relationship from
> `sales.payment.customer_id`. Report its constraint name, source and target
> tables, ordered column pair, and whether the target is in scan scope. Does
> that declared FK also point to `legacy.customer.id`? Do not infer a second
> relationship from matching names.

Score against the fixture definition, which must remain hidden from the
agent: `payment_customer_fk` maps `sales.payment.customer_id` to
`crm.customer.id`, pair position 1, target in scope, database-declared and
confirmed. No declared FK maps the same column to `legacy.customer.id`.
Answers must not turn the absent legacy FK into a claim about all possible
business relationships. Exact constraint name, both endpoints, pair order,
scope, and the negative *declared-FK* answer are required for task success.

## Two arms with the same available facts

Run each arm in a fresh, disposable project and conversation using the same
saved snapshot, model/version, task wording, time budget, and final-answer
format. No live source connection, web search, SQL execution tool, repository
source files, test labels, or cross-run memory is available to either agent.

1. **Graphit arm:** provide only the project-local Graphit stdio MCP server
   and the source name `erp`. Let the agent choose among its read-only tools;
   record whether it uses `get_relevant_context`, `get_table`,
   `get_relationships`, or other calls. Do not preselect the focus table or
   inject tool responses into the prompt.
2. **Full-facts arm:** provide no Graphit tools. Supply a single deterministic
   JSON packet containing *all* 14 tables' saved columns, declared keys, and
   confirmed FK relationships from the identical snapshot. FK entries must
   carry the same name, ordered pairs, target scope, validation/inheritance,
   origin, and status available to the Graphit arm. Include source and
   snapshot version. Reject the packet if any list is truncated or a field
   needed for scoring is absent. Build it offline before either run; never
   let the baseline agent inspect Graphit's implementation or answer key.

This is a context-delivery comparison, not a Graphit-versus-SQL-MCP test. The
existing `_full_structural_baseline` benchmark is **not** this packet: it
omits the FK constraint name and provenance, so its 618-token figure cannot
be reused as an equal-information baseline. A scaled rerun may add the
existing 100 unrelated archive tables, but must preserve the same task facts
and apply the same completeness checks.

### Offline bundle preparation (implemented)

From the repository root, with development dependencies installed:

```powershell
.\.venv\Scripts\python.exe -m scripts.prepare_agent_eval .pytest-tmp\agent-eval-01
```

The output path must not already exist. The script creates
`graphit-project/` with the synthetic saved snapshot and
`baseline-arm/baseline.json` with all 14 tables' columns, keys, and confirmed
FK facts. It prints the baseline packet's SHA-256. The JSON is stable across
fresh runs, includes the FK name and provenance omitted by the older benchmark,
and contains no separate answer-key file. Missing counts, mismatched facts,
or truncated local queries abort preparation; an interrupted run can leave an
incomplete output directory, which is never overwritten automatically. The
script does not connect to PostgreSQL or invoke a model. For actual runs,
expose **only the relevant arm's directory and tools** to each agent; do not
grant either agent filesystem access to this repository, the sibling arm, or
the hidden scoring fixture.

For the separately specified larger-schema crossover experiment, add the
existing 100 unrelated archive tables:

```powershell
.\.venv\Scripts\python.exe -m scripts.prepare_agent_eval .pytest-tmp\agent-eval-scaled --scaled
```

This produces 114 tables and 420 columns while preserving the task, answer
key, and single declared FK. The deterministic full-facts packet is 51,256
UTF-8 bytes with SHA-256
`af7d8d09825c4ec85e4e2be4cf9d3d61b8debafdb9ea9d7f21496edd065d525c`;
its model-free `o200k_base` proxy is 12,651 tokens when the pinned local
encoding cache is used. The hash describes the current canonical builder
output, not a permanently compatible public format. The same compact Graphit
profile, prompt, rubric, isolation, alternating order, and at-least-three-pair
rule apply. Small- and scaled-fixture runs must be reported separately. No
scaled model arm has been authorized or run.

## Measurements and stopping rules

- Repeat each arm at least three times with a fresh conversation and alternate
  arm order; retain model/version, client version, snapshot identifier, task
  text, and tool configuration for each run.
- Record exact-match task success and specific omissions/hallucinations, wall
  time from task submission to final answer, number/order of tool calls, and
  whether the agent queried unrelated tables. Timeouts and tool errors are
  failures, not dropped samples.
- Prefer client-reported total input/output tokens and cost, including tool
  schemas, prompts, tool responses, and retries. If unavailable, report only
  separately labeled `o200k_base` counts of visible text; do not call them
  billed tokens or end-to-end savings.
- Compare cost/latency only for runs that meet the same correctness rubric;
  also report the full success rate. A shorter incorrect answer is not a win.
- Preserve raw prompts, redacted transcripts, result rubric, and aggregate
  numbers without credentials or private database values. Stop before model
  calls if the user has not approved any possible API/subscription cost.
- For command-line pilots, save the exact UTF-8 prompt inside the isolated arm,
  checksum it, and pass it through stdin with `codex exec ... -`. Redirect
  `--json` stdout and stderr to arm-local files instead of returning the event
  stream through an enclosing command capture. This prevents shell expansion
  of prompt punctuation and preserves long transcripts even when the caller's
  display context is bounded.

The small and 114-table offline fixture/packet builders are implemented and
tested. Actual Codex/Claude model runs require explicit cost/trust approval;
Claude also needs an
installed client or another user-approved route. A separate later experiment
must compare Graphit-first and SQL-MCP-only workflows on the same read-only
database task before claiming Graphit helps agents use SQL tools.

### First local pilot attempt (2026-09-17)

The user approved one Codex pilot per arm. A fresh synthetic bundle was created
outside the repository; its baseline SHA-256 was
`f4de55401ea7d4190cafc535f7c32e2bfe55f315de4a8dd9bafef584991f8260`.
No model task was started. Before a run, a model-free `codex sandbox` probe
using a workspace-only permission profile did not complete; a second probe
with built-in `:read-only` also remained silent for 30 seconds. Both were
interrupted. Since neither probe established native-Windows filesystem
isolation, this is a blocked pilot, **not** a failed Graphit retrieval or a
measured agent comparison. Recheck the Codex sandbox in a suitable environment
before launching either arm; do not weaken the no-repository/no-sibling rule
just to obtain a result.

Follow-up diagnosis: a model-free `unelevated` sandbox echo succeeds when run
outside the enclosing workspace sandbox, but Codex rejects the required
deny-other-files read profile with `Restricted read-only access requires the
elevated Windows sandbox backend`. The existing Ubuntu WSL environment has
Python but no Codex CLI or `bwrap`; no installation or authentication transfer
was attempted. The pilot remains paused until a supported strict sandbox is
available. This is an environment limitation, not a Graphit test result.

On 2026-10-02, after explicit user approval, Ubuntu WSL2 received the official
standalone Codex CLI 0.160.0 and Ubuntu's `bubblewrap` 0.9.0 package. No
credential was copied from Windows. A temporary named permission profile can
read its own synthetic workspace while a sibling marker is invisible inside
the sandbox, establishing the required filesystem boundary. Device-code login
completed successfully without copying the Windows credential.

### First authenticated pilot execution (2026-10-02)

The fresh bundle used baseline SHA-256
`f4de55401ea7d4190cafc535f7c32e2bfe55f315de4a8dd9bafef584991f8260`.
Each arm could read its own workspace and could not read the sibling arm. The
Graphit arm used a fresh `graphit-db 0.1.0` wheel installed in a dedicated WSL
virtual environment. Neither arm had a live database or repository access.

The baseline arm returned the correct constraint, endpoints, ordered pair,
scope, and negative legacy-FK answer after one read of `baseline.json`. The
client reported 30,474 input tokens, including 26,624 cached input tokens, 137
output tokens, and 0 reasoning-output tokens; observed wall time was 16.49
seconds. This run is **protocol-invalid**, not a successful baseline sample:
PowerShell interpreted Markdown backticks in the command-line prompt before
the text reached WSL, attempted to execute the three quoted identifiers, and
removed them from the actual prompt. Preserve the correct answer as an invalid
run; do not use its latency or token counts in a Graphit comparison.

The Graphit arm received a corrected plain-text prompt and the isolated stdio
MCP configuration. Its command output exceeded the enclosing capture context,
the process ended, and the requested final-answer file was not created. The
ephemeral client left no persisted transcript or usage summary. This is a
failed/unanalyzable Graphit pilot sample and must not be dropped or assigned
invented tool-call or token numbers. The current evidence therefore supports
no correctness, latency, or token-savings comparison. Any replacement model
run requires fresh explicit approval because the original authorization was
limited to one authenticated run per arm.

### First valid matched pair (2026-10-02)

After fresh explicit approval for one replacement per arm, both runs used new
isolated workspaces, the same task text, the same Codex CLI 0.160.0 default
model, and the same permission profile. Prompts were passed through stdin and
JSONL stdout/stderr were written directly to arm-local files. The CLI event
stream did not report the resolved server-side model identifier. Raw synthetic
prompts, event transcripts, final answers, timings, checksums, and the scored
summary are preserved in
[`docs/evidence/agent-eval-2026-10-02/`](evidence/agent-eval-2026-10-02/README.md).

Both arms passed every exact-answer requirement. Graphit made one
`get_relationships` call and received 425 UTF-8 bytes of fact context. The
full-facts arm located and read the complete 4,154-byte packet using two shell
calls. Graphit completed in 17.54 seconds with 54,957 client-reported input
tokens (46,976 cached) and 177 output tokens. The full-facts arm completed in
17.03 seconds with 45,314 input tokens (41,216 cached) and 182 output tokens.
Both reported zero reasoning-output tokens.

Therefore this first valid pair shows **no Graphit token or latency saving** on
the small 14-table fixture: Graphit used 9,643 more input tokens (21.3%) and
was 0.51 seconds (3.0%) slower despite returning 89.8% fewer fact bytes. A
plausible cause is fixed MCP tool-schema/context overhead, but this pair does
not isolate causality. It is only one sample per arm; do not report aggregate
success or savings until the required repeated runs exist.

A subsequent model-free measurement found 1,777 local proxy tokens across all
14 serialized tool definitions. Graphit's optional Codex `--compact-tools`
profile retains eight core navigation/relationship tools at 976 proxy tokens.
It is separate from the valid full-profile pair above.

### Compact-profile matched pairs (2026-10-02)

After separate explicit approvals, three fresh matched pairs alternated run
order. Prompts and packet hashes matched the earlier valid pair, isolation was
reverified for every arm, and Codex accepted the exact eight-tool
`enabled_tools` configuration before model use. Pair 1 evidence is preserved in
[`docs/evidence/agent-eval-compact-2026-10-02/`](evidence/agent-eval-compact-2026-10-02/README.md).
Pairs 2 and 3 plus the machine-readable aggregate are preserved in
[`docs/evidence/agent-eval-compact-repeats-2026-10-02/`](evidence/agent-eval-compact-repeats-2026-10-02/README.md).

All six arms passed the exact rubric. Compact Graphit used one
`get_relationships` call in every run; baseline used two shell calls. Compact
returned the same 425-byte fact context while baseline read the complete
4,154-byte packet. Mean compact input was 49,748.7 tokens versus baseline's
46,014.0, including 9,044.7 versus 5,992.7 non-cached input tokens. Mean wall
time was 17.81 versus 16.99 seconds. Compact total input was higher in all
three pairs; compact latency was higher in two and lower in one.

The compact mean was therefore 3,734.7 total input tokens (8.1%), 3,052.0
non-cached input tokens (50.9%), and 0.82 seconds (4.8%) above baseline. Pair
1's non-cached advantage did not replicate. The compact protocol minimum is
met for this tiny synthetic task, but it demonstrates correctness and smaller
returned facts—not token or latency savings. Keep the compact profile opt-in.
A larger-schema crossover test requires a separately specified experiment;
do not combine the full-profile sample with this compact aggregate.

### First scaled compact-profile matched pair (2026-10-02)

After explicit approval for exactly two new model calls, fresh isolated arms
used the 114-table / 420-column bundle specified above. Compact Graphit ran
first and full-facts baseline second. Both arms passed own-readable and
sibling-invisible sandbox checks; prompt hashes were unchanged, the baseline
packet retained its canonical SHA-256, and Codex accepted the exact eight-tool
allowlist. Raw prompts, transcripts, finals, timings, and checksums are in
[`docs/evidence/agent-eval-scaled-2026-10-02/`](evidence/agent-eval-scaled-2026-10-02/README.md).

Both arms passed the exact rubric. Compact Graphit made one
`get_relationships` call, returned 425 fact bytes, took 16.25 seconds, and
reported 51,139 total / 45,056 cached / 6,083 non-cached input tokens and 174
output tokens. Baseline used two shell calls, read the complete 51,256-byte
packet, took 15.59 seconds, and reported 53,635 total / 39,040 cached / 14,595
non-cached input tokens and 182 output tokens. Both reported zero reasoning
output and zero-byte stderr logs.

Graphit therefore used 2,496 fewer total input tokens (4.7%) and 8,512 fewer
non-cached input tokens (58.3%), while taking 0.66 seconds longer (4.2%). This
is the first observed token crossover, but it is one matched pair—not an
aggregate, billing claim, production result, or reason to change the default.
Two further scaled pairs with alternating order are required by the protocol.

### Scaled compact-profile aggregate (2026-10-02)

After explicit approval for four additional calls, pairs 2 and 3 alternated
order and reused the exact packet, prompts, rubric, isolation profile, and
eight-tool allowlist. Evidence and the machine-readable aggregate are in
[`docs/evidence/agent-eval-scaled-repeats-2026-10-02/`](evidence/agent-eval-scaled-repeats-2026-10-02/README.md).
All six scaled arms passed the exact rubric with zero-byte stderr logs.

Compact Graphit averaged 49,749.7 total / 8,619.0 non-cached input tokens and
16.54 seconds. Baseline averaged 59,315.0 / 14,259.0 and 17.34 seconds. Thus
Graphit averaged 9,565.3 fewer total input tokens (16.1%) and 5,640.0 fewer
non-cached tokens (39.6%). Total and non-cached input were lower in every pair.
This meets the repeat threshold and demonstrates a token crossover for this
exact 114-table synthetic context-delivery task.

Do not generalize the mean 0.81-second (4.7%) latency advantage: Graphit was
slower in pairs 1 and 2 and faster in pair 3, where baseline read the complete
packet twice and used three shell calls. The result is not proof of billing
savings, a universal table-count threshold, production adoption, SQL-MCP
behavior, or Claude behavior.
