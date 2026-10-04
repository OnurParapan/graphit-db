"""Bounded, read-only Oracle Database catalog adapter."""

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
    RelationKind,
    ScanScope,
    SchemaMetadata,
    TableMetadata,
)
from graphit.sources import SourceConfig

CONNECT_TIMEOUT_SECONDS = 5
CALL_TIMEOUT_MS = 5000
_SCHEMAS_SQL = """
SELECT username FROM (
  SELECT username FROM all_users
  WHERE oracle_maintained = 'N'
    AND (
      username = SYS_CONTEXT('USERENV', 'SESSION_USER')
      OR EXISTS (
        SELECT 1 FROM all_objects object
        WHERE object.owner = all_users.username
          AND object.object_type IN ('TABLE', 'VIEW', 'MATERIALIZED VIEW')
      )
    )
  ORDER BY username
) WHERE ROWNUM <= 101
"""

_TABLES_TEMPLATE = """
SELECT owner, object_name, object_type
FROM (
  SELECT owner, object_name, object_type FROM all_objects
  WHERE owner IN ({schemas})
    AND object_type IN ('TABLE', 'VIEW', 'MATERIALIZED VIEW')
  ORDER BY owner, object_name, object_type
) WHERE ROWNUM <= {limit}
"""

_COLUMNS_TEMPLATE = """
SELECT owner, table_name, column_name, column_id, data_type,
       data_length, data_precision, data_scale, char_length, nullable
FROM (
  SELECT owner, table_name, column_name, column_id, data_type,
         data_length, data_precision, data_scale, char_length, nullable
  FROM all_tab_columns
  WHERE owner IN ({schemas})
  ORDER BY owner, table_name, column_id
) WHERE ROWNUM <= {limit}
"""

_CONSTRAINTS_TEMPLATE = """
SELECT owner, table_name, constraint_name, constraint_type, status, validated,
       position, column_name, target_owner, target_table, target_column
FROM (
  SELECT c.owner, c.table_name, c.constraint_name, c.constraint_type,
         c.status, c.validated, cc.position, cc.column_name,
         rc.owner AS target_owner, rc.table_name AS target_table,
         rcc.column_name AS target_column
  FROM all_constraints c
  JOIN all_cons_columns cc
    ON cc.owner = c.owner AND cc.constraint_name = c.constraint_name
       AND cc.table_name = c.table_name
  LEFT JOIN all_constraints rc
    ON rc.owner = c.r_owner AND rc.constraint_name = c.r_constraint_name
  LEFT JOIN all_cons_columns rcc
    ON rcc.owner = rc.owner AND rcc.constraint_name = rc.constraint_name
       AND rcc.table_name = rc.table_name AND rcc.position = cc.position
  WHERE c.owner IN ({schemas}) AND c.constraint_type IN ('P', 'U', 'R')
  ORDER BY c.owner, c.table_name, c.constraint_name, cc.position
) WHERE ROWNUM <= {limit}
"""

_INDEXES_TEMPLATE = """
SELECT table_owner, table_name, index_name, index_type, uniqueness, status,
       column_position, column_name, primary_flag
FROM (
  SELECT i.table_owner, i.table_name, i.index_name, i.index_type,
         i.uniqueness, i.status, ic.column_position, ic.column_name,
         CASE WHEN EXISTS (
           SELECT 1 FROM all_constraints c
           WHERE c.owner = i.owner AND c.index_name = i.index_name
             AND c.constraint_type = 'P'
         ) THEN 1 ELSE 0 END AS primary_flag
  FROM all_indexes i
  JOIN all_ind_columns ic
    ON ic.index_owner = i.owner AND ic.index_name = i.index_name
       AND ic.table_owner = i.table_owner AND ic.table_name = i.table_name
  WHERE i.table_owner IN ({schemas})
    AND i.index_type IN ('NORMAL', 'NORMAL/REV', 'BITMAP',
                         'FUNCTION-BASED NORMAL', 'FUNCTION-BASED NORMAL/REV',
                         'FUNCTION-BASED BITMAP')
  ORDER BY i.table_owner, i.table_name, i.index_name, ic.column_position
) WHERE ROWNUM <= {limit}
"""


def _driver() -> Any:
    try:
        return importlib.import_module("oracledb")
    except ImportError:
        raise ConnectionTestError(
            "DRIVER_NOT_INSTALLED",
            "Oracle support requires python-oracledb; reinstall or upgrade graphit-db.",
        ) from None


