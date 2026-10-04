"""Bounded, read-only Microsoft SQL Server catalog adapter."""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any

from graphit.discovery import CredentialResolutionError, resolve_database_password
from graphit.scanners.protocol import (
    ColumnMetadata,
    ConnectionTestError,
    ConnectionTestResult,
    ForeignKeyMetadata,
    IndexMetadata,
    KeyConstraintMetadata,
    MetadataScanError,
    MetadataSnapshot,
    ScanScope,
    SchemaMetadata,
    TableMetadata,
)
from graphit.sources import SourceConfig

CONNECT_TIMEOUT_SECONDS = 5
QUERY_TIMEOUT_SECONDS = 5
_DRIVERS = ("ODBC Driver 18 for SQL Server", "ODBC Driver 17 for SQL Server")

_IDENTITY_SQL = """
SELECT DB_NAME(), SYSTEM_USER, CAST(SERVERPROPERTY('ProductVersion') AS nvarchar(128))
"""

_WRITE_PERMISSION_SQL = """
SELECT CASE WHEN
  IS_SRVROLEMEMBER('sysadmin') = 1 OR
  IS_MEMBER('db_owner') = 1 OR IS_MEMBER('db_datawriter') = 1 OR
  IS_MEMBER('db_ddladmin') = 1 OR IS_MEMBER('db_securityadmin') = 1 OR
  IS_MEMBER('db_accessadmin') = 1 OR
  EXISTS (
    SELECT 1
    FROM sys.database_permissions AS p
    JOIN sys.user_token AS token ON token.principal_id = p.grantee_principal_id
    WHERE p.state IN ('G', 'W')
      AND (
        (p.class = 0 AND p.permission_name IN
          ('CONTROL', 'ALTER', 'CREATE TABLE', 'INSERT', 'UPDATE', 'DELETE'))
        OR
        (p.class IN (1, 3) AND p.permission_name IN
          ('CONTROL', 'ALTER', 'INSERT', 'UPDATE', 'DELETE'))
      )
  ) OR EXISTS (
    SELECT 1
    FROM sys.schemas AS s
    JOIN sys.user_token AS token ON token.principal_id = s.principal_id
    WHERE s.name NOT IN ('sys', 'INFORMATION_SCHEMA')
  ) THEN 1 ELSE 0 END
"""

_SCHEMAS_TEMPLATE = """
SELECT DISTINCT TOP ({limit}) s.name
FROM sys.schemas AS s
JOIN sys.objects AS o ON o.schema_id = s.schema_id
WHERE o.type IN ('U', 'V') AND o.is_ms_shipped = 0
  AND s.name NOT IN ('sys', 'INFORMATION_SCHEMA')
ORDER BY s.name
"""

_TABLES_TEMPLATE = """
SELECT TOP ({limit}) s.name, o.name, o.type,
       CASE WHEN o.type = 'U' AND EXISTS (
         SELECT 1 FROM sys.indexes AS i
         JOIN sys.partition_schemes AS ps ON ps.data_space_id = i.data_space_id
         WHERE i.object_id = o.object_id
       ) THEN 1 ELSE 0 END
FROM sys.objects AS o
JOIN sys.schemas AS s ON s.schema_id = o.schema_id
WHERE s.name IN ({schemas}) AND o.type IN ('U', 'V') AND o.is_ms_shipped = 0
ORDER BY s.name, o.name
"""

