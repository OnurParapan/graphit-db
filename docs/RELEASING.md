# Releasing Graphit

Graphit publishes the `graphit-db` distribution from GitHub Actions through
PyPI Trusted Publishing. Do not store a PyPI password or long-lived API token
in the repository or GitHub secrets.

## One-time publisher setup

Before creating the first release, sign in to PyPI and register a pending
GitHub publisher at <https://pypi.org/manage/account/publishing/> with exactly:

- PyPI project name: `graphit-db`
- GitHub owner: `OnurParapan`
- GitHub repository: `graphit-db`
- Workflow filename: `release.yml`
- GitHub environment: `pypi`

In the GitHub repository settings, create the `pypi` environment and require
Onur Parapan as a deployment reviewer. Do not add a PyPI token secret. The
publisher and workflow names are identity claims and must match exactly.

The public PyPI JSON endpoint returned 404 for `graphit-db` on 2026-10-02.
That is only a point-in-time availability check; the name is not secured until
the first trusted publication succeeds.

## Release trigger

The release workflow runs only when a GitHub Release is published. For version
`0.1.0`, the release tag must be exactly `v0.1.0`; the build fails before any
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

## First-release checklist

Before publishing the GitHub Release:

- confirm the pending PyPI publisher and protected GitHub environment exist,
- confirm `main` CI is green,
- confirm `graphit-db` still returns no existing PyPI project,
- review version, classifier, README, `LICENSE`, and `NOTICE`,
- create tag `v0.1.0` from the intended commit, and
- publish the GitHub Release, then explicitly approve the `pypi` deployment.

After publication:

- verify the PyPI project, file hashes, verified GitHub URL, and attestations,
- install from PyPI in a fresh environment with pip, pipx, and uv where
  available,
- run `graphit version`, `graphit --help`, and the installed smoke test,
- update README and release-readiness statements that still say unpublished,
  and
- never replace version `0.1.0`; publish a new version for every correction.
