"""Bounded, read-only PostgreSQL connection and catalog scanning."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg

from graphit.discovery import CredentialResolutionError, resolve_postgresql_password
from graphit.scanners.protocol import (
    ColumnMetadata,
    ForeignKeyMetadata,
    IndexMetadata,
    KeyConstraintMetadata,
    MetadataSnapshot,
    RelationKind,
    ScanScope,
    SchemaMetadata,
    TableMetadata,
)
from graphit.sources import SourceConfig

CONNECT_TIMEOUT_SECONDS = 5
STATEMENT_TIMEOUT_MS = 5000
_STARTUP_OPTIONS = (
    "-c default_transaction_read_only=on "
    f"-c statement_timeout={STATEMENT_TIMEOUT_MS} "
    "-c lock_timeout=1000 "
    "-c idle_in_transaction_session_timeout=5000"
)

_SCHEMAS_SQL = """
SELECT n.nspname, pg_catalog.has_schema_privilege(n.oid, 'USAGE')
FROM pg_catalog.pg_namespace AS n
WHERE n.nspname::text = ANY(%s::text[])
ORDER BY n.nspname
"""

_TABLES_SQL = """
SELECT n.nspname, c.relname, c.relkind
FROM pg_catalog.pg_class AS c
JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
WHERE n.nspname::text = ANY(%s::text[])
  AND c.relkind IN ('r', 'p', 'v', 'm')
ORDER BY n.nspname, c.relname
LIMIT %s
"""

_COLUMNS_SQL = """
SELECT n.nspname, c.relname, a.attname, a.attnum,
       pg_catalog.format_type(a.atttypid, a.atttypmod), NOT a.attnotnull
FROM pg_catalog.pg_attribute AS a
JOIN pg_catalog.pg_class AS c ON c.oid = a.attrelid
JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
WHERE n.nspname::text = ANY(%s::text[])
  AND c.relkind IN ('r', 'p', 'v', 'm')
  AND a.attnum > 0
  AND NOT a.attisdropped
ORDER BY n.nspname, c.relname, a.attnum
LIMIT %s
"""

_CONSTRAINTS_SQL = """
SELECT sn.nspname, source_table.relname, con.conname, con.contype,
       con.convalidated, con.conkey,
       ARRAY(
           SELECT a.attname::text
           FROM pg_catalog.unnest(con.conkey) WITH ORDINALITY AS cols(attnum, position)
           JOIN pg_catalog.pg_attribute AS a
             ON a.attrelid = con.conrelid AND a.attnum = cols.attnum
           ORDER BY cols.position
       ) AS source_columns,
       tn.nspname, target_table.relname, con.confkey,
       ARRAY(
           SELECT a.attname::text
           FROM pg_catalog.unnest(con.confkey) WITH ORDINALITY AS cols(attnum, position)
           JOIN pg_catalog.pg_attribute AS a
             ON a.attrelid = con.confrelid AND a.attnum = cols.attnum
           ORDER BY cols.position
       ) AS target_columns,
       con.conparentid <> 0 AS inherited
FROM pg_catalog.pg_constraint AS con
JOIN pg_catalog.pg_class AS source_table ON source_table.oid = con.conrelid
JOIN pg_catalog.pg_namespace AS sn ON sn.oid = source_table.relnamespace
LEFT JOIN pg_catalog.pg_class AS target_table ON target_table.oid = con.confrelid
LEFT JOIN pg_catalog.pg_namespace AS tn ON tn.oid = target_table.relnamespace
WHERE sn.nspname::text = ANY(%s::text[])
  AND source_table.relkind IN ('r', 'p')
  AND con.contype IN ('p', 'u', 'f')
ORDER BY sn.nspname, source_table.relname, con.conname
LIMIT %s
"""

_INDEXES_SQL = """
SELECT n.nspname, source_table.relname, index_table.relname, am.amname,
       idx.indisunique, idx.indisprimary, idx.indisvalid, idx.indisready,
       idx.indpred IS NOT NULL, idx.indnkeyatts, idx.indnatts,
       ARRAY(
           SELECT CASE WHEN cols.attnum = 0 THEN NULL ELSE a.attname::text END
           FROM pg_catalog.unnest(idx.indkey::smallint[])
                WITH ORDINALITY AS cols(attnum, position)
           LEFT JOIN pg_catalog.pg_attribute AS a
             ON a.attrelid = idx.indrelid AND a.attnum = cols.attnum
           ORDER BY cols.position
       ) AS index_columns,
       ARRAY(
           SELECT cols.attnum::integer
           FROM pg_catalog.unnest(idx.indkey::smallint[])
                WITH ORDINALITY AS cols(attnum, position)
           ORDER BY cols.position
       ) AS index_attnums
