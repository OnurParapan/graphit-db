# Graphit — Security Model

## Principle

Graphit uses the least privilege required to understand database structure and
optional relationship evidence.

## Source credentials

Recommended source account:

- read-only,
- no DDL/DML privileges,
- limited to selected databases/schemas,
- bounded SELECT permission only when enhanced inference is enabled.

Configuration stores a credential variable name and, for discovered dotenv
URLs, one bounded project-relative allowlisted filename—not either value. Credentials and
full URLs are never written to `graphit.toml`, SQLite, generated graph exports,
logs, exceptions, or MCP responses.

Initialization discovery may read a PostgreSQL, SQL Server, or Oracle URL from the current process
environment or a bounded allowlist of project `.env*` files. Discovery descends
at most three safe directory levels, considers at most 32 files, and excludes
VCS, dependency, build, virtual-environment, and Graphit state directories. It parses a
password only into a transient in-process candidate and reports its presence as
`present (hidden)`. It never prints the URL, represents the password in the
candidate's debug output, or persists either value. Discovery refuses symlinked
dotenv files or path components, ignores example/template names, and caps each
file at 1 MiB. Init obtains sanitized user confirmation
before contacting a candidate unless `--yes` was explicit. It enforces the
selected adapter's metadata-only checks and timeouts as `source test`,
then persists only the non-secret reference after success. Rejected and failed
candidates are not saved. Later resolution also checks that the URL's non-secret
connection identity still matches the saved source before using its password.
Bounded verification catalog reads discover accessible user schemas while
excluding engine-specific system namespaces. Oracle uses the catalog's
`ORACLE_MAINTAINED` marker and always retains the current non-system user even
when its new schema has no objects yet. Init
then runs the existing bounded catalog scanner under a fresh connection with
each adapter's strongest available read-only setting. Schema overflow or catalog limits fail closed; no partial successful
snapshot is presented.

`graphit source test` reads the configured environment variable only at call
time. PostgreSQL receives separate driver parameters. SQL Server receives a
transient, escaped ODBC connection string that is never retained or printed.
Its default `require` mode verifies the server certificate. A discovered URL's
explicit `TrustServerCertificate=yes` becomes the separate
`require-trust-server-certificate` mode; traffic remains encrypted, but this
weakens server identity verification and is intended for explicitly configured
local/self-signed environments.
Oracle receives separate user/password parameters plus a non-secret generated
Easy Connect descriptor. PostgreSQL forces and confirms a read-only transaction.
Oracle starts `SET TRANSACTION READ ONLY` before any catalog query and rejects
SYS, for which Oracle does not provide the same guarantee. SQL Server requests
ODBC read-only mode and detects direct database/object write permissions. Such
credentials are permitted with a visible `PRIVILEGED_CREDENTIAL` warning because
the adapter executes only fixed bounded metadata SELECTs; the advisory ODBC flag
is defense in depth, not the product's sole safety boundary. Every adapter applies
connect/query timeouts and only executes fixed bounded catalog reads. Driver
error details are classified but never printed because they may contain secrets
or connection parameters. PostgreSQL `prefer` may fall back to plaintext;
SQL Server `require` validates the server certificate; Oracle `require` uses
TCPS and relies on the deployment's trust/wallet configuration.

## Scan modes

### Metadata-only

Reads database catalogs and supported object definitions. This is the safest
default for new projects.

### Enhanced inference

Adds bounded read-only sampling/statistical queries. It is explicitly enabled,
configurable, and may be disabled by organizational policy. This mode is
planned, not implemented. Before enabling it, Graphit must prove that each
query's access path and work budget are bounded; a result `LIMIT` and statement
timeout alone do not establish that. No current candidate preview reads source
table values.

## Query policy

Scanner SQL must:

- be owned by the database adapter, not user-provided,
- parameterize values where possible,
- quote identifiers through a tested adapter utility,
- apply statement timeouts,
- avoid requested locks and unbounded scans,
- reject any modifying statement class.

Graphit does not expose an arbitrary SQL execution interface.

## Data minimization

Persist:

- object metadata,
- safe database comments when configured,
- counts, estimates, ratios, and hashes needed for evidence,
- relationship decisions.

Do not persist raw sampled business values by default.

## Local store

`.graphit/` may reveal schema names and business semantics. It is ignored by Git
by default and should inherit user-only filesystem permissions where supported.

Graph exports carry the same sensitivity as schema metadata. The CLI warns when
writing outside `.graphit/exports/`.

The self-contained HTML graph loads no remote assets. Its local search/filter
behavior is a fixed inline script authorized by an exact SHA-256 in the
document CSP. Untrusted database identifiers, evidence, and human reasons are
HTML-escaped into markup/data attributes and are never inserted into executable
code. The full accessible graph remains readable when JavaScript is disabled.
The automatic whole-database ERD is written under ignored `.graphit/exports/`,
uses the same escaping and CSP boundary, contains schema metadata only, and
refuses overwrite. Its complete projection fails closed above explicit node or
FK limits rather than concealing omitted structure.

## Agent wiring

- Prefer project-local configuration.
- Show all files before changing them.
- Do not install global hooks or mutate global agent settings silently.
- MCP defaults to read-only knowledge access.
- Default init writes only project-local Codex and Claude Graphit entries,
  preserves unrelated settings, and refuses unrecognized conflicts.
  `--refresh-agents` accepts only launchers matching Graphit's generated shape;
  `--no-agents` performs no agent write.

## Logging

Logs may include operation, duration, source alias, snapshot ID, and counts.
They must not include passwords, full secret-bearing DSNs, sampled values, or
unredacted exception context from drivers.

## Supply chain

- Keep runtime dependencies minimal.
- Pin release builds through a lock/reproducible release process.
- Run dependency and artifact security checks in CI.
- Standalone binaries must publish hashes and signed provenance when introduced.

The PyPI release workflow separates an unprivileged build/verification job from
the publishing job. Actions and release tools are pinned, and only the publish
job receives `id-token: write`. It uses a protected `pypi` environment and
short-lived Trusted Publishing credentials; no PyPI token belongs in GitHub
secrets or local configuration. The publish job consumes verified artifacts and
does not check out or execute repository source.

The initial CI workflow uses read-only repository token permissions and a
disposable PostgreSQL service with a test-only password. It does not consume
production database credentials or publish artifacts. The workflow checks
installed dependency consistency but does not yet replace a dependency
vulnerability audit, locked release build, or provenance verification.
