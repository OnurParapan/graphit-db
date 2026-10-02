# Releasing Graphit

Graphit publishes the `graphit-db` distribution from GitHub Actions through
PyPI Trusted Publishing. Do not store a PyPI password or long-lived API token
in the repository or GitHub secrets.

## Trusted Publisher identity

The first release established a PyPI Trusted Publisher with exactly:

- PyPI project name: `graphit-db`
- GitHub owner: `OnurParapan`
- GitHub repository: `graphit-db`
- Workflow filename: `release.yml`
- GitHub environment: `pypi`

The GitHub `pypi` environment requires Onur Parapan as a deployment reviewer.
Do not add a PyPI token secret. The publisher and workflow names are identity
claims and must match exactly.

Release `0.1.0` secured the public project name on 2026-10-02. If the owner,
repository, workflow filename, or environment changes, update the trusted
publisher deliberately before publishing another release.

## Release trigger

The release workflow runs only when a GitHub Release is published. For version
`X.Y.Z`, the release tag must be exactly `vX.Y.Z`; the build fails before any
upload if the tag and `pyproject.toml` version differ.

The workflow:

1. checks out the tagged source without persisted Git credentials,
2. builds wheel and sdist in an unprivileged job,
3. checks archive scope, author, URLs, license, notice, entry point, and README,
4. installs the wheel and exercises init plus Codex/Claude MCP launchers,
5. transfers only the verified distributions to a separate publish job,
6. pauses at the protected `pypi` environment for human approval, and
7. exchanges GitHub OIDC for a short-lived PyPI credential and uploads with
   PEP 740 attestations.

All referenced GitHub Actions are pinned to full commit hashes. Only the
publish job receives `id-token: write`; it does not check out or build source.

## Release checklist

Before publishing the GitHub Release:

- confirm the PyPI publisher identity and protected GitHub environment,
- confirm `main` CI is green,
- confirm the intended version does not already exist on PyPI,
- review version, classifier, README, `LICENSE`, and `NOTICE`,
- create tag `vX.Y.Z` from the intended commit, and
- publish the GitHub Release, then explicitly approve the `pypi` deployment.

After publication:

- verify the PyPI project, file hashes, verified GitHub URL, and attestations,
- install from PyPI in a fresh environment with pip, pipx, and uv where
  available,
- run `graphit version`, `graphit --help`, and the installed smoke test,
- update README and release-readiness statements that still say unpublished,
  and
- never replace a published version; publish a new version for every correction.

## Published release evidence

Version `0.1.0` was published on 2026-10-02 from tag `v0.1.0` and commit
`33cef8b16c7ae9de9adb0d07baba81a2776ca5f5`:

- GitHub release: <https://github.com/OnurParapan/graphit-db/releases/tag/v0.1.0>
- protected workflow run: <https://github.com/OnurParapan/graphit-db/actions/runs/37020399874>
- PyPI project: <https://pypi.org/project/graphit-db/0.1.0/>
- wheel SHA-256: `f79229a3edbab13dcc37249dddaf967449c9f6de8468adb0cb1145674e0c8eb2`
- sdist SHA-256: `6a90adf14f06328f75e4f8b271c20427aa401767fc4653a0ae7c590c1515799b`

Both files carry PyPI-hosted attestations identifying GitHub repository
`OnurParapan/graphit-db`, workflow `release.yml`, and environment `pypi`.
A fresh Windows virtual environment then installed `graphit-db==0.1.0` from
public PyPI and passed `pip check`, version, initialization, SQLite-store,
Codex/Claude launcher, and representative stdio MCP checks. pipx and uv were
not installed on that machine, so those wrappers remain user-observation paths,
not separately executed release evidence.