_COLUMNS_TEMPLATE = """
SELECT TOP ({limit}) s.name, o.name, c.name, c.column_id,
       CASE
         WHEN t.name IN ('varchar','char','varbinary','binary') THEN
           t.name + '(' + CASE WHEN c.max_length = -1 THEN 'max'
                               ELSE CAST(c.max_length AS varchar(10)) END + ')'
         WHEN t.name IN ('nvarchar','nchar') THEN
           t.name + '(' + CASE WHEN c.max_length = -1 THEN 'max'
                               ELSE CAST(c.max_length / 2 AS varchar(10)) END + ')'
         WHEN t.name IN ('decimal','numeric') THEN
           t.name + '(' + CAST(c.precision AS varchar(10)) + ','
                  + CAST(c.scale AS varchar(10)) + ')'
         ELSE t.name
       END,
       c.is_nullable
FROM sys.columns AS c
JOIN sys.objects AS o ON o.object_id = c.object_id
JOIN sys.schemas AS s ON s.schema_id = o.schema_id
JOIN sys.types AS t ON t.user_type_id = c.user_type_id
WHERE s.name IN ({schemas}) AND o.type IN ('U', 'V') AND o.is_ms_shipped = 0
ORDER BY s.name, o.name, c.column_id
"""

_CONSTRAINTS_TEMPLATE = """
SELECT TOP ({limit}) * FROM (
  SELECT s.name AS source_schema, t.name AS source_table, kc.name AS constraint_name,
         kc.type AS constraint_type, 1 AS validated, ic.key_ordinal AS position,
         c.name AS source_column, NULL AS target_schema, NULL AS target_table,
         NULL AS target_column, 0 AS disabled, 0 AS inherited
  FROM sys.key_constraints AS kc
  JOIN sys.tables AS t ON t.object_id = kc.parent_object_id
  JOIN sys.schemas AS s ON s.schema_id = t.schema_id
  JOIN sys.index_columns AS ic
    ON ic.object_id = kc.parent_object_id AND ic.index_id = kc.unique_index_id
  JOIN sys.columns AS c ON c.object_id = ic.object_id AND c.column_id = ic.column_id
  WHERE s.name IN ({schemas}) AND ic.key_ordinal > 0
  UNION ALL
  SELECT ss.name, st.name, fk.name, 'F', CASE WHEN fk.is_not_trusted = 0 THEN 1 ELSE 0 END,
         fkc.constraint_column_id, sc.name, ts.name, tt.name, tc.name, fk.is_disabled, 0
  FROM sys.foreign_keys AS fk
  JOIN sys.foreign_key_columns AS fkc ON fkc.constraint_object_id = fk.object_id
  JOIN sys.tables AS st ON st.object_id = fk.parent_object_id
  JOIN sys.schemas AS ss ON ss.schema_id = st.schema_id
  JOIN sys.columns AS sc
    ON sc.object_id = fkc.parent_object_id AND sc.column_id = fkc.parent_column_id
  JOIN sys.tables AS tt ON tt.object_id = fk.referenced_object_id
  JOIN sys.schemas AS ts ON ts.schema_id = tt.schema_id
  JOIN sys.columns AS tc
    ON tc.object_id = fkc.referenced_object_id AND tc.column_id = fkc.referenced_column_id
  WHERE ss.name IN ({schemas})
) AS catalog_constraints
ORDER BY source_schema, source_table, constraint_name, position
"""

_INDEXES_TEMPLATE = """
SELECT TOP ({limit}) s.name, t.name, i.name, i.type_desc, i.is_unique,
       i.is_primary_key, CASE WHEN i.is_disabled = 0 THEN 1 ELSE 0 END,
       CASE WHEN i.is_disabled = 0 THEN 1 ELSE 0 END, i.has_filter,
       ic.key_ordinal, ic.is_included_column, c.name
FROM sys.indexes AS i
JOIN sys.tables AS t ON t.object_id = i.object_id
JOIN sys.schemas AS s ON s.schema_id = t.schema_id
JOIN sys.index_columns AS ic ON ic.object_id = i.object_id AND ic.index_id = i.index_id
JOIN sys.columns AS c ON c.object_id = ic.object_id AND c.column_id = ic.column_id
WHERE s.name IN ({schemas}) AND i.type IN (1, 2) AND i.is_hypothetical = 0
ORDER BY s.name, t.name, i.name, ic.is_included_column, ic.key_ordinal, ic.index_column_id
"""


