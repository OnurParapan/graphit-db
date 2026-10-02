"""Secret-safe PostgreSQL connection discovery."""

from pathlib import Path

from graphit.discovery import discover_postgresql


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
