"""Secret-safe PostgreSQL connection discovery."""

from pathlib import Path

import pytest

from graphit.discovery import (
    CredentialResolutionError,
    discover_databases,
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


def test_discovers_and_resolves_database_urls_from_nested_project_dotenv(
    tmp_path: Path,
) -> None:
    backend = tmp_path / "backend"
    backend.mkdir()
    (backend / ".env").write_text(
        "MSSQL_DATABASE_URL=mssql://reader:sql-secret@localhost/erp?encrypt=false\n"
        "ORACLE_DATABASE_URL=oracle://report:ora-secret@localhost/REPORTS\n",
        encoding="utf-8",
    )

    candidates = discover_databases(tmp_path, environ={})

    assert [(item.engine, item.origin) for item in candidates] == [
        ("mssql", "backend/.env"),
        ("oracle", "backend/.env"),
    ]
    source = _url_source(
        engine="mssql",
        port=1433,
        ssl_mode="disable",
        credential_env="MSSQL_DATABASE_URL",
        credential_file="backend/.env",
    )
    assert resolve_postgresql_password(source, root=tmp_path, environ={}) == "sql-secret"


def test_nested_discovery_excludes_dependency_state_and_overdeep_directories(
    tmp_path: Path,
) -> None:
    for relative in (
        Path("node_modules/package/.env"),
        Path(".graphit/.env"),
        Path("one/two/three/four/.env"),
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "DATABASE_URL=postgresql://reader:secret@localhost/ignored\n",
            encoding="utf-8",
        )

    assert discover_databases(tmp_path, environ={}) == ()


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


def test_discovers_sql_server_and_oracle_urls_without_exposing_passwords(tmp_path: Path) -> None:
    candidates = discover_databases(
        tmp_path,
        environ={
            "MSSQL_DATABASE_URL": (
                "mssql://reader:sql%20secret@sql.internal:1444/erp?encrypt=true"
            ),
            "ORACLE_DATABASE_URL": "oracles://report:ora%20secret@ora.internal:1522/ERPPRD",
        },
    )

    assert [(item.engine, item.port, item.ssl_mode) for item in candidates] == [
        ("mssql", 1444, "require"),
        ("oracle", 1522, "require"),
    ]
    assert [item.password for item in candidates] == ["sql secret", "ora secret"]
    assert "sql secret" not in repr(candidates)
    assert "ora secret" not in repr(candidates)


def test_discovers_common_sqlalchemy_mssql_and_oracle_url_schemes(tmp_path: Path) -> None:
    candidates = discover_databases(
        tmp_path,
        environ={
            "SQLALCHEMY_DATABASE_URI": (
                "mssql+pyodbc://reader:secret@sql.internal/erp?encrypt=true"
            ),
            "REPORTING_DB_URI": "oracle+oracledb://report:secret@ora.internal/REPORTS",
        },
    )

    assert [(item.engine, item.database_name) for item in candidates] == [
        ("oracle", "REPORTS"),
        ("mssql", "erp"),
    ]


def test_discovers_async_sqlalchemy_urls_and_oracle_query_service(tmp_path: Path) -> None:
    candidates = discover_databases(
        tmp_path,
        environ={
            "POSTGRESQL_DATABASE_URL": ("postgresql+asyncpg://reader:secret@localhost:5434/erp"),
            "MSSQL_DATABASE_URL": (
                "mssql+aioodbc://reader:secret@localhost/erp?Encrypt=yes&"
                "TrustServerCertificate=yes&driver=ODBC+Driver+18"
            ),
            "ORACLE_DATABASE_URL": (
                "oracle+oracledb://reader:secret@localhost:1521/?service_name=FREEPDB1"
            ),
        },
    )

    assert [(item.engine, item.database_name, item.ssl_mode) for item in candidates] == [
        ("mssql", "erp", "require-trust-server-certificate"),
        ("oracle", "FREEPDB1", "disable"),
        ("postgresql", "erp", "prefer"),
    ]


def test_rejects_invalid_mssql_encryption_flags(tmp_path: Path) -> None:
    assert (
        discover_databases(
            tmp_path,
            environ={
                "MSSQL_DATABASE_URL": (
                    "mssql://reader:secret@localhost/erp?Encrypt=yes&TrustServerCertificate=maybe"
                )
            },
        )
        == ()
    )


@pytest.mark.parametrize(
    ("engine", "url", "ssl_mode"),
    [
        ("mssql", "sqlserver://reader:secret@localhost/erp?encrypt=false", "disable"),
        ("oracle", "oracle://reader:secret@localhost/ERP", "disable"),
    ],
)
def test_resolves_non_postgresql_saved_url_references(
    tmp_path: Path, engine: str, url: str, ssl_mode: str
) -> None:
    (tmp_path / ".env").write_text(f"DATABASE_URL={url}\n", encoding="utf-8")
    source = _url_source(
        engine=engine,
        port=1433 if engine == "mssql" else 1521,
        database_name="erp" if engine == "mssql" else "ERP",
        ssl_mode=ssl_mode,
    )

    assert resolve_postgresql_password(source, root=tmp_path, environ={}) == "secret"