def _driver() -> Any:
    try:
        return importlib.import_module("pyodbc")
    except ImportError:
        raise ConnectionTestError(
            "DRIVER_NOT_INSTALLED",
            "Microsoft SQL Server support requires pyodbc; reinstall or upgrade graphit-db.",
        ) from None


def _odbc_value(value: str) -> str:
    return "{" + value.replace("}", "}}") + "}"


def _selected_driver(module: Any) -> str:
    installed = set(module.drivers())
    for name in _DRIVERS:
        if name in installed:
            return name
    raise ConnectionTestError(
        "ODBC_DRIVER_NOT_FOUND",
        "Install Microsoft ODBC Driver 18 (or 17) for SQL Server.",
    )


def _safe_error(error: Exception) -> ConnectionTestError:
    detail = str(error).lower()
    if any(token in detail for token in ("28000", "18456", "login failed")):
        return ConnectionTestError("AUTHENTICATION_FAILED", "SQL Server authentication failed.")
    if any(token in detail for token in ("hyt00", "hyt01", "login timeout", "timed out")):
        return ConnectionTestError("CONNECTION_TIMEOUT", "SQL Server connection timed out.")
    if any(token in detail for token in ("certificate", "ssl", "tls")):
        return ConnectionTestError("TLS_ERROR", "SQL Server TLS negotiation failed.")
    if any(token in detail for token in ("42000", "permission", "denied")):
        return ConnectionTestError("PERMISSION_DENIED", "SQL Server catalog access was denied.")
    return ConnectionTestError("CONNECTION_FAILED", "Could not verify the SQL Server connection.")


@contextmanager
def _read_only_connection(source: SourceConfig, project_root: Path | None = None) -> Iterator[Any]:
    try:
        password = resolve_database_password(source, root=project_root)
        module = _driver()
        driver = _selected_driver(module)
        encrypt = "no" if source.ssl_mode == "disable" else "yes"
        trust_certificate = "yes" if source.ssl_mode == "require-trust-server-certificate" else "no"
        server_host = f"[{source.host}]" if ":" in source.host else source.host
        connection_string = (
            f"Driver={_odbc_value(driver)};"
            f"Server=tcp:{server_host},{source.port};"
            f"Database={_odbc_value(source.database_name)};"
            f"UID={_odbc_value(source.username)};PWD={_odbc_value(password)};"
            f"Encrypt={encrypt};TrustServerCertificate={trust_certificate};APP=Graphit;"
        )
        connection = module.connect(
            connection_string,
            autocommit=False,
            timeout=CONNECT_TIMEOUT_SECONDS,
            readonly=True,
        )
    except CredentialResolutionError as error:
        raise ConnectionTestError("MISSING_CREDENTIAL", str(error)) from None
    except ConnectionTestError:
        raise
    except Exception as error:
        raise _safe_error(error) from None
    with closing(connection):
        try:
            yield connection
        finally:
            try:
                connection.rollback()
            except Exception:
                pass


def _cursor(connection: Any) -> Any:
    connection.timeout = QUERY_TIMEOUT_SECONDS
    cursor = connection.cursor()
    return cursor


def _principal_identity(cursor: Any) -> tuple[str, str, str, bool]:
    cursor.execute(_IDENTITY_SQL)
    row = cursor.fetchone()
    if row is None or len(row) < 3:
        raise ConnectionTestError(
            "INVALID_SERVER_RESULT", "SQL Server returned invalid identity metadata."
        )
    cursor.execute(_WRITE_PERMISSION_SQL)
    permission_row = cursor.fetchone()
    if permission_row is None:
        raise ConnectionTestError(
            "INVALID_SERVER_RESULT", "SQL Server returned invalid permission metadata."
        )
    return str(row[0]), str(row[1]), str(row[2]), bool(permission_row[0])