def _safe_error(error: Exception) -> ConnectionTestError:
    detail = str(error).lower()
    if any(token in detail for token in ("ora-01017", "invalid credential", "invalid username")):
        return ConnectionTestError("AUTHENTICATION_FAILED", "Oracle authentication failed.")
    if any(token in detail for token in ("dpi-1067", "dpi-1080", "timeout", "timed out")):
        return ConnectionTestError("QUERY_TIMEOUT", "Oracle operation timed out.")
    if any(token in detail for token in ("ora-12170", "ora-12535", "ora-12541")):
        return ConnectionTestError("CONNECTION_TIMEOUT", "Oracle connection timed out.")
    if any(token in detail for token in ("certificate", "ssl", "tls", "ora-29024")):
        return ConnectionTestError("TLS_ERROR", "Oracle TLS negotiation failed.")
    if any(token in detail for token in ("ora-01031", "insufficient privileges")):
        return ConnectionTestError("PERMISSION_DENIED", "Oracle catalog access was denied.")
    return ConnectionTestError("CONNECTION_FAILED", "Could not verify the Oracle connection.")


@contextmanager
def _read_only_connection(source: SourceConfig, project_root: Path | None = None) -> Iterator[Any]:
    try:
        password = resolve_database_password(source, root=project_root)
        module = _driver()
        protocol = "tcps" if source.ssl_mode == "require" else "tcp"
        dsn = f"{protocol}://{source.host}:{source.port}/{source.database_name}"
        params = module.ConnectParams(
            host=source.host,
            port=source.port,
            service_name=source.database_name,
            protocol=protocol,
            tcp_connect_timeout=CONNECT_TIMEOUT_SECONDS,
        )
        connection = module.connect(
            user=source.username,
            password=password,
            dsn=params.get_connect_string() if hasattr(params, "get_connect_string") else dsn,
        )
        connection.call_timeout = CALL_TIMEOUT_MS
    except CredentialResolutionError as error:
        raise ConnectionTestError("MISSING_CREDENTIAL", str(error)) from None
    except ConnectionTestError:
        raise
    except Exception as error:
        raise _safe_error(error) from None
    with closing(connection):
        try:
            with closing(connection.cursor()) as cursor:
                cursor.execute("SET TRANSACTION READ ONLY")
        except Exception as error:
            raise _safe_error(error) from None
        try:
            yield connection
        finally:
            try:
                connection.rollback()
            except Exception:
                pass


def _identity(connection: Any, cursor: Any) -> tuple[str, str, str]:
    cursor.execute(
        "SELECT SYS_CONTEXT('USERENV','SERVICE_NAME'), "
        "SYS_CONTEXT('USERENV','SESSION_USER') FROM dual"
    )
    row = cursor.fetchone()
    if row is None or len(row) < 2 or not row[0] or not row[1]:
        raise ConnectionTestError(
            "INVALID_SERVER_RESULT", "Oracle returned invalid identity metadata."
        )
    username = str(row[1])
    if username.casefold() == "sys":
        raise ConnectionTestError(
            "READ_ONLY_NOT_ENFORCED", "Oracle SYS connections cannot guarantee read-only semantics."
        )
    return str(row[0]), username, str(connection.version)


def verify_connection(
    source: SourceConfig, *, project_root: Path | None = None
) -> ConnectionTestResult:
    """Start an Oracle read-only transaction and discover visible user schemas."""

    try:
        with (
            _read_only_connection(source, project_root) as connection,
            closing(connection.cursor()) as cursor,
        ):
            database, username, version = _identity(connection, cursor)
            cursor.execute(_SCHEMAS_SQL)
            schemas = tuple(str(row[0]) for row in cursor.fetchall())
            if len(schemas) > 100:
                raise ConnectionTestError(
                    "SCHEMA_LIMIT_EXCEEDED", "The database exposes more than 100 user schemas."
                )
            if not schemas:
                raise ConnectionTestError(
                    "NO_ACCESSIBLE_SCHEMA", "No accessible user schema was discovered."
                )
            return ConnectionTestResult(database, username, version, schemas)
    except ConnectionTestError:
        raise
    except Exception as error:
        raise _safe_error(error) from None


def _scope(scope: ScanScope, source: SourceConfig) -> tuple[str, dict[str, str]]:
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
    bindings = {f"s{index}": name for index, name in enumerate(scope.schemas)}
    return ",".join(f":{name}" for name in bindings), bindings


def _oracle_type(row: Any) -> str:
    data_type = str(row[4])
    if data_type in {"CHAR", "NCHAR", "VARCHAR2", "NVARCHAR2"} and row[8] is not None:
        return f"{data_type}({int(row[8])})"
    if data_type == "NUMBER" and row[6] is not None:
        precision = int(row[6])
        return (
            f"NUMBER({precision},{int(row[7])})" if row[7] is not None else f"NUMBER({precision})"
        )
    if data_type in {"RAW"} and row[5] is not None:
        return f"{data_type}({int(row[5])})"
    return data_type


