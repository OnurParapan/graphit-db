"""CLI foundation contract tests."""

import json
from pathlib import Path

from pytest import MonkeyPatch
from typer.testing import CliRunner

from graphit import __version__
from graphit.cli import app
from graphit.queries import (
    ColumnDetail,
    ForeignKeyDetail,
    KeyDetail,
    PathResult,
    PathStep,
    QueryError,
    RelationshipContext,
    RelationshipDetail,
    SearchMatch,
    SearchResult,
    TableContext,
)
from graphit.scanners.postgresql import ConnectionTestError, ConnectionTestResult, MetadataScanError
from graphit.snapshots import StoredSnapshot
from graphit.sources import SourceConfig, list_sources

runner = CliRunner()


def test_version_reports_installed_version() -> None:
    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert result.stdout == f"Graphit {__version__}\n"


def test_root_help_describes_product_boundary() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "compact context for AI coding agents" in result.stdout
    assert "version" in result.stdout


def test_init_explicit_project_and_conflict(tmp_path: Path) -> None:
    first = runner.invoke(app, ["init", "--project", str(tmp_path)])
    again = runner.invoke(app, ["init", "--project", str(tmp_path)])

    assert first.exit_code == 0
    assert "created: graphit.toml" in first.stdout
    assert f"created: {Path('.graphit') / 'graphit.db'}" in first.stdout
    assert (tmp_path / ".graphit" / "graphit.db").is_file()
    assert again.exit_code == 2
    assert "PROJECT_INIT_FAILED" in again.stderr