def verify_connection(
    source: SourceConfig, *, project_root: Path | None = None
) -> ConnectionTestResult:
    """Verify a SQL Server principal and discover at most 100 visible user schemas."""

    try:
        with (
            _read_only_connection(source, project_root) as connection,
            closing(_cursor(connection)) as cursor,
        ):
            database, username, version, has_write_permissions = _principal_identity(cursor)
            cursor.execute(_SCHEMAS_TEMPLATE.format(limit=101))
            schemas = tuple(str(row[0]) for row in cursor.fetchall())
            if len(schemas) > 100:
                raise ConnectionTestError(
                    "SCHEMA_LIMIT_EXCEEDED", "The database exposes more than 100 user schemas."
                )
            if not schemas:
                raise ConnectionTestError(
                    "NO_ACCESSIBLE_SCHEMA", "No accessible user schema was discovered."
                )
            warnings = (
                (
                    (
                        "PRIVILEGED_CREDENTIAL: this SQL Server principal has write permissions; "
                        "Graphit will still execute only fixed, bounded metadata SELECTs."
                    ),
                )
                if has_write_permissions
                else ()
            )
            return ConnectionTestResult(database, username, version, schemas, warnings)
    except ConnectionTestError:
        raise
    except Exception as error:
        raise _safe_error(error) from None


def _scope_values(scope: ScanScope, source: SourceConfig) -> tuple[str, dict[str, str]]:
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
    names = {f"s{index}": name for index, name in enumerate(scope.schemas)}
    return ",".join("?" for _ in names), names


def _group_constraints(
    rows: list[Any], table_names: set[tuple[str, str]]
) -> tuple[tuple[KeyConstraintMetadata, ...], tuple[ForeignKeyMetadata, ...]]:
    grouped: dict[tuple[str, str, str, str], list[Any]] = {}
    for row in rows:
        grouped.setdefault((str(row[0]), str(row[1]), str(row[2]), str(row[3]).strip()), []).append(
            row
        )
    keys: list[KeyConstraintMetadata] = []
    foreign_keys: list[ForeignKeyMetadata] = []
    for (schema, table, name, kind), parts in sorted(grouped.items()):
        ordered = sorted(parts, key=lambda item: int(item[5]))
        if [int(item[5]) for item in ordered] != list(range(1, len(ordered) + 1)):
            raise MetadataScanError("INCOMPLETE_CONSTRAINT", "A constraint has incomplete columns.")
        source_columns = tuple(str(item[6]) for item in ordered)
        if kind in {"PK", "UQ"}:
            keys.append(
                KeyConstraintMetadata(
                    schema,
                    table,
                    name,
                    "PRIMARY_KEY" if kind == "PK" else "UNIQUE",
                    source_columns,
                    False,
                )
            )
            continue
        target_schema, target_table = str(ordered[0][7]), str(ordered[0][8])
        if kind != "F" or any(
            str(item[7]) != target_schema or str(item[8]) != target_table or item[9] is None
            for item in ordered
        ):
            raise MetadataScanError("INCOMPLETE_CONSTRAINT", "A foreign key is incomplete.")
        foreign_keys.append(
            ForeignKeyMetadata(
                schema,
                table,
                name,
                source_columns,
                target_schema,
                target_table,
                tuple(str(item[9]) for item in ordered),
                (target_schema, target_table) in table_names,
                all(bool(item[4]) and not bool(item[10]) for item in ordered),
                False,
            )
        )
    return tuple(keys), tuple(foreign_keys)


def _group_indexes(rows: list[Any]) -> tuple[IndexMetadata, ...]:
    grouped: dict[tuple[str, str, str], list[Any]] = {}
    for row in rows:
        grouped.setdefault((str(row[0]), str(row[1]), str(row[2])), []).append(row)
    indexes: list[IndexMetadata] = []
    for (schema, table, name), parts in sorted(grouped.items()):
        key_parts = sorted(
            (item for item in parts if not bool(item[10])), key=lambda item: int(item[9])
        )
        included = sorted(
            (item for item in parts if bool(item[10])), key=lambda item: str(item[11])
        )
        if not key_parts or [int(item[9]) for item in key_parts] != list(
            range(1, len(key_parts) + 1)
        ):
            raise MetadataScanError("INCOMPLETE_INDEX", "A catalog index has incomplete metadata.")
        first = parts[0]
        indexes.append(
            IndexMetadata(
                schema,
                table,
                name,
                str(first[3]),
                tuple(str(item[11]) for item in key_parts),
                tuple(str(item[11]) for item in included),
                bool(first[4]),
                bool(first[5]),
                bool(first[6]),
                bool(first[7]),
                bool(first[8]),
            )
        )
    return tuple(indexes)


