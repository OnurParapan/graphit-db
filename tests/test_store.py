"""Versioned SQLite store behavior and failure safety."""

import sqlite3
from pathlib import Path

import pytest
from pytest import MonkeyPatch

from graphit import store


def test_creates_versioned_store_with_documented_tables(tmp_path: Path) -> None:
    path = tmp_path / "graphit.db"

    assert store.initialize_store(path) is True

    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        names = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert {
            "store_migrations",
            "sources",
            "scan_runs",
            "snapshots",
            "objects",
            "columns",
            "edges",
            "relationship_evidence",
            "review_decisions",
        } <= names
        assert (
            connection.execute(
                "SELECT checksum FROM store_migrations WHERE version = 1"
            ).fetchone()[0]
            == store.MIGRATIONS[0].checksum
        )
        assert connection.execute("PRAGMA foreign_key_list(edges)").fetchall()


def test_reopen_preserves_existing_rows(tmp_path: Path) -> None:
    path = tmp_path / "graphit.db"
    store.initialize_store(path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO review_decisions "
            "(source_logical_key, target_logical_key, relationship_kind, decision) "
            "VALUES ('source', 'target', 'REFERENCES', 'APPROVED')"
        )

    assert store.initialize_store(path) is False

    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT count(*) FROM review_decisions").fetchone()[0] == 1


def test_rejects_newer_schema_without_changing_version(tmp_path: Path) -> None:
    path = tmp_path / "graphit.db"
    store.initialize_store(path)
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version = 99")

    with pytest.raises(store.StoreError, match="newer than supported"):
        store.initialize_store(path)

    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 99


def test_rejects_tampered_migration_history(tmp_path: Path) -> None:
    path = tmp_path / "graphit.db"
    store.initialize_store(path)
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE store_migrations SET checksum = 'tampered'")

    with pytest.raises(store.StoreError, match="missing or changed"):
        store.initialize_store(path)


def test_failed_migration_rolls_back_all_its_statements(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    path = tmp_path / "graphit.db"
    store.initialize_store(path)
    broken = store.Migration(3, ("CREATE TABLE temporary_data (id INTEGER)", "INVALID SQL"))
    monkeypatch.setattr(store, "MIGRATIONS", (*store.MIGRATIONS, broken))
    monkeypatch.setattr(store, "SCHEMA_VERSION", 3)

    with pytest.raises(store.StoreError, match="Cannot initialize local store"):
        store.initialize_store(path)

    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert (
            connection.execute(
                "SELECT name FROM sqlite_master WHERE name = 'temporary_data'"
            ).fetchone()
            is None
        )
        assert connection.execute("SELECT count(*) FROM store_migrations").fetchone()[0] == 2


def test_migrates_v1_source_credentials_without_changing_existing_reference(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    path = tmp_path / "graphit.db"
    migrations = store.MIGRATIONS
    monkeypatch.setattr(store, "MIGRATIONS", migrations[:1])
    monkeypatch.setattr(store, "SCHEMA_VERSION", 1)
    store.initialize_store(path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            """INSERT INTO sources
            (name, engine, database_name, host, port, username,
             credential_env, ssl_mode, selected_schemas_json)
            VALUES ('legacy', 'postgresql', 'erp', 'localhost', 5432, 'reader',
                    'ERP_PASSWORD', 'require', '[\"public\"]')"""
        )

    monkeypatch.setattr(store, "MIGRATIONS", migrations)
    monkeypatch.setattr(store, "SCHEMA_VERSION", 2)
    assert store.initialize_store(path) is False

    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert connection.execute(
            "SELECT credential_env, credential_kind, credential_file FROM sources"
        ).fetchone() == ("ERP_PASSWORD", "password_env", None)


def test_unversioned_database_is_not_adopted(tmp_path: Path) -> None:
    path = tmp_path / "graphit.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE user_data (value TEXT)")

    with pytest.raises(store.StoreError, match="Unversioned SQLite database"):
        store.initialize_store(path)

    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'user_data'"
        ).fetchone()
