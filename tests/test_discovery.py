"""Secret-safe PostgreSQL connection discovery."""

from pathlib import Path

import pytest

from graphit.discovery import (
    CredentialResolutionError,
    discover_postgresql,
    resolve_postgresql_password,
)
from graphit.sources import SourceConfig


def test_discovers_encoded_postgresql_environment_url_without_representing_secret(
    tmp_path: Path,
) -> None:
    secret = "s3cr%t value"
    candidates = discover_postgresql(
        tmp_path,
        environ={
            "DATABASE_URL": (
                "postgresql://reader:s3cr%25t%20value@db.internal:5433/erp%20main?sslmode=require"
            )
        },
    )

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.host == "db.internal"
    assert candidate.port == 5433
    assert candidate.database_name == "erp main"
    assert candidate.username == "reader"
    assert candidate.password == secret
    assert candidate.ssl_mode == "require"
    assert candidate.has_password is True
    assert secret not in repr(candidate)


def test_process_environment_wins_and_duplicate_dotenv_connection_is_collapsed(
    tmp_path: Path,
) -> None:
    (tmp_path / ".env").write_text(
        "DATABASE_URL=postgresql://reader:file-secret@localhost/erp\n",
        encoding="utf-8",
    )
    (tmp_path / ".env.local").write_text(
        'export REPORTING_DATABASE_URL="postgres://report:report-secret@localhost/reporting"\n',
        encoding="utf-8",
    )

    candidates = discover_postgresql(
        tmp_path,
        environ={"DATABASE_URL": "postgresql://reader:process-secret@localhost/erp"},
    )

    assert [(item.database_name, item.origin) for item in candidates] == [
        ("erp", "environment"),
        ("reporting", ".env.local"),
    ]
    assert candidates[0].password == "process-secret"


def test_discovery_ignores_examples_symlinks_oversized_and_invalid_urls(tmp_path: Path) -> None:
    secret = "must-not-be-read"
    (tmp_path / ".env.example").write_text(
        f"DATABASE_URL=postgresql://reader:{secret}@localhost/example\n",
        encoding="utf-8",
    )
    (tmp_path / ".env.production").write_bytes(b"x" * (1024 * 1024 + 1))
    external = tmp_path / "external.env"
    external.write_text(
        f"DATABASE_URL=postgresql://reader:{secret}@localhost/external\n",
        encoding="utf-8",
    )
    try:
        (tmp_path / ".env.local").symlink_to(external)
    except OSError:
        pass

    candidates = discover_postgresql(
        tmp_path,
        environ={
            "DATABASE_URL": "mysql://reader:secret@localhost/not-postgres",
            "POSTGRES_URL": "postgresql://missing-components",
        },
    )

    assert candidates == ()


def _url_source(**changes: object) -> SourceConfig:
    values = {
        "name": "erp",
        "host": "localhost",
        "port": 5432,
        "database_name": "erp",
        "username": "reader",
        "credential_env": "DATABASE_URL",
        "schemas": ("public",),
        "ssl_mode": "require",
        "credential_kind": "url_dotenv",
        "credential_file": ".env",
    }
    values.update(changes)
    return SourceConfig(**values)  # type: ignore[arg-type]


def test_resolves_saved_dotenv_url_password_without_persisting_the_value(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "DATABASE_URL=postgresql://reader:runtime-secret@localhost/erp?sslmode=require\n",
        encoding="utf-8",
    )

    assert resolve_postgresql_password(_url_source(), root=tmp_path, environ={}) == (
        "runtime-secret"
    )


def test_url_credential_resolution_fails_closed_when_identity_changes(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "DATABASE_URL=postgresql://reader:secret@other-host/erp?sslmode=require\n",
        encoding="utf-8",
    )

    with pytest.raises(CredentialResolutionError, match="identity changed"):
        resolve_postgresql_password(_url_source(), root=tmp_path, environ={})


def test_resolves_legacy_password_environment_reference(tmp_path: Path) -> None:
    source = _url_source(
        credential_kind="password_env",
        credential_file=None,
        credential_env="ERP_PASSWORD",
    )

    assert (
        resolve_postgresql_password(
            source, root=tmp_path, environ={"ERP_PASSWORD": "legacy-secret"}
        )
        == "legacy-secret"
    )
