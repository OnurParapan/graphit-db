"""Source configuration safety and persistence tests."""

import sqlite3
from pathlib import Path

import pytest

from graphit.project import initialize_project
from graphit.sources import (
    ProjectNotInitialized,
    SourceAlreadyExists,
    SourceConfig,
    SourceError,
    SourceNotFound,
    add_source,
    list_sources,
    show_source,
)


def _source(**changes: object) -> SourceConfig:
    values = {
        "name": "claims",
        "host": "localhost",
        "port": 5432,
        "database_name": "claims_db",
        "username": "graphit_reader",
        "credential_env": "CLAIMS_DB_PASSWORD",
        "schemas": ("public", "billing"),
        "ssl_mode": "require",
    }
    values.update(changes)
    return SourceConfig(**values)  # type: ignore[arg-type]


def test_add_list_show_persist_without_password_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    initialize_project(tmp_path)
    monkeypatch.setenv("CLAIMS_DB_PASSWORD", "do-not-persist-this-secret")
    source = _source()

    add_source(tmp_path, source)

    assert show_source(tmp_path, "claims") == source
    assert list_sources(tmp_path) == (source,)
    store_bytes = (tmp_path / ".graphit" / "graphit.db").read_bytes()
    assert b"do-not-persist-this-secret" not in store_bytes
    assert b"CLAIMS_DB_PASSWORD" in store_bytes


def test_list_is_sorted_and_duplicates_do_not_replace(tmp_path: Path) -> None:
    initialize_project(tmp_path)
    add_source(tmp_path, _source(name="zeta"))
    add_source(tmp_path, _source(name="alpha"))

    assert [item.name for item in list_sources(tmp_path)] == ["alpha", "zeta"]
    with pytest.raises(SourceAlreadyExists, match="already exists"):
        add_source(tmp_path, _source(name="alpha", host="other-host"))
    assert show_source(tmp_path, "alpha").host == "localhost"


@pytest.mark.parametrize(
    ("engine", "port", "schema", "ssl_mode"),
    [
        ("mssql", 1433, "dbo", "require"),
        ("oracle", 1521, "APP", "disable"),
    ],
)
def test_mssql_and_oracle_source_metadata_round_trips(
    tmp_path: Path, engine: str, port: int, schema: str, ssl_mode: str
) -> None:
    initialize_project(tmp_path)
    source = _source(
        engine=engine,
        port=port,
        schemas=(schema,),
        ssl_mode=ssl_mode,
    )

    add_source(tmp_path, source)

    assert show_source(tmp_path, "claims") == source


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"name": "bad name"}, "Source name"),
        ({"engine": "mysql"}, "postgresql, mssql, or oracle"),
        ({"host": "user@host"}, "Host"),
        ({"host": "host;PWD=secret"}, "Host"),
        ({"port": 0}, "Port"),
        ({"port": 65536}, "Port"),
        ({"database_name": ""}, "Database"),
        ({"username": "bad\nname"}, "Username"),
        ({"credential_env": "PASSWORD=secret"}, "Credential"),
        ({"credential_kind": "raw_password"}, "Credential kind"),
        (
            {"credential_kind": "url_dotenv", "credential_file": "../outside.env"},
            "allowlist",
        ),
        ({"credential_kind": "url_env", "credential_file": ".env"}, "valid only"),
        ({"schemas": ()}, "schemas"),
        ({"schemas": ("public", "public")}, "schemas"),
        ({"schemas": ("bad\nschema",)}, "Schema"),
        ({"ssl_mode": "unknown"}, "SSL mode"),
        ({"engine": "mssql", "ssl_mode": "prefer"}, "SSL mode for mssql"),
        ({"engine": "oracle", "ssl_mode": "verify-full"}, "SSL mode for oracle"),
    ],
)
def test_invalid_metadata_is_rejected_before_write(
    tmp_path: Path, changes: dict[str, object], message: str
) -> None:
    initialize_project(tmp_path)

    with pytest.raises(SourceError, match=message):
        add_source(tmp_path, _source(**changes))

    assert list_sources(tmp_path) == ()


def test_missing_project_and_store_do_not_get_created_by_source_commands(tmp_path: Path) -> None:
    with pytest.raises(ProjectNotInitialized, match="graphit init"):
        add_source(tmp_path, _source())
    assert not (tmp_path / ".graphit").exists()

    initialize_project(tmp_path)
    (tmp_path / ".graphit" / "graphit.db").unlink()
    with pytest.raises(ProjectNotInitialized, match="graphit init"):
        list_sources(tmp_path)
    assert not (tmp_path / ".graphit" / "graphit.db").exists()


def test_show_missing_source(tmp_path: Path) -> None:
    initialize_project(tmp_path)
    with pytest.raises(SourceNotFound, match="was not found"):
        show_source(tmp_path, "missing")


def test_store_constraint_is_enforced_without_replacing_existing_source(tmp_path: Path) -> None:
    initialize_project(tmp_path)
    add_source(tmp_path, _source())
    path = tmp_path / ".graphit" / "graphit.db"
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 1