def test_init_uses_git_root_from_nested_directory(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    (tmp_path / ".git").mkdir()
    nested = tmp_path / "src" / "module"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0
    assert (tmp_path / "graphit.toml").exists()
    assert not (nested / "graphit.toml").exists()


def test_init_discovers_dotenv_postgresql_url_without_leaking_or_persisting_password(
    tmp_path: Path,
) -> None:
    secret = "never-print-or-persist-this"
    (tmp_path / ".env").write_text(
        f"DATABASE_URL=postgresql://reader:{secret}@localhost:5434/erp?sslmode=require\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["init", "--project", str(tmp_path), "--no-connect"])

    assert result.exit_code == 0
    assert "Discovered PostgreSQL from .env -> DATABASE_URL" in result.stdout
    assert "Host: localhost:5434" in result.stdout
    assert "Database: erp" in result.stdout
    assert "User: reader" in result.stdout
    assert "Password: present (hidden)" in result.stdout
    assert "SSL mode: require" in result.stdout
    assert "Database connection skipped by --no-connect" in result.stdout
    assert secret not in result.stdout + result.stderr
    assert secret.encode() not in (tmp_path / "graphit.toml").read_bytes()
    assert secret.encode() not in (tmp_path / ".graphit" / "graphit.db").read_bytes()


def test_init_verifies_and_saves_approved_dotenv_source_without_secret(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    secret = "runtime-only-secret"
    (tmp_path / ".env").write_text(
        f"DATABASE_URL=postgresql://reader:{secret}@localhost/erp?sslmode=require\n",
        encoding="utf-8",
    )
    verified: list[tuple[SourceConfig, Path | None]] = []

    def successful_test(
        source: SourceConfig, *, project_root: Path | None = None
    ) -> ConnectionTestResult:
        verified.append((source, project_root))
        return ConnectionTestResult("erp", "reader", "16.4")

    monkeypatch.setattr("graphit.cli.verify_connection", successful_test)

    result = runner.invoke(app, ["init", "--project", str(tmp_path), "--yes"])

    assert result.exit_code == 0
    assert "Added source 'erp' after read-only verification" in result.stdout
    assert "No metadata scan was run yet" in result.stdout
    assert "Connect read-only" not in result.stdout
    source, project_root = verified[0]
    assert project_root == tmp_path
    assert source.credential_kind == "url_dotenv"
    assert source.credential_file == ".env"
    stored = (tmp_path / ".graphit" / "graphit.db").read_bytes()
    assert secret.encode() not in stored
    assert b"DATABASE_URL" in stored

    again = runner.invoke(app, ["init", "--project", str(tmp_path), "--force", "--yes"])
    assert again.exit_code == 0
    assert "Source for DATABASE_URL is already configured" in again.stdout
    assert len(verified) == 1
    assert len(list_sources(tmp_path)) == 1


def test_init_declined_or_failed_connection_does_not_save_source(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text(
        "DATABASE_URL=postgresql://reader:secret@localhost/erp\n", encoding="utf-8"
    )
    attempts = 0

    def should_not_connect(*_args: object, **_kwargs: object) -> ConnectionTestResult:
        nonlocal attempts
        attempts += 1
        raise AssertionError("declined candidate must not connect")

    monkeypatch.setattr("graphit.cli.verify_connection", should_not_connect)
    declined = runner.invoke(app, ["init", "--project", str(tmp_path)], input="n\n")

    assert declined.exit_code == 0
    assert attempts == 0
    assert "Skipped DATABASE_URL by user choice" in declined.stdout

    def failed_test(*_args: object, **_kwargs: object) -> ConnectionTestResult:
        raise ConnectionTestError("AUTHENTICATION_FAILED", "PostgreSQL authentication failed.")

    monkeypatch.setattr("graphit.cli.verify_connection", failed_test)
    failed = runner.invoke(
        app,
        ["init", "--project", str(tmp_path), "--force", "--yes"],
    )

    assert failed.exit_code == 4
    assert "AUTHENTICATION_FAILED" in failed.stderr
    assert list_sources(tmp_path) == ()


def test_source_add_list_show_without_password_output(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    runner.invoke(app, ["init", "--project", str(tmp_path)])
    monkeypatch.setenv("CLAIMS_DB_PASSWORD", "do-not-print-secret")
    common = ["--project", str(tmp_path)]
    add = runner.invoke(
        app,
        [
            "source",
            "add",
            "claims",
            "--host",
            "localhost",
            "--database",
            "claims_db",
            "--username",
            "reader",
            "--credential-env",
            "CLAIMS_DB_PASSWORD",
            "--schema",
            "public",
            "--schema",
            "billing",
            *common,
        ],
    )
    listed = runner.invoke(app, ["source", "list", *common])
    shown = runner.invoke(app, ["source", "show", "claims", *common])

    assert add.exit_code == listed.exit_code == shown.exit_code == 0
    assert "No database connection was made" in add.stdout
    assert "claims\tpostgresql\tlocalhost:5432/claims_db" in listed.stdout
    assert "Schemas: public, billing" in shown.stdout
    assert "CLAIMS_DB_PASSWORD" in shown.stdout
    assert "do-not-print-secret" not in add.stdout + listed.stdout + shown.stdout


def test_source_cli_missing_project_and_duplicate(tmp_path: Path) -> None:
    common = ["--project", str(tmp_path)]
    missing = runner.invoke(app, ["source", "list", *common])
    runner.invoke(app, ["init", *common])
    args = [
        "source",
        "add",
        "claims",
        "--host",
        "localhost",
        "--database",
        "claims_db",
        "--username",
        "reader",
        "--credential-env",
        "CLAIMS_DB_PASSWORD",
        *common,
    ]
    first = runner.invoke(app, args)
    duplicate = runner.invoke(app, args)
    unknown = runner.invoke(app, ["source", "show", "unknown", *common])

    assert missing.exit_code == 3
    assert "PROJECT_NOT_INITIALIZED" in missing.stderr
    assert first.exit_code == 0
    assert duplicate.exit_code == 2
    assert "already exists" in duplicate.stderr
    assert unknown.exit_code == 2
    assert "was not found" in unknown.stderr


def test_source_test_cli_success_and_missing_credential(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    common = ["--project", str(tmp_path)]
    runner.invoke(app, ["init", *common])
    runner.invoke(
        app,
        [
            "source",
            "add",
            "claims",
            "--host",
            "localhost",
            "--database",
            "claims_db",
            "--username",
            "reader",
            "--credential-env",
            "CLAIMS_DB_PASSWORD",
            *common,
        ],
    )
    monkeypatch.delenv("CLAIMS_DB_PASSWORD", raising=False)
    missing = runner.invoke(app, ["source", "test", "claims", *common])

    assert missing.exit_code == 4
    assert "MISSING_CREDENTIAL" in missing.stderr

    def successful_test(
        _source: object, *, project_root: Path | None = None
    ) -> ConnectionTestResult:
        assert project_root == tmp_path
        return ConnectionTestResult("claims_db", "reader", "16.2")

    monkeypatch.setattr("graphit.cli.verify_connection", successful_test)
    success = runner.invoke(app, ["source", "test", "claims", *common])

    assert success.exit_code == 0
    assert "claims_db as reader (read-only)" in success.stdout


def test_source_test_cli_sanitizes_driver_failure(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    common = ["--project", str(tmp_path)]
    runner.invoke(app, ["init", *common])
    runner.invoke(
        app,
        [
            "source",
            "add",
            "claims",
            "--host",
            "localhost",
            "--database",
            "claims_db",
            "--username",
            "reader",
            "--credential-env",
            "CLAIMS_DB_PASSWORD",
            *common,
        ],
    )

    def failing_test(_source: object, *, project_root: Path | None = None) -> ConnectionTestResult:
        assert project_root == tmp_path
        raise ConnectionTestError("TLS_ERROR", "PostgreSQL TLS negotiation failed.")

    monkeypatch.setattr("graphit.cli.verify_connection", failing_test)
    failed = runner.invoke(app, ["source", "test", "claims", *common])

    assert failed.exit_code == 4
    assert "TLS_ERROR" in failed.stderr
    assert "password" not in failed.stderr.lower()


def test_scan_cli_reports_snapshot_and_sanitized_failure(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    runner.invoke(app, ["init", "--project", str(tmp_path)])

    def successful_scan(_root: Path, name: str) -> StoredSnapshot:
        assert name == "erp"
        return StoredSnapshot("erp", 7, 2, 15, 23, "fingerprint")

    monkeypatch.setattr("graphit.cli.scan_source", successful_scan)
    success = runner.invoke(app, ["scan", "--source", "erp", "--project", str(tmp_path)])

    assert success.exit_code == 0
    assert "Snapshot 2 saved for erp: 15 objects, 23 edges." in success.stdout

    def failed_scan(_root: Path, _name: str) -> StoredSnapshot:
        raise MetadataScanError("SCHEMA_PERMISSION_DENIED", "Selected schema is inaccessible.")

    monkeypatch.setattr("graphit.cli.scan_source", failed_scan)
    failed = runner.invoke(app, ["scan", "--source", "erp", "--project", str(tmp_path)])

    assert failed.exit_code == 5
    assert "SCHEMA_PERMISSION_DENIED" in failed.stderr
    assert "password" not in failed.stderr.lower()


def test_search_cli_human_json_and_no_snapshot(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    def successful_search(_root: Path, name: str, query: str, limit: int) -> SearchResult:
        assert (name, query, limit) == ("erp", "customer", 1)
        return SearchResult(
            "erp",
            2,
            (
                SearchMatch(
                    "COLUMN",
                    "postgres:column:x",
                    '"public"."Customer"."id"',
                    True,
                    "integer",
                    False,
                    True,
                    True,
                ),
            ),
            True,
        )

    monkeypatch.setattr("graphit.cli.search_objects", successful_search)
    args = ["search", "customer", "--source", "erp", "--limit", "1", "--project", str(tmp_path)]
    human = runner.invoke(app, args)
    machine = runner.invoke(app, [*args, "--json"])

    assert human.exit_code == 0
    assert "snapshot 2" in human.stdout
    assert 'COLUMN\t"public"."Customer"."id" : integer NOT NULL PK UNIQUE' in human.stdout
    assert "More matches exist" in human.stdout
    assert machine.exit_code == 0
    payload = json.loads(machine.stdout)
    assert payload["snapshot_version"] == 2
    assert payload["matches"][0]["primary_key"] is True
    assert payload["truncated"] is True

    def missing_snapshot(_root: Path, _name: str, _query: str, _limit: int) -> SearchResult:
        raise QueryError("NO_SNAPSHOT", "Source 'erp' has no successful scan.")

    monkeypatch.setattr("graphit.cli.search_objects", missing_snapshot)
    missing = runner.invoke(app, args)
    assert missing.exit_code == 6
    assert "NO_SNAPSHOT" in missing.stderr


def test_show_cli_compact_human_and_json_output(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    def result(_root: Path, name: str, table: str, limit: int) -> TableContext:
        assert (name, table, limit) == ("erp", "public.orders", 1)
        return TableContext(
            source_name="erp",
            snapshot_version=3,
            logical_key="postgres:table:orders",
            qualified_name='"public"."orders"',
            in_scope=True,
            partitioned=False,
            columns=(ColumnDetail("customer_id", "integer", False, False, False),),
            keys=(KeyDetail("orders_pkey", "PRIMARY_KEY", ("id",), False),),
            foreign_keys=(
                ForeignKeyDetail(
                    "orders_customer_fk",
                    '"external"."customer"',
                    (("customer_id", "id"),),
                    False,
                    False,
                    True,
                ),
            ),
            columns_truncated=True,
            keys_truncated=False,
            foreign_keys_truncated=False,
        )

    monkeypatch.setattr("graphit.cli.show_table", result)
    args = ["show", "public.orders", "--source", "erp", "--limit", "1", "--project", str(tmp_path)]
    human = runner.invoke(app, args)
    machine = runner.invoke(app, [*args, "--json"])

    assert human.exit_code == 0
    assert "snapshot 3" in human.stdout
    assert "COLUMN\tcustomer_id : integer NOT NULL" in human.stdout
    assert "PRIMARY_KEY\torders_pkey (id)" in human.stdout
    assert "NOT VALID" in human.stdout
    assert "target outside scan scope" in human.stdout
    assert "More columns exist" in human.stdout
    assert machine.exit_code == 0
    payload = json.loads(machine.stdout)
    assert payload["foreign_keys"][0]["column_pairs"] == [["customer_id", "id"]]
    assert payload["columns_truncated"] is True


def test_show_cli_not_found_exit_code(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    def missing(_root: Path, _name: str, _table: str, _limit: int) -> TableContext:
        raise QueryError("TABLE_NOT_FOUND", "Table was not found.")

    monkeypatch.setattr("graphit.cli.show_table", missing)
    result = runner.invoke(
        app, ["show", "public.missing", "--source", "erp", "--project", str(tmp_path)]
    )
    assert result.exit_code == 6
    assert "TABLE_NOT_FOUND" in result.stderr


def test_relationships_cli_human_json_and_error(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    relation = RelationshipDetail(
        name="payment_order_fk",
        source_table='"public"."payment"',
        target_table='"public"."orders"',
        column_pairs=(("order_id", "id"),),
        target_in_scope=True,
        validated=False,
        inherited=False,
    )

    def found(_root: Path, name: str, table: str, limit: int) -> RelationshipContext:
        assert (name, table, limit) == ("erp", "public.orders", 1)
        return RelationshipContext(
            source_name="erp",
            snapshot_version=4,
            qualified_name='"public"."orders"',
            in_scope=True,
            incoming=(relation,),
            outgoing=(),
            incoming_truncated=True,
            outgoing_truncated=False,
        )

    monkeypatch.setattr("graphit.cli.table_relationships", found)
    args = [
        "relationships",
        "public.orders",
        "--source",
        "erp",
        "--limit",
        "1",
        "--project",
        str(tmp_path),
    ]
    human = runner.invoke(app, args)
    machine = runner.invoke(app, [*args, "--json"])

    assert human.exit_code == 0
    assert "Declared FKs" in human.stdout
    assert "IN\tpayment_order_fk" in human.stdout
    assert "NOT VALID" in human.stdout
    assert "More incoming FKs" in human.stdout
    assert machine.exit_code == 0
    payload = json.loads(machine.stdout)
    assert payload["incoming"][0]["origin"] == "DATABASE"
    assert payload["incoming"][0]["column_pairs"] == [["order_id", "id"]]
    assert payload["incoming_truncated"] is True

    def missing(_root: Path, _name: str, _table: str, _limit: int) -> RelationshipContext:
        raise QueryError("NO_SNAPSHOT", "No successful scan.")

    monkeypatch.setattr("graphit.cli.table_relationships", missing)
    failed = runner.invoke(app, args)
    assert failed.exit_code == 6
    assert "NO_SNAPSHOT" in failed.stderr


def test_path_cli_human_json_and_budget_error(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    def found(_root: Path, name: str, start: str, end: str, hops: int) -> PathResult:
        assert (name, start, end, hops) == ("erp", "public.payment", "public.customer", 3)
        return PathResult(
            source_name="erp",
            snapshot_version=5,
            start_table='"public"."payment"',
            end_table='"public"."customer"',
            steps=(
                PathStep(
                    foreign_key="payment_order_fk",
                    from_table='"public"."payment"',
                    to_table='"public"."orders"',
                    direction="FORWARD",
                    source_table='"public"."payment"',
                    target_table='"public"."orders"',
                    column_pairs=(("order_id", "id"),),
                    target_in_scope=True,
                    validated=True,
                    inherited=False,
                ),
            ),
        )

    monkeypatch.setattr("graphit.cli.find_table_path", found)
    args = [
        "path",
        "public.payment",
        "public.customer",
        "--source",
        "erp",
        "--max-hops",
        "3",
        "--project",
        str(tmp_path),
    ]
    human = runner.invoke(app, args)
    machine = runner.invoke(app, [*args, "--json"])

    assert human.exit_code == 0
    assert "Declared FK path" in human.stdout
    assert "payment_order_fk" in human.stdout
    assert "FORWARD" in human.stdout
    assert machine.exit_code == 0
    payload = json.loads(machine.stdout)
    assert payload["steps"][0]["origin"] == "DATABASE"
    assert payload["steps"][0]["column_pairs"] == [["order_id", "id"]]

    def over_budget(_root: Path, _name: str, _start: str, _end: str, _hops: int) -> PathResult:
        raise QueryError("PATH_BUDGET_EXCEEDED", "Path search examined too many FKs.")

    monkeypatch.setattr("graphit.cli.find_table_path", over_budget)
    failed = runner.invoke(app, args)
    assert failed.exit_code == 7
    assert "PATH_BUDGET_EXCEEDED" in failed.stderr