FROM pg_catalog.pg_index AS idx
JOIN pg_catalog.pg_class AS source_table ON source_table.oid = idx.indrelid
JOIN pg_catalog.pg_namespace AS n ON n.oid = source_table.relnamespace
JOIN pg_catalog.pg_class AS index_table ON index_table.oid = idx.indexrelid
JOIN pg_catalog.pg_am AS am ON am.oid = index_table.relam
WHERE n.nspname::text = ANY(%s::text[])
  AND source_table.relkind IN ('r', 'p', 'm')
  AND index_table.relkind IN ('i', 'I')
  AND idx.indislive
ORDER BY n.nspname, source_table.relname, index_table.relname
LIMIT %s
"""


class ConnectionTestError(Exception):
    """A sanitized PostgreSQL connection-test failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class MetadataScanError(Exception):
    """A sanitized failure of a bounded catalog scan."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ConnectionTestResult:
    """Non-secret identity returned by the read-only verification query."""

    database: str
    username: str
    server_version: str


def _safe_error(error: psycopg.Error) -> ConnectionTestError:
    # Driver diagnostics can contain credentials or connection strings. Never
    # include their raw text in a public exception or log.
    detail = str(error).lower()
    if error.sqlstate in {"28P01", "28000"} or "password authentication failed" in detail:
        return ConnectionTestError("AUTHENTICATION_FAILED", "PostgreSQL authentication failed.")
    if error.sqlstate == "57014":
        return ConnectionTestError("QUERY_TIMEOUT", "PostgreSQL verification query timed out.")
    if isinstance(error, psycopg.errors.ConnectionTimeout) or any(
        phrase in detail for phrase in ("timed out", "timeout", "timeout expired")
    ):
        return ConnectionTestError("CONNECTION_TIMEOUT", "PostgreSQL connection timed out.")
    if any(phrase in detail for phrase in ("ssl", "tls", "certificate")):
        return ConnectionTestError("TLS_ERROR", "PostgreSQL TLS negotiation failed.")
    if error.sqlstate == "42501":
        return ConnectionTestError("PERMISSION_DENIED", "PostgreSQL catalog access was denied.")
    return ConnectionTestError("CONNECTION_FAILED", "Could not verify the PostgreSQL connection.")


@contextmanager
def _read_only_connection(
    source: SourceConfig, project_root: Path | None = None
) -> Iterator[psycopg.Connection[tuple[Any, ...]]]:
    """Open a bounded read-only session shared by verification and scanning."""

    try:
        password = resolve_postgresql_password(source, root=project_root)
    except CredentialResolutionError as error:
        raise ConnectionTestError("MISSING_CREDENTIAL", str(error)) from None
    with psycopg.connect(
        host=source.host,
        port=source.port,
        dbname=source.database_name,
        user=source.username,
        password=password,
        sslmode=source.ssl_mode,
        connect_timeout=CONNECT_TIMEOUT_SECONDS,
        options=_STARTUP_OPTIONS,
        application_name="graphit",
    ) as connection:
        connection.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        connection.read_only = True
        yield connection


def _assert_read_only(connection: psycopg.Connection[tuple[Any, ...]]) -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_catalog.current_setting('transaction_read_only')")
        row = cursor.fetchone()
        if row is None or row[0] != "on":
            raise ConnectionTestError(
                "READ_ONLY_NOT_ENFORCED",
                "PostgreSQL did not confirm a read-only transaction.",
            )


def verify_connection(
    source: SourceConfig, *, project_root: Path | None = None
) -> ConnectionTestResult:
    """Connect only for one bounded SELECT inside a read-only transaction."""

    try:
        with (
            _read_only_connection(source, project_root) as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "SELECT pg_catalog.current_database(), current_user, "
                "pg_catalog.current_setting('transaction_read_only'), "
                "pg_catalog.current_setting('server_version')"
            )
            row = cursor.fetchone()
            if row is None or row[2] != "on":
                raise ConnectionTestError(
                    "READ_ONLY_NOT_ENFORCED",
                    "PostgreSQL did not confirm a read-only transaction.",
                )
            return ConnectionTestResult(str(row[0]), str(row[1]), str(row[3]))
    except psycopg.Error as error:
        raise _safe_error(error) from None


class PostgreSQLScanner:
    """Read a bounded structural slice from PostgreSQL system catalogs."""

    def __init__(self, project_root: Path | None = None) -> None:
        self.project_root = project_root

    def scan_metadata(self, source: SourceConfig, scope: ScanScope) -> MetadataSnapshot:
        if (
            not scope.schemas
            or len(scope.schemas) > 100
            or len(set(scope.schemas)) != len(scope.schemas)
            or any(not name or "\x00" in name for name in scope.schemas)
            or not 1 <= scope.max_tables <= 5000
            or not 1 <= scope.max_columns <= 100000
            or not 1 <= scope.max_constraints <= 100000
            or not 1 <= scope.max_indexes <= 100000
        ):
            raise MetadataScanError("INVALID_SCOPE", "Scan scope or result limits are invalid.")
        if not set(scope.schemas).issubset(source.schemas):
            raise MetadataScanError("INVALID_SCOPE", "Scan scope exceeds configured schemas.")

        names = list(scope.schemas)
        try:
            with _read_only_connection(source, self.project_root) as connection:
                _assert_read_only(connection)
                with connection.cursor() as cursor:
                    cursor.execute(_SCHEMAS_SQL, (names,))
                    schema_rows = cursor.fetchall()
                    visible = {str(row[0]) for row in schema_rows}
                    if visible != set(scope.schemas):
                        raise MetadataScanError(
                            "SCHEMA_NOT_FOUND", "One or more selected schemas do not exist."
                        )
                    if any(not row[1] for row in schema_rows):
                        raise MetadataScanError(
                            "SCHEMA_PERMISSION_DENIED",
                            "The source account lacks USAGE on a selected schema.",
                        )

                    cursor.execute(_TABLES_SQL, (names, scope.max_tables + 1))
                    table_rows = cursor.fetchall()
                    if len(table_rows) > scope.max_tables:
                        raise MetadataScanError(
                            "TABLE_LIMIT_EXCEEDED", "The selected schemas exceed the table limit."
                        )

                    cursor.execute(_COLUMNS_SQL, (names, scope.max_columns + 1))
                    column_rows = cursor.fetchall()
                    if len(column_rows) > scope.max_columns:
                        raise MetadataScanError(
                            "COLUMN_LIMIT_EXCEEDED", "The selected schemas exceed the column limit."
                        )

                    cursor.execute(_CONSTRAINTS_SQL, (names, scope.max_constraints + 1))
                    constraint_rows = cursor.fetchall()
                    if len(constraint_rows) > scope.max_constraints:
                        raise MetadataScanError(
                            "CONSTRAINT_LIMIT_EXCEEDED",
                            "The selected schemas exceed the constraint limit.",
                        )

                    cursor.execute(_INDEXES_SQL, (names, scope.max_indexes + 1))
                    index_rows = cursor.fetchall()
                    if len(index_rows) > scope.max_indexes:
                        raise MetadataScanError(
                            "INDEX_LIMIT_EXCEEDED", "The selected schemas exceed the index limit."
                        )

            table_names = {(str(row[0]), str(row[1])) for row in table_rows}
            keys: list[KeyConstraintMetadata] = []
            foreign_keys: list[ForeignKeyMetadata] = []
            for row in constraint_rows:
                source_columns = tuple(str(name) for name in row[6])
                if not row[5] or len(source_columns) != len(row[5]):
                    raise MetadataScanError(
                        "INCOMPLETE_CONSTRAINT", "A source constraint has incomplete columns."
                    )
                if row[3] in ("p", "u"):
                    keys.append(
                        KeyConstraintMetadata(
                            schema_name=str(row[0]),
                            table_name=str(row[1]),
                            name=str(row[2]),
                            kind="PRIMARY_KEY" if row[3] == "p" else "UNIQUE",
                            columns=source_columns,
                            inherited=bool(row[11]),
                        )
                    )
                elif row[3] == "f":
                    target_columns = tuple(str(name) for name in row[10])
                    if (
                        row[7] is None
                        or row[8] is None
                        or not row[9]
                        or len(target_columns) != len(row[9])
                        or len(source_columns) != len(target_columns)
                    ):
                        raise MetadataScanError(
                            "INCOMPLETE_CONSTRAINT",
                            "A foreign key has incomplete target columns.",
                        )
                    target_schema, target_table = str(row[7]), str(row[8])
                    foreign_keys.append(
                        ForeignKeyMetadata(
                            source_schema=str(row[0]),
                            source_table=str(row[1]),
                            name=str(row[2]),
                            source_columns=source_columns,
                            target_schema=target_schema,
                            target_table=target_table,
                            target_columns=target_columns,
                            target_in_scope=(target_schema, target_table) in table_names,
                            validated=bool(row[4]),
                            inherited=bool(row[11]),
                        )
                    )
                else:
                    raise MetadataScanError(
                        "INCOMPLETE_CONSTRAINT", "An unsupported constraint type was returned."
                    )

            relation_kinds: dict[str, RelationKind] = {
                "r": "TABLE",
                "p": "TABLE",
                "v": "VIEW",
                "m": "MATERIALIZED_VIEW",
            }
            if any(str(row[2]) not in relation_kinds for row in table_rows):
                raise MetadataScanError(
                    "INVALID_METADATA", "A catalog relation has an unsupported kind."
                )

            indexes: list[IndexMetadata] = []
            for row in index_rows:
                column_names = row[11]
                attribute_numbers = row[12]
                key_count, total_count = row[9], row[10]
                if (
                    (str(row[0]), str(row[1])) not in table_names
                    or not isinstance(column_names, list)
                    or not isinstance(attribute_numbers, list)
                    or not isinstance(key_count, int)
                    or not isinstance(total_count, int)
                    or not 1 <= key_count <= total_count == len(column_names)
                    or len(attribute_numbers) != total_count
                    or any(
                        not isinstance(number, int)
                        or number < 0
                        or (number == 0 and name is not None)
                        or (number > 0 and (not isinstance(name, str) or not name))
                        for number, name in zip(attribute_numbers, column_names, strict=True)
                    )
                    or any(number == 0 for number in attribute_numbers[key_count:])
                    or bool(row[5])
                    and not bool(row[4])
                ):
                    raise MetadataScanError(
                        "INCOMPLETE_INDEX", "A catalog index has incomplete metadata."
                    )
                indexes.append(
                    IndexMetadata(
                        schema_name=str(row[0]),
                        table_name=str(row[1]),
                        name=str(row[2]),
                        access_method=str(row[3]),
                        key_columns=tuple(column_names[:key_count]),
                        included_columns=tuple(column_names[key_count:]),
                        unique=bool(row[4]),
                        primary=bool(row[5]),
                        valid=bool(row[6]),
                        ready=bool(row[7]),
                        partial=bool(row[8]),
                    )
                )

            return MetadataSnapshot(
                source_name=source.name,
                schemas=tuple(SchemaMetadata(str(row[0])) for row in schema_rows),
                tables=tuple(
                    TableMetadata(
                        str(row[0]),
                        str(row[1]),
                        row[2] == "p",
                        relation_kinds[str(row[2])],
                    )
                    for row in table_rows
                ),
                columns=tuple(
                    ColumnMetadata(
                        schema_name=str(row[0]),
                        table_name=str(row[1]),
                        name=str(row[2]),
                        ordinal_position=int(row[3]),
                        data_type=str(row[4]),
                        nullable=bool(row[5]),
                    )
                    for row in column_rows
                ),
                keys=tuple(keys),
                foreign_keys=tuple(foreign_keys),
                indexes=tuple(indexes),
            )
        except ConnectionTestError as error:
            raise MetadataScanError(error.code, str(error)) from None
        except psycopg.Error as error:
            safe = _safe_error(error)
            raise MetadataScanError(safe.code, str(safe)) from None
