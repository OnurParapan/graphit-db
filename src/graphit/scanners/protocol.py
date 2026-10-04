"""Typed, database-neutral boundary for structural metadata scans."""

from dataclasses import dataclass
from typing import Literal, Protocol

from graphit.sources import SourceConfig

RelationKind = Literal["TABLE", "VIEW", "MATERIALIZED_VIEW"]


def quote_identifier(name: str) -> str:
    """Preserve exact identifier spelling in a display-safe qualified name."""

    return '"' + name.replace('"', '""') + '"'


@dataclass(frozen=True)
class ScanScope:
    """Selected schemas and hard caps for one in-memory catalog scan."""

    schemas: tuple[str, ...]
    max_tables: int = 5000
    max_columns: int = 100000
    max_constraints: int = 100000
    max_indexes: int = 100000


@dataclass(frozen=True)
class SchemaMetadata:
    name: str


@dataclass(frozen=True)
class TableMetadata:
    schema_name: str
    name: str
    partitioned: bool
    kind: RelationKind = "TABLE"

    @property
    def qualified_name(self) -> str:
        return f"{quote_identifier(self.schema_name)}.{quote_identifier(self.name)}"


@dataclass(frozen=True)
class ColumnMetadata:
    schema_name: str
    table_name: str
    name: str
    ordinal_position: int
    data_type: str
    nullable: bool

    @property
    def qualified_name(self) -> str:
        return (
            f"{quote_identifier(self.schema_name)}."
            f"{quote_identifier(self.table_name)}.{quote_identifier(self.name)}"
        )


@dataclass(frozen=True)
class KeyConstraintMetadata:
    schema_name: str
    table_name: str
    name: str
    kind: Literal["PRIMARY_KEY", "UNIQUE"]
    columns: tuple[str, ...]
    inherited: bool


@dataclass(frozen=True)
class ForeignKeyMetadata:
    source_schema: str
    source_table: str
    name: str
    source_columns: tuple[str, ...]
    target_schema: str
    target_table: str
    target_columns: tuple[str, ...]
    target_in_scope: bool
    validated: bool
    inherited: bool


@dataclass(frozen=True)
class IndexMetadata:
    schema_name: str
    table_name: str
    name: str
    access_method: str
    key_columns: tuple[str | None, ...]
    included_columns: tuple[str, ...]
    unique: bool
    primary: bool
    valid: bool
    ready: bool
    partial: bool


@dataclass(frozen=True)
class MetadataSnapshot:
    """Ephemeral scan result; persistence is a later vertical slice."""

    source_name: str
    schemas: tuple[SchemaMetadata, ...]
    tables: tuple[TableMetadata, ...]
    columns: tuple[ColumnMetadata, ...]
    keys: tuple[KeyConstraintMetadata, ...] = ()
    foreign_keys: tuple[ForeignKeyMetadata, ...] = ()
    indexes: tuple[IndexMetadata, ...] = ()


class DatabaseScanner(Protocol):
    """Source adapter independent of CLI, HTTP, MCP, and SQLite."""

    def scan_metadata(self, source: SourceConfig, scope: ScanScope) -> MetadataSnapshot: ...


class ConnectionTestError(Exception):
    """A source adapter could not safely verify a database connection."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class MetadataScanError(Exception):
    """A source adapter could not complete a bounded catalog scan."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ConnectionTestResult:
    """Non-secret identity and safety notices returned by adapter verification."""

    database: str
    username: str
    server_version: str
    schemas: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