def _constraints(
    rows: list[Any], table_names: set[tuple[str, str]]
) -> tuple[tuple[KeyConstraintMetadata, ...], tuple[ForeignKeyMetadata, ...]]:
    grouped: dict[tuple[str, str, str, str], list[Any]] = {}
    for row in rows:
        grouped.setdefault((str(row[0]), str(row[1]), str(row[2]), str(row[3])), []).append(row)
    keys: list[KeyConstraintMetadata] = []
    foreign_keys: list[ForeignKeyMetadata] = []
    for (schema, table, name, kind), parts in sorted(grouped.items()):
        ordered = sorted(parts, key=lambda item: int(item[6]))
        if [int(item[6]) for item in ordered] != list(range(1, len(ordered) + 1)):
            raise MetadataScanError("INCOMPLETE_CONSTRAINT", "A constraint has incomplete columns.")
        source_columns = tuple(str(item[7]) for item in ordered)
        if kind in {"P", "U"}:
            keys.append(
                KeyConstraintMetadata(
                    schema,
                    table,
                    name,
                    "PRIMARY_KEY" if kind == "P" else "UNIQUE",
                    source_columns,
                    False,
                )
            )
            continue
        target_schema, target_table = str(ordered[0][8]), str(ordered[0][9])
        if kind != "R" or any(
            item[8] is None
            or item[9] is None
            or item[10] is None
            or str(item[8]) != target_schema
            or str(item[9]) != target_table
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
                tuple(str(item[10]) for item in ordered),
                (target_schema, target_table) in table_names,
                all(str(item[4]) == "ENABLED" and str(item[5]) == "VALIDATED" for item in ordered),
                False,
            )
        )
    return tuple(keys), tuple(foreign_keys)


def _indexes(rows: list[Any], columns: set[tuple[str, str, str]]) -> tuple[IndexMetadata, ...]:
    grouped: dict[tuple[str, str, str], list[Any]] = {}
    for row in rows:
        grouped.setdefault((str(row[0]), str(row[1]), str(row[2])), []).append(row)
    result: list[IndexMetadata] = []
    for (schema, table, name), parts in sorted(grouped.items()):
        ordered = sorted(parts, key=lambda item: int(item[6]))
        if [int(item[6]) for item in ordered] != list(range(1, len(ordered) + 1)):
            raise MetadataScanError("INCOMPLETE_INDEX", "A catalog index has incomplete metadata.")
        first = ordered[0]
        result.append(
            IndexMetadata(
                schema,
                table,
                name,
                str(first[3]),
                tuple(
                    str(item[7]) if (schema, table, str(item[7])) in columns else None
                    for item in ordered
                ),
                (),
                str(first[4]) == "UNIQUE",
                bool(first[8]),
                str(first[5]) == "VALID",
                str(first[5]) == "VALID",
                False,
            )
        )
    return tuple(result)


class OracleScanner:
    """Read bounded structural metadata from Oracle ALL_* dictionary views."""

    def __init__(self, project_root: Path | None = None) -> None:
        self.project_root = project_root

    def scan_metadata(self, source: SourceConfig, scope: ScanScope) -> MetadataSnapshot:
        placeholders, bindings = _scope(scope, source)
        try:
            with (
                _read_only_connection(source, self.project_root) as connection,
                closing(connection.cursor()) as cursor,
            ):
                _identity(connection, cursor)
                cursor.execute(_SCHEMAS_SQL)
                visible = {str(row[0]) for row in cursor.fetchall()}
                if not set(scope.schemas).issubset(visible):
                    raise MetadataScanError(
                        "SCHEMA_NOT_FOUND", "One or more selected schemas are not visible."
                    )
                cursor.execute(
                    _TABLES_TEMPLATE.format(schemas=placeholders, limit=scope.max_tables + 1),
                    bindings,
                )
                table_rows = cursor.fetchall()
                cursor.execute(
                    _COLUMNS_TEMPLATE.format(schemas=placeholders, limit=scope.max_columns + 1),
                    bindings,
                )
                column_rows = cursor.fetchall()
                cursor.execute(
                    _CONSTRAINTS_TEMPLATE.format(
                        schemas=placeholders, limit=scope.max_constraints + 1
                    ),
                    bindings,
                )
                constraint_rows = cursor.fetchall()
                cursor.execute(
                    _INDEXES_TEMPLATE.format(schemas=placeholders, limit=scope.max_indexes + 1),
                    bindings,
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
        column_names = {(str(row[0]), str(row[1]), str(row[2])) for row in column_rows}
        keys, foreign_keys = _constraints(list(constraint_rows), table_names)
        kinds: dict[str, RelationKind] = {
            "TABLE": "TABLE",
            "VIEW": "VIEW",
            "MATERIALIZED VIEW": "MATERIALIZED_VIEW",
        }
        return MetadataSnapshot(
            source.name,
            tuple(SchemaMetadata(name) for name in sorted(scope.schemas)),
            tuple(
                TableMetadata(str(row[0]), str(row[1]), False, kinds[str(row[2])])
                for row in table_rows
            ),
            tuple(
                ColumnMetadata(
                    str(row[0]),
                    str(row[1]),
                    str(row[2]),
                    int(row[3]),
                    _oracle_type(row),
                    str(row[9]) == "Y",
                )
                for row in column_rows
            ),
            keys,
            foreign_keys,
            _indexes(list(index_rows), column_names),
        )
