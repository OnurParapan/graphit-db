"""PostgreSQL connection-test safety without a live target server."""

from typing import Any

import psycopg
import pytest
from pytest import MonkeyPatch

from graphit.scanners.postgresql import ConnectionTestError, verify_connection
from graphit.sources import SourceConfig


def _source() -> SourceConfig:
    return SourceConfig(
        name="claims",
        host="db.example.test",
        port=5432,
        database_name="claims_db",
        username="reader",
        credential_env="CLAIMS_DB_PASSWORD",
        schemas=("public",),
        ssl_mode="require",
    )


class FakeCursor:
    def __init__(self, row: tuple[str, str, str, str] | None) -> None:
        self.row = row
        self.query: str | None = None

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, query: str) -> None:
        self.query = query

    def fetchone(self) -> tuple[str, str, str, str] | None:
        return self.row


class FakeConnection:
    def __init__(self, row: tuple[str, str, str, str] | None) -> None:
        self.read_only = False
        self.isolation_level: psycopg.IsolationLevel | None = None
        self.cursor_object = FakeCursor(row)
        self.closed = False

    def __enter__(self) -> "FakeConnection":
        return self

    def __exit__(self, *_args: object) -> None:
        self.closed = True

    def cursor(self) -> FakeCursor:
        assert self.read_only is True
        return self.cursor_object


def test_one_bounded_read_only_query_and_no_persisted_secret(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("CLAIMS_DB_PASSWORD", "very-private-value")
    fake = FakeConnection(("claims_db", "reader", "on", "16.2"))
    connection_args: dict[str, Any] = {}

    def connect(**kwargs: Any) -> FakeConnection:
        connection_args.update(kwargs)
        return fake

    monkeypatch.setattr("graphit.scanners.postgresql.psycopg.connect", connect)

    result = verify_connection(_source())

    assert result.database == "claims_db"
    assert result.username == "reader"
    assert result.server_version == "16.2"
    assert connection_args["password"] == "very-private-value"
    assert connection_args["sslmode"] == "require"
    assert connection_args["connect_timeout"] == 5
    assert "default_transaction_read_only=on" in connection_args["options"]
    assert "statement_timeout=5000" in connection_args["options"]
    assert fake.cursor_object.query is not None
    assert fake.cursor_object.query.startswith("SELECT ")
    assert fake.closed is True
    assert fake.isolation_level == psycopg.IsolationLevel.REPEATABLE_READ
    assert "very-private-value" not in repr(result)


def test_missing_credential_does_not_attempt_connection(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv("CLAIMS_DB_PASSWORD", raising=False)

    def must_not_connect(**_kwargs: Any) -> None:
        pytest.fail("connection must not be attempted")

    monkeypatch.setattr("graphit.scanners.postgresql.psycopg.connect", must_not_connect)

    with pytest.raises(ConnectionTestError) as raised:
        verify_connection(_source())
    assert raised.value.code == "MISSING_CREDENTIAL"


@pytest.mark.parametrize(
    ("driver_message", "expected_code"),
    [
        (
            "password authentication failed for user, secret=very-private-value",
            "AUTHENTICATION_FAILED",
        ),
        ("connection timed out, secret=very-private-value", "CONNECTION_TIMEOUT"),
        ("SSL certificate verify failed, secret=very-private-value", "TLS_ERROR"),
        ("could not connect, secret=very-private-value", "CONNECTION_FAILED"),
    ],
)
def test_driver_errors_are_classified_without_exposing_secrets(
    monkeypatch: MonkeyPatch, driver_message: str, expected_code: str
) -> None:
    monkeypatch.setenv("CLAIMS_DB_PASSWORD", "very-private-value")

    def fail(**_kwargs: Any) -> None:
        raise psycopg.OperationalError(driver_message)

    monkeypatch.setattr("graphit.scanners.postgresql.psycopg.connect", fail)

    with pytest.raises(ConnectionTestError) as raised:
        verify_connection(_source())
    assert raised.value.code == expected_code
    assert "very-private-value" not in str(raised.value)
    assert "secret=" not in str(raised.value)


def test_statement_timeout_has_distinct_code(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("CLAIMS_DB_PASSWORD", "very-private-value")

    def fail(**_kwargs: Any) -> None:
        raise psycopg.errors.QueryCanceled("canceling statement due to statement timeout")

    monkeypatch.setattr("graphit.scanners.postgresql.psycopg.connect", fail)

    with pytest.raises(ConnectionTestError) as raised:
        verify_connection(_source())
    assert raised.value.code == "QUERY_TIMEOUT"


@pytest.mark.parametrize("row", [None, ("claims_db", "reader", "off", "16.2")])
def test_unconfirmed_read_only_state_fails_closed(
    monkeypatch: MonkeyPatch, row: tuple[str, str, str, str] | None
) -> None:
    monkeypatch.setenv("CLAIMS_DB_PASSWORD", "very-private-value")
    fake = FakeConnection(row)
    monkeypatch.setattr("graphit.scanners.postgresql.psycopg.connect", lambda **_: fake)

    with pytest.raises(ConnectionTestError) as raised:
        verify_connection(_source())

    assert raised.value.code == "READ_ONLY_NOT_ENFORCED"
    assert fake.closed is True