class MSSQLScanner:
    """Read bounded structural metadata from SQL Server system catalogs."""

    def __init__(self, project_root: Path | None = None) -> None:
        self.project_root = project_root

    def scan_metadata(self, source: SourceConfig, scope: ScanScope) -> MetadataSnapshot:
        placeholders, bindings = _scope_values(scope, source)
        parameters = tuple(bindings.values())
        try:
            with (
                _read_only_connection(source, self.project_root) as connection,
                closing(_cursor(connection)) as cursor,
            ):
                _principal_identity(cursor)
                cursor.execute(_SCHEMAS_TEMPLATE.format(limit=101))
                visible = {str(row[0]) for row in cursor.fetchall()}
                if not set(scope.schemas).issubset(visible):
                    raise MetadataScanError(
                        "SCHEMA_NOT_FOUND", "One or more selected schemas are not visible."
                    )
                cursor.execute(
                    _TABLES_TEMPLATE.format(limit=scope.max_tables + 1, schemas=placeholders),
                    *parameters,
                )
                table_rows = cursor.fetchall()
                cursor.execute(
                    _COLUMNS_TEMPLATE.format(limit=scope.max_columns + 1, schemas=placeholders),
                    *parameters,
                )
                column_rows = cursor.fetchall()
                cursor.execute(
                    _CONSTRAINTS_TEMPLATE.format(
                        limit=scope.max_constraints + 1, schemas=placeholders
                    ),
                    *(parameters + parameters),
                )
                constraint_rows = cursor.fetchall()
                cursor.execute(
                    _INDEXES_TEMPLATE.format(limit=scope.max_indexes + 1, schemas=placeholders),
                    *parameters,
                )
                index_rows = cursor.fetchall()
        except MetadataScanError:
            raise
        except ConnectionTestError as error:
            raise MetadataScanError(error.code, str(error)) from None
        except Exception as error:
            safe = _safe_error(error)
            raise MetadataScanError(safe.code, str(safe)) from None

        for rows, limit, code, label in (
            (table_rows, scope.max_tables, "TABLE_LIMIT_EXCEEDED", "table"),
            (column_rows, scope.max_columns, "COLUMN_LIMIT_EXCEEDED", "column"),
            (constraint_rows, scope.max_constraints, "CONSTRAINT_LIMIT_EXCEEDED", "constraint"),
            (index_rows, scope.max_indexes, "INDEX_LIMIT_EXCEEDED", "index"),
        ):
            if len(rows) > limit:
                raise MetadataScanError(code, f"The selected schemas exceed the {label} limit.")

        table_names = {(str(row[0]), str(row[1])) for row in table_rows}
        keys, foreign_keys = _group_constraints(list(constraint_rows), table_names)
        return MetadataSnapshot(
            source.name,
            tuple(SchemaMetadata(name) for name in sorted(scope.schemas)),
            tuple(
                TableMetadata(
                    str(row[0]),
                    str(row[1]),
                    bool(row[3]),
                    "TABLE" if str(row[2]).strip() == "U" else "VIEW",
                )
                for row in table_rows
            ),
            tuple(
                ColumnMetadata(
                    str(row[0]), str(row[1]), str(row[2]), int(row[3]), str(row[4]), bool(row[5])
                )
                for row in column_rows
            ),
            keys,
            foreign_keys,
            _group_indexes(list(index_rows)),
        )
