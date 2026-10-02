"""Bounded, snapshot-aware queries over the project-local knowledge graph."""

import json
import re
import sqlite3
from collections import deque
from contextlib import closing
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from graphit.scanners.protocol import quote_identifier
from graphit.sources import require_store_path, show_source

DEFAULT_SEARCH_LIMIT = 20
MAX_SEARCH_LIMIT = 100
MAX_SEARCH_TERMS = 8
MAX_QUERY_LENGTH = 200
DEFAULT_OVERVIEW_LIMIT = 10
MAX_OVERVIEW_LIMIT = 50
DEFAULT_CONTEXT_OBJECTS = 8
MAX_CONTEXT_OBJECTS = 20
DEFAULT_CONTEXT_FOLLOWUPS = 3
MAX_CONTEXT_FOLLOWUPS = 3
DEFAULT_CONTEXT_CHARS = 4000
MIN_CONTEXT_CHARS = 500
MAX_CONTEXT_CHARS = 12000
MAX_TASK_LENGTH = 300
MAX_TASK_TERMS = 8
CONTEXT_CANDIDATES_PER_TERM = 50
_TASK_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "as",
        "at",
        "by",
        "change",
        "for",
        "from",
        "in",
        "into",
        "of",
        "on",
        "or",
        "the",
        "to",
        "update",
        "with",
        "bir",
        "bu",
        "da",
        "de",
        "için",
        "ile",
        "ve",
        "veya",
    }
)


class QueryError(Exception):
    """A safe, user-facing local graph query failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SearchMatch:
    kind: str
    logical_key: str
    qualified_name: str
    in_scope: bool
    data_type: str | None
    nullable: bool | None
    primary_key: bool | None
    unique_value: bool | None


@dataclass(frozen=True)
class SearchResult:
    source_name: str
    snapshot_version: int
    matches: tuple[SearchMatch, ...]
    truncated: bool


@dataclass(frozen=True)
class OverviewTable:
    qualified_name: str
    declared_fk_count: int


@dataclass(frozen=True)
class DatabaseOverview:
    source_name: str
    snapshot_version: int
    schema_count: int
    external_schema_count: int
    table_count: int
    external_table_count: int
    column_count: int
    foreign_key_count: int
    schemas: tuple[str, ...]
    schemas_truncated: bool
    entry_tables: tuple[OverviewTable, ...]
    entry_tables_truncated: bool


@dataclass(frozen=True)
class RelevantObject:
    kind: str
    qualified_name: str
    in_scope: bool
    matched_terms: tuple[str, ...]
    reason: str
    data_type: str | None
    nullable: bool | None
    primary_key: bool | None
    unique_value: bool | None


@dataclass(frozen=True)
class SuggestedCall:
    tool: str
    arguments: dict[str, str]


@dataclass(frozen=True)
class RelevantContext:
    source_name: str
    snapshot_version: int
    selection_method: str
    task_terms: tuple[str, ...]
    unmatched_terms: tuple[str, ...]
    objects: tuple[RelevantObject, ...]
    suggested_followups: tuple[SuggestedCall, ...]
    truncated: bool
    terms_truncated: bool
    candidate_search_truncated: bool


def relevant_context_payload(context: RelevantContext) -> dict[str, object]:
    """Serialize only known object attributes for bounded MCP context."""

    payload = asdict(context)
    payload["objects"] = tuple(
        {key: value for key, value in item.items() if value is not None}
        for item in payload["objects"]
    )
    return payload


DEFAULT_SHOW_LIMIT = 30
MAX_SHOW_LIMIT = 100
DEFAULT_RELATIONSHIP_LIMIT = 20
MAX_RELATIONSHIP_LIMIT = 100
DEFAULT_IMPACT_LIMIT = 20
MAX_IMPACT_LIMIT = 100
MAX_COLUMN_IMPACT_FKS = 5_000
DEFAULT_INDEX_LIMIT = 20
MAX_INDEX_LIMIT = 100
MAX_INDEX_OFFSET = 100_000
MAX_INDEX_ATTRIBUTES = 128
DEFAULT_PATH_HOPS = 4
MAX_PATH_HOPS = 8
MAX_PATH_NODES = 500
MAX_PATH_EDGES = 2000


@dataclass(frozen=True)
class ColumnDetail:
    name: str
    data_type: str
    nullable: bool
    primary_key: bool
    unique_value: bool


@dataclass(frozen=True)
class KeyDetail:
    name: str
    kind: str
    columns: tuple[str, ...]
    inherited: bool


@dataclass(frozen=True)
class ForeignKeyDetail:
    name: str
    target_table: str
    column_pairs: tuple[tuple[str, str], ...]
    target_in_scope: bool
    validated: bool
    inherited: bool


@dataclass(frozen=True)
class TableContext:
    source_name: str
    snapshot_version: int
    logical_key: str
    qualified_name: str
    in_scope: bool
    partitioned: bool | None
    columns: tuple[ColumnDetail, ...]
    keys: tuple[KeyDetail, ...]
    foreign_keys: tuple[ForeignKeyDetail, ...]
    columns_truncated: bool
    keys_truncated: bool
    foreign_keys_truncated: bool


@dataclass(frozen=True)
class ViewColumnDetail:
    name: str
    data_type: str
    not_null_declared: bool


@dataclass(frozen=True)
class ViewContext:
    source_name: str
    snapshot_version: int
    kind: Literal["VIEW", "MATERIALIZED_VIEW"]
    qualified_name: str
    columns: tuple[ViewColumnDetail, ...]
    columns_truncated: bool


@dataclass(frozen=True)
class IndexPart:
    position: int
    column_name: str | None


@dataclass(frozen=True)
class IndexDetail:
    name: str
    qualified_name: str
    access_method: str
    key_columns: tuple[IndexPart, ...]
    included_columns: tuple[IndexPart, ...]
    unique: bool
    primary: bool
    valid: bool
    ready: bool
    partial: bool


@dataclass(frozen=True)
class TableIndexContext:
    source_name: str
    snapshot_version: int
    relation_kind: Literal["TABLE", "MATERIALIZED_VIEW"]
    qualified_name: str
    indexes: tuple[IndexDetail, ...]
    index_count: int
    offset: int
    truncated: bool


@dataclass(frozen=True)
class RelationshipDetail:
    name: str
    source_table: str
    target_table: str
    column_pairs: tuple[tuple[str, str], ...]
    target_in_scope: bool
    validated: bool
    inherited: bool
    origin: str = "DATABASE"
    status: str = "CONFIRMED"


@dataclass(frozen=True)
class RelationshipContext:
    source_name: str
    snapshot_version: int
    qualified_name: str
    in_scope: bool
    incoming: tuple[RelationshipDetail, ...]
    outgoing: tuple[RelationshipDetail, ...]
    incoming_truncated: bool
    outgoing_truncated: bool


@dataclass(frozen=True)
class ImpactContext:
    source_name: str
    snapshot_version: int
    qualified_name: str
    in_scope: bool
    dependents: tuple[RelationshipDetail, ...]
    foreign_key_count: int
    dependent_table_count: int
    truncated: bool
    scope: str = "DECLARED_FK_DIRECT"


@dataclass(frozen=True)
class ColumnImpactReference:
    foreign_key: str
    source_table: str
    source_column: str
    target_table: str
    target_column: str
    pair_position: int
    pair_count: int
    validated: bool
    inherited: bool
    origin: str = "DATABASE"
    status: str = "CONFIRMED"


@dataclass(frozen=True)
class ColumnImpactContext:
    source_name: str
    snapshot_version: int
    qualified_name: str
    references: tuple[ColumnImpactReference, ...]
    reference_count: int
    truncated: bool
    scope: str = "DECLARED_FK_COLUMN_DIRECT"


@dataclass(frozen=True)
class PathStep:
    foreign_key: str
    from_table: str
    to_table: str
    direction: str
    source_table: str
    target_table: str
    column_pairs: tuple[tuple[str, str], ...]
    target_in_scope: bool
    validated: bool
    inherited: bool
    origin: str = "DATABASE"
    status: str = "CONFIRMED"


@dataclass(frozen=True)
class PathResult:
    source_name: str
    snapshot_version: int
    start_table: str
    end_table: str
    steps: tuple[PathStep, ...]


@dataclass(frozen=True)
class TransitiveImpactItem:
    dependent_table: str
    hops: int
    steps: tuple[PathStep, ...]


@dataclass(frozen=True)
class TransitiveImpactContext:
    source_name: str
    snapshot_version: int
    qualified_name: str
    in_scope: bool
    max_hops: int
    dependents: tuple[TransitiveImpactItem, ...]
    dependent_table_count: int
    truncated: bool
    scope: str = "DECLARED_FK_TRANSITIVE_TABLE"


def _latest_snapshot(connection: sqlite3.Connection, source_name: str) -> tuple[int, int]:
    row = connection.execute(
        """SELECT snapshots.id, snapshots.version FROM snapshots
        JOIN sources ON sources.id = snapshots.source_id
        JOIN scan_runs ON scan_runs.id = snapshots.scan_run_id
        WHERE sources.name = ? AND scan_runs.status = 'COMPLETED'
        ORDER BY snapshots.version DESC LIMIT 1""",
        (source_name,),
    ).fetchone()
    if row is None:
        raise QueryError("NO_SNAPSHOT", f"Source '{source_name}' has no successful scan.")
    return int(row[0]), int(row[1])


def database_overview(
    root: Path, source_name: str, limit: int = DEFAULT_OVERVIEW_LIMIT
) -> DatabaseOverview:
    """Summarize one completed local snapshot without loading its full graph."""

    if not 1 <= limit <= MAX_OVERVIEW_LIMIT:
        raise QueryError("INVALID_LIMIT", f"Limit must be between 1 and {MAX_OVERVIEW_LIMIT}.")
    source = show_source(root, source_name)
    path = require_store_path(root)
    try:
        with closing(sqlite3.connect(path, timeout=5)) as connection:
            connection.execute("PRAGMA query_only = ON")
            snapshot_id, version = _latest_snapshot(connection, source.name)
            counts = connection.execute(
                """SELECT
                    COUNT(CASE WHEN object_type = 'SCHEMA'
                        AND COALESCE(json_extract(metadata_json, '$.in_scope'), 1) = 1
                        THEN 1 END),
                    COUNT(CASE WHEN object_type = 'SCHEMA'
                        AND json_extract(metadata_json, '$.in_scope') = 0 THEN 1 END),
                    COUNT(CASE WHEN object_type = 'TABLE'
                        AND COALESCE(json_extract(metadata_json, '$.in_scope'), 1) = 1
                        THEN 1 END),
                    COUNT(CASE WHEN object_type = 'TABLE'
                        AND json_extract(metadata_json, '$.in_scope') = 0 THEN 1 END),
                    COUNT(CASE WHEN object_type = 'COLUMN' THEN 1 END)
                FROM objects WHERE snapshot_id = ?""",
                (snapshot_id,),
            ).fetchone()
            foreign_key_count = connection.execute(
                """SELECT COUNT(*) FROM edges AS e
                JOIN objects AS source ON source.id = e.source_object_id
                  AND source.snapshot_id = e.snapshot_id AND source.object_type = 'TABLE'
                JOIN objects AS target ON target.id = e.target_object_id
                  AND target.snapshot_id = e.snapshot_id AND target.object_type = 'TABLE'
                WHERE e.snapshot_id = ? AND e.edge_type = 'REFERENCES'
                  AND e.origin = 'DATABASE' AND e.status = 'CONFIRMED'""",
                (snapshot_id,),
            ).fetchone()
            schema_rows = connection.execute(
                """SELECT qualified_name FROM objects
                WHERE snapshot_id = ? AND object_type = 'SCHEMA'
                  AND COALESCE(json_extract(metadata_json, '$.in_scope'), 1) = 1
                ORDER BY qualified_name LIMIT ?""",
                (snapshot_id, limit + 1),
            ).fetchall()
            table_rows = connection.execute(
                """SELECT o.qualified_name, COUNT(e.id) AS declared_fk_count
                FROM objects AS o LEFT JOIN edges AS e
                  ON e.snapshot_id = o.snapshot_id
                  AND e.edge_type = 'REFERENCES' AND e.origin = 'DATABASE'
                  AND e.status = 'CONFIRMED'
                  AND (e.source_object_id = o.id OR e.target_object_id = o.id)
                WHERE o.snapshot_id = ? AND o.object_type = 'TABLE'
                  AND COALESCE(json_extract(o.metadata_json, '$.in_scope'), 1) = 1
                GROUP BY o.id
                ORDER BY declared_fk_count DESC, o.qualified_name LIMIT ?""",
                (snapshot_id, limit + 1),
            ).fetchall()
    except sqlite3.Error:
        raise QueryError("STORE_READ_FAILED", "Could not read the local graph store.") from None
    if counts is None or foreign_key_count is None:
        raise QueryError("STORE_READ_FAILED", "Could not read the local graph store.")
    return DatabaseOverview(
        source_name=source.name,
        snapshot_version=version,
        schema_count=int(counts[0]),
        external_schema_count=int(counts[1]),
        table_count=int(counts[2]),
        external_table_count=int(counts[3]),
        column_count=int(counts[4]),
        foreign_key_count=int(foreign_key_count[0]),
        schemas=tuple(str(row[0]) for row in schema_rows[:limit]),
        schemas_truncated=len(schema_rows) > limit,
        entry_tables=tuple(OverviewTable(str(row[0]), int(row[1])) for row in table_rows[:limit]),
        entry_tables_truncated=len(table_rows) > limit,
    )


def _metadata(value: str) -> dict[str, object]:
    try:
        decoded = json.loads(value)
    except (ValueError, TypeError):
        raise QueryError("STORE_READ_FAILED", "Local graph data is invalid.") from None
    if not isinstance(decoded, dict):
        raise QueryError("STORE_READ_FAILED", "Local graph data is invalid.")
    return decoded


def _identifier(part: str) -> str:
    if part.startswith('"'):
        if len(part) < 3 or not part.endswith('"'):
            raise QueryError("INVALID_TABLE", "Table name has invalid quoted identifiers.")
        inner = part[1:-1]
        index = 0
        while index < len(inner):
            if inner[index] == '"':
                if index + 1 >= len(inner) or inner[index + 1] != '"':
                    raise QueryError("INVALID_TABLE", "Table name has invalid quoted identifiers.")
                index += 2
            else:
                index += 1
        return inner.replace('""', '"')
    if not part or not (part[0].isalpha() or part[0] == "_"):
        raise QueryError("INVALID_TABLE", "Use a table name or schema.table.")
    if not all(char.isalnum() or char in "_$" for char in part[1:]):
        raise QueryError("INVALID_TABLE", "Use a table name or schema.table.")
    return part.lower()


def _table_reference(value: str) -> tuple[str | None, str]:
    if not value or len(value) > 200 or any(ord(char) < 32 for char in value):
        raise QueryError("INVALID_TABLE", "Table reference is empty, too long, or invalid.")
    parts: list[str] = []
    current: list[str] = []
    quoted = False
    index = 0
    while index < len(value):
        char = value[index]
        if char == '"':
            current.append(char)
            if quoted and index + 1 < len(value) and value[index + 1] == '"':
                current.append('"')
                index += 2
                continue
            quoted = not quoted
        elif char == "." and not quoted:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
        index += 1
    parts.append("".join(current))
    if quoted or len(parts) not in (1, 2):
        raise QueryError("INVALID_TABLE", "Use a table name or schema.table.")
    parsed = tuple(_identifier(part) for part in parts)
    if any(not part for part in parsed):
        raise QueryError("INVALID_TABLE", "Table name cannot be empty.")
    return (None, parsed[0]) if len(parsed) == 1 else (parsed[0], parsed[1])


def _column_reference(value: str) -> tuple[tuple[str | None, str], str, str]:
    if not value or len(value) > 200 or any(ord(char) < 32 for char in value):
        raise QueryError("INVALID_COLUMN", "Column reference is empty, too long, or invalid.")
    quoted = False
    last_dot = -1
    index = 0
    while index < len(value):
        char = value[index]
        if char == '"':
            if quoted and index + 1 < len(value) and value[index + 1] == '"':
                index += 2
                continue
            quoted = not quoted
        elif char == "." and not quoted:
            last_dot = index
        index += 1
    if quoted or last_dot < 1 or last_dot == len(value) - 1:
        raise QueryError("INVALID_COLUMN", "Use table.column or schema.table.column.")
    try:
        table = _table_reference(value[:last_dot])
        column = _identifier(value[last_dot + 1 :])
    except QueryError:
        raise QueryError("INVALID_COLUMN", "Use table.column or schema.table.column.") from None
    return table, column, value[:last_dot]


def _validate_query(query: str, limit: int) -> tuple[str, ...]:
    if not 1 <= limit <= MAX_SEARCH_LIMIT:
        raise QueryError("INVALID_LIMIT", f"Limit must be between 1 and {MAX_SEARCH_LIMIT}.")
    if len(query) > MAX_QUERY_LENGTH or any(
        ord(char) < 32 and not char.isspace() for char in query
    ):
        raise QueryError(
            "INVALID_QUERY", "Search query is too long or contains control characters."
        )
    terms = tuple(part.casefold() for part in query.split())
    if not terms or len(terms) > MAX_SEARCH_TERMS:
        raise QueryError("INVALID_QUERY", f"Enter 1 to {MAX_SEARCH_TERMS} search terms.")
    return terms


def search_objects(
    root: Path, source_name: str, query: str, limit: int = DEFAULT_SEARCH_LIMIT
) -> SearchResult:
    """Search only the latest complete snapshot; never connect to the source DB."""

    terms = _validate_query(query, limit)
    source = show_source(root, source_name)
    path = require_store_path(root)
    folded_query = " ".join(terms)
    predicates = " AND ".join("instr(CASEFOLD(o.qualified_name), ?) > 0" for _ in terms)
    sql = f"""SELECT o.object_type, o.logical_key, o.qualified_name,
               o.metadata_json, c.data_type, c.nullable, c.primary_key, c.unique_value
        FROM objects AS o
        LEFT JOIN columns AS c ON c.object_id = o.id
        WHERE o.snapshot_id = ?
          AND o.object_type IN ('SCHEMA', 'TABLE', 'VIEW', 'MATERIALIZED_VIEW', 'COLUMN')
          AND {predicates}
        ORDER BY CASE
            WHEN CASEFOLD(o.object_name) = ? THEN 0
            WHEN instr(CASEFOLD(o.object_name), ?) = 1 THEN 1
            WHEN instr(CASEFOLD(o.object_name), ?) > 0 THEN 2
            ELSE 3 END,
          CASE o.object_type
            WHEN 'TABLE' THEN 0 WHEN 'VIEW' THEN 1
            WHEN 'MATERIALIZED_VIEW' THEN 2 WHEN 'COLUMN' THEN 3 ELSE 4 END,
          o.qualified_name, o.id
        LIMIT ?"""
    try:
        with closing(sqlite3.connect(path, timeout=5)) as connection:
            connection.execute("PRAGMA query_only = ON")
            connection.create_function("CASEFOLD", 1, str.casefold, deterministic=True)
            snapshot_id, version = _latest_snapshot(connection, source.name)
            rows = connection.execute(
                sql,
                (snapshot_id, *terms, folded_query, terms[0], terms[0], limit + 1),
            ).fetchall()
    except sqlite3.Error:
        raise QueryError("STORE_READ_FAILED", "Could not read the local graph store.") from None

    try:
        matches = tuple(
            SearchMatch(
                kind=str(row[0]),
                logical_key=str(row[1]),
                qualified_name=str(row[2]),
                in_scope=_metadata(row[3]).get("in_scope", True) is not False,
                data_type=str(row[4]) if row[4] is not None else None,
                nullable=bool(row[5]) if row[5] is not None else None,
                primary_key=bool(row[6]) if row[6] is not None else None,
                unique_value=bool(row[7]) if row[7] is not None else None,
            )
            for row in rows[:limit]
        )
    except (ValueError, TypeError, AttributeError):
        raise QueryError("STORE_READ_FAILED", "Local graph data is invalid.") from None
    return SearchResult(source.name, version, matches, len(rows) > limit)


def _context_terms(task: str) -> tuple[tuple[str, ...], bool]:
    if (
        not task.strip()
        or len(task) > MAX_TASK_LENGTH
        or any(ord(char) < 32 and not char.isspace() for char in task)
    ):
        raise QueryError("INVALID_TASK", "Task is empty, too long, or contains control characters.")
    words = dict.fromkeys(
        word
        for word in re.findall(r"[^\W_]+", task.casefold())
        if len(word) > 1 and word not in _TASK_STOPWORDS
    )
    if not words or any(len(word) > MAX_QUERY_LENGTH for word in words):
        raise QueryError("INVALID_TASK", "Task has no usable search terms.")
    terms = tuple(words)
    return terms[:MAX_TASK_TERMS], len(terms) > MAX_TASK_TERMS


def _exact_object_name_hit(match: SearchMatch, hits: set[str]) -> bool:
    qualified = match.qualified_name.casefold()
    return any(
        qualified == quote_identifier(term).casefold()
        if match.kind == "SCHEMA"
        else qualified.endswith("." + quote_identifier(term).casefold())
        for term in hits
    )


def get_relevant_context(
    root: Path,
    source_name: str,
    task: str,
    max_objects: int = DEFAULT_CONTEXT_OBJECTS,
    max_chars: int = DEFAULT_CONTEXT_CHARS,
    max_followups: int = DEFAULT_CONTEXT_FOLLOWUPS,
) -> RelevantContext:
    """Select a few explainable name matches from one stable local snapshot."""

    if not 1 <= max_objects <= MAX_CONTEXT_OBJECTS:
        raise QueryError(
            "INVALID_BUDGET", f"max_objects must be between 1 and {MAX_CONTEXT_OBJECTS}."
        )
    if not MIN_CONTEXT_CHARS <= max_chars <= MAX_CONTEXT_CHARS:
        raise QueryError(
            "INVALID_BUDGET",
            f"max_chars must be between {MIN_CONTEXT_CHARS} and {MAX_CONTEXT_CHARS}.",
        )
    if not 1 <= max_followups <= MAX_CONTEXT_FOLLOWUPS:
        raise QueryError(
            "INVALID_BUDGET",
            f"max_followups must be between 1 and {MAX_CONTEXT_FOLLOWUPS}.",
        )
    terms, terms_truncated = _context_terms(task)
    matches: dict[str, tuple[SearchMatch, set[str]]] = {}
    unmatched: list[str] = []
    snapshot_version: int | None = None
    candidate_search_truncated = False
    for term in terms:
        result = search_objects(root, source_name, term, CONTEXT_CANDIDATES_PER_TERM)
        if snapshot_version is None:
            snapshot_version = result.snapshot_version
        elif result.snapshot_version != snapshot_version:
            raise QueryError("SNAPSHOT_CHANGED", "A new scan completed; retry the context request.")
        candidate_search_truncated |= result.truncated
        if not result.matches:
            unmatched.append(term)
        for match in result.matches:
            if match.logical_key not in matches:
                matches[match.logical_key] = (match, set())
            matches[match.logical_key][1].add(term)

    assert snapshot_version is not None
    kind_rank = {"TABLE": 0, "VIEW": 1, "MATERIALIZED_VIEW": 2, "COLUMN": 3, "SCHEMA": 4}
    ranked = sorted(
        matches.values(),
        key=lambda item: (
            -len(item[1]),
            not item[0].in_scope,
            kind_rank.get(item[0].kind, 3),
            not _exact_object_name_hit(item[0], item[1]),
            item[0].qualified_name,
        ),
    )
    selected = [
        RelevantObject(
            kind=match.kind,
            qualified_name=match.qualified_name,
            in_scope=match.in_scope,
            matched_terms=tuple(term for term in terms if term in hits),
            reason="MATCHED_TASK_TERMS",
            data_type=match.data_type,
            nullable=match.nullable,
            primary_key=match.primary_key,
            unique_value=match.unique_value,
        )
        for match, hits in ranked[:max_objects]
    ]
    truncated = terms_truncated or candidate_search_truncated or len(ranked) > max_objects
    while True:
        relations = [
            item for item in selected if item.kind in ("TABLE", "VIEW", "MATERIALIZED_VIEW")
        ][:max_followups]
        followups = tuple(
            SuggestedCall(
                "get_table" if item.kind == "TABLE" else "get_view",
                {"source": source_name, "name": item.qualified_name},
            )
            for item in relations
        )
        if not followups and not selected:
            followups = (SuggestedCall("database_overview", {"source": source_name}),)
        context = RelevantContext(
            source_name=source_name,
            snapshot_version=snapshot_version,
            selection_method="lexical_name_match",
            task_terms=terms,
            unmatched_terms=tuple(unmatched),
            objects=tuple(selected),
            suggested_followups=followups,
            truncated=truncated,
            terms_truncated=terms_truncated,
            candidate_search_truncated=candidate_search_truncated,
        )
        compact = json.dumps(
            relevant_context_payload(context), ensure_ascii=False, separators=(",", ":")
        )
        if len(compact) <= max_chars:
            return context
        if not selected:
            raise QueryError("CONTEXT_TOO_LARGE", "Increase max_chars or shorten the task.")
        selected.pop()
        truncated = True


def _names(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or not all(isinstance(item, str) for item in value):
        raise QueryError("STORE_READ_FAILED", "Local graph key data is invalid.")
    return tuple(value)


def _resolve_table(
    connection: sqlite3.Connection,
    snapshot_id: int,
    reference: str,
    parsed: tuple[str | None, str],
) -> tuple[int, str, str, str]:
    schema_name, table_name = parsed
    sql = """SELECT id, logical_key, qualified_name, metadata_json FROM objects
        WHERE snapshot_id = ? AND object_type = 'TABLE' AND object_name = ?"""
    parameters: list[object] = [snapshot_id, table_name]
    if schema_name is not None:
        sql += " AND schema_name = ?"
        parameters.append(schema_name)
    rows = connection.execute(sql + " ORDER BY qualified_name LIMIT 2", parameters).fetchall()
    if not rows:
        raise QueryError(
            "TABLE_NOT_FOUND", f"Table '{reference}' was not found in the latest scan."
        )
    if len(rows) > 1:
        raise QueryError(
            "AMBIGUOUS_TABLE", "Table exists in multiple schemas; include schema name."
        )
    return int(rows[0][0]), str(rows[0][1]), str(rows[0][2]), str(rows[0][3])


def _table_scope(raw_metadata: str) -> tuple[bool, bool | None]:
    metadata = _metadata(raw_metadata)
    in_scope = metadata.get("in_scope", True)
    partitioned = metadata.get("partitioned")
    if type(in_scope) is not bool or (partitioned is not None and type(partitioned) is not bool):
        raise QueryError("STORE_READ_FAILED", "Local graph table data is invalid.")
    return in_scope, partitioned


def _columns_for_table(
    connection: sqlite3.Connection, snapshot_id: int, table_id: int, limit: int
) -> tuple[tuple[ColumnDetail, ...], bool]:
    rows = connection.execute(
        """SELECT o.object_name, c.data_type, c.nullable, c.primary_key, c.unique_value
        FROM columns AS c JOIN objects AS o ON o.id = c.object_id
        WHERE o.snapshot_id = ? AND c.table_object_id = ? AND o.object_type = 'COLUMN'
        ORDER BY c.ordinal_position, o.object_name LIMIT ?""",
        (snapshot_id, table_id, limit + 1),
    ).fetchall()
    return (
        tuple(
            ColumnDetail(str(row[0]), str(row[1]), bool(row[2]), bool(row[3]), bool(row[4]))
            for row in rows[:limit]
        ),
        len(rows) > limit,
    )


def _keys_for_table(
    connection: sqlite3.Connection, snapshot_id: int, table_id: int, limit: int
) -> tuple[tuple[KeyDetail, ...], bool]:
    rows = connection.execute(
        """SELECT o.object_name, o.metadata_json FROM objects AS o
        WHERE o.snapshot_id = ? AND o.parent_id = ? AND o.object_type = 'CONSTRAINT'
          AND EXISTS (SELECT 1 FROM edges AS e
              WHERE e.snapshot_id = ? AND e.source_object_id = o.id
                AND e.edge_type IN ('PRIMARY_KEY', 'UNIQUE_KEY', 'UNIQUE'))
        ORDER BY o.object_name LIMIT ?""",
        (snapshot_id, table_id, snapshot_id, limit + 1),
    ).fetchall()
    keys: list[KeyDetail] = []
    for name, raw_metadata in rows[:limit]:
        metadata = _metadata(raw_metadata)
        kind = metadata.get("kind")
        inherited = metadata.get("inherited")
        if kind not in ("PRIMARY_KEY", "UNIQUE") or type(inherited) is not bool:
            raise QueryError("STORE_READ_FAILED", "Local graph key data is invalid.")
        keys.append(KeyDetail(str(name), kind, _names(metadata.get("columns")), inherited))
    return tuple(keys), len(rows) > limit


def _foreign_key_detail(target: str, raw_metadata: str) -> ForeignKeyDetail:
    metadata = _metadata(raw_metadata)
    name = metadata.get("constraint")
    pairs = metadata.get("column_pairs")
    target_in_scope = metadata.get("target_in_scope")
    validated = metadata.get("validated")
    inherited = metadata.get("inherited")
    if (
        not isinstance(name, str)
        or metadata.get("level") != "TABLE"
        or not isinstance(pairs, list)
        or not pairs
        or any(
            not isinstance(pair, list)
            or len(pair) != 2
            or not all(isinstance(item, str) for item in pair)
            for pair in pairs
        )
        or not isinstance(target_in_scope, bool)
        or not isinstance(validated, bool)
        or not isinstance(inherited, bool)
    ):
        raise QueryError("STORE_READ_FAILED", "Local graph relationship data is invalid.")
    return ForeignKeyDetail(
        name=name,
        target_table=target,
        column_pairs=tuple((pair[0], pair[1]) for pair in pairs),
        target_in_scope=target_in_scope,
        validated=validated,
        inherited=inherited,
    )


def _foreign_keys_for_table(
    connection: sqlite3.Connection, snapshot_id: int, table_id: int, limit: int
) -> tuple[tuple[ForeignKeyDetail, ...], bool]:
    rows = connection.execute(
        """SELECT target.qualified_name, e.metadata_json
        FROM edges AS e JOIN objects AS target
          ON target.id = e.target_object_id AND target.snapshot_id = e.snapshot_id
        WHERE e.snapshot_id = ? AND e.source_object_id = ?
          AND e.edge_type = 'REFERENCES' AND e.status = 'CONFIRMED'
          AND e.origin = 'DATABASE' AND target.object_type = 'TABLE'
        ORDER BY e.id LIMIT ?""",
        (snapshot_id, table_id, limit + 1),
    ).fetchall()
    foreign_keys = tuple(
        _foreign_key_detail(str(target), str(raw_metadata)) for target, raw_metadata in rows[:limit]
    )
    return foreign_keys, len(rows) > limit


def show_table(
    root: Path,
    source_name: str,
    reference: str,
    limit: int = DEFAULT_SHOW_LIMIT,
) -> TableContext:
    """Return one exact table and bounded structural facts from the latest snapshot."""

    parsed = _table_reference(reference)
    if not 1 <= limit <= MAX_SHOW_LIMIT:
        raise QueryError("INVALID_LIMIT", f"Limit must be between 1 and {MAX_SHOW_LIMIT}.")
    source = show_source(root, source_name)
    path = require_store_path(root)
    try:
        with closing(sqlite3.connect(path, timeout=5)) as connection:
            connection.execute("PRAGMA query_only = ON")
            snapshot_id, version = _latest_snapshot(connection, source.name)
            table_id, logical_key, qualified_name, raw_metadata = _resolve_table(
                connection, snapshot_id, reference, parsed
            )
            columns, columns_truncated = _columns_for_table(
                connection, snapshot_id, table_id, limit
            )
            keys, keys_truncated = _keys_for_table(connection, snapshot_id, table_id, limit)
            foreign_keys, foreign_keys_truncated = _foreign_keys_for_table(
                connection, snapshot_id, table_id, limit
            )
    except sqlite3.Error:
        raise QueryError("STORE_READ_FAILED", "Could not read the local graph store.") from None
    in_scope, partitioned = _table_scope(raw_metadata)
    return TableContext(
        source_name=source.name,
        snapshot_version=version,
        logical_key=logical_key,
        qualified_name=qualified_name,
        in_scope=in_scope,
        partitioned=partitioned,
        columns=columns,
        keys=keys,
        foreign_keys=foreign_keys,
        columns_truncated=columns_truncated,
        keys_truncated=keys_truncated,
        foreign_keys_truncated=foreign_keys_truncated,
    )


def show_view(
    root: Path,
    source_name: str,
    reference: str,
    limit: int = DEFAULT_SHOW_LIMIT,
) -> ViewContext:
    """Return one saved view's catalog columns, without inferred lineage."""

    try:
        schema_name, view_name = _table_reference(reference)
    except QueryError as error:
        if error.code != "INVALID_TABLE":
            raise
        raise QueryError("INVALID_VIEW", "Use a view name or schema.view.") from None
    if not 1 <= limit <= MAX_SHOW_LIMIT:
        raise QueryError("INVALID_LIMIT", f"Limit must be between 1 and {MAX_SHOW_LIMIT}.")
    source = show_source(root, source_name)
    path = require_store_path(root)
    try:
        with closing(sqlite3.connect(path, timeout=5)) as connection:
            connection.execute("PRAGMA query_only = ON")
            snapshot_id, version = _latest_snapshot(connection, source.name)
            sql = """SELECT id, object_type, qualified_name, metadata_json FROM objects
                WHERE snapshot_id = ? AND object_type IN ('VIEW', 'MATERIALIZED_VIEW')
                  AND object_name = ?"""
            parameters: list[object] = [snapshot_id, view_name]
            if schema_name is not None:
                sql += " AND schema_name = ?"
                parameters.append(schema_name)
            rows = connection.execute(
                sql + " ORDER BY qualified_name LIMIT 2", parameters
            ).fetchall()
            if not rows:
                raise QueryError(
                    "VIEW_NOT_FOUND", f"View '{reference}' was not found in the latest scan."
                )
            if len(rows) > 1:
                raise QueryError(
                    "AMBIGUOUS_VIEW", "View exists in multiple schemas; include schema name."
                )
            view_id, raw_kind, qualified_name, raw_metadata = rows[0]
            metadata = _metadata(str(raw_metadata))
            if metadata.get("in_scope") is not True or raw_kind not in (
                "VIEW",
                "MATERIALIZED_VIEW",
            ):
                raise QueryError("STORE_READ_FAILED", "Local graph view data is invalid.")
            columns, columns_truncated = _columns_for_table(
                connection, snapshot_id, int(view_id), limit
            )
    except sqlite3.Error:
        raise QueryError("STORE_READ_FAILED", "Could not read the local graph store.") from None
    kind: Literal["VIEW", "MATERIALIZED_VIEW"] = (
        "VIEW" if raw_kind == "VIEW" else "MATERIALIZED_VIEW"
    )
    return ViewContext(
        source_name=source.name,
        snapshot_version=version,
        kind=kind,
        qualified_name=str(qualified_name),
        columns=tuple(
            ViewColumnDetail(column.name, column.data_type, not column.nullable)
            for column in columns
        ),
        columns_truncated=columns_truncated,
    )


def _index_detail(
    connection: sqlite3.Connection,
    snapshot_id: int,
    relation_id: int,
    relation_name: str,
    row: tuple[object, ...],
) -> IndexDetail:
    index_id, name, qualified_name, raw_metadata = row
    if not isinstance(name, str) or not name or not isinstance(qualified_name, str):
        raise QueryError("STORE_READ_FAILED", "Saved index identity is invalid.")
    if qualified_name != f"{relation_name}.{quote_identifier(name)}":
        raise QueryError("STORE_READ_FAILED", "Saved index identity is invalid.")
    metadata = _metadata(str(raw_metadata))
    keys = metadata.get("key_columns")
    included = metadata.get("included_columns")
    method = metadata.get("access_method")
    flags = tuple(metadata.get(flag) for flag in ("unique", "primary", "valid", "ready", "partial"))
    if (
        not isinstance(keys, list)
        or not keys
        or not isinstance(included, list)
        or len(keys) + len(included) > MAX_INDEX_ATTRIBUTES
        or any(value is not None and (not isinstance(value, str) or not value) for value in keys)
        or any(not isinstance(value, str) or not value for value in included)
        or not isinstance(method, str)
        or not method
        or any(type(value) is not bool for value in flags)
        or flags[1] is True
        and flags[0] is not True
    ):
        raise QueryError("STORE_READ_FAILED", "Saved index metadata is invalid.")
    parent_edges = connection.execute(
        """SELECT origin, status, confidence FROM edges
        WHERE snapshot_id = ? AND source_object_id = ? AND target_object_id = ?
          AND edge_type = 'CONTAINS' LIMIT 2""",
        (snapshot_id, relation_id, index_id),
    ).fetchall()
    if len(parent_edges) != 1 or parent_edges[0] != ("DATABASE", "CONFIRMED", 1.0):
        raise QueryError("STORE_READ_FAILED", "Saved index parent edge is invalid.")
    edge_rows = connection.execute(
        """SELECT e.edge_type, e.origin, e.status, e.confidence, e.metadata_json,
            target.object_name, target.object_type, target.parent_id, target.snapshot_id
        FROM edges AS e LEFT JOIN objects AS target ON target.id = e.target_object_id
        WHERE e.snapshot_id = ? AND e.source_object_id = ?
        ORDER BY e.id LIMIT ?""",
        (snapshot_id, index_id, MAX_INDEX_ATTRIBUTES + 1),
    ).fetchall()
    if len(edge_rows) > MAX_INDEX_ATTRIBUTES:
        raise QueryError("STORE_READ_FAILED", "Saved index has too many column edges.")
    actual: list[tuple[str, int, str]] = []
    for edge in edge_rows:
        edge_type, origin, status, confidence, raw_edge, target_name, target_kind, parent, sid = (
            edge
        )
        edge_metadata = _metadata(str(raw_edge))
        position = edge_metadata.get("position")
        if (
            edge_type not in ("INDEX_KEY", "INDEX_INCLUDE")
            or (origin, status, confidence) != ("DATABASE", "CONFIRMED", 1.0)
            or type(position) is not int
            or not isinstance(target_name, str)
            or target_kind != "COLUMN"
            or parent != relation_id
            or sid != snapshot_id
        ):
            raise QueryError("STORE_READ_FAILED", "Saved index column edge is invalid.")
        actual.append((str(edge_type), position, target_name))
    expected = [
        ("INDEX_KEY", position, name)
        for position, name in enumerate(keys, start=1)
        if name is not None
    ] + [("INDEX_INCLUDE", position, name) for position, name in enumerate(included, start=1)]
    if sorted(actual) != sorted(expected):
        raise QueryError("STORE_READ_FAILED", "Saved index columns do not match their edges.")
    return IndexDetail(
        name=name,
        qualified_name=qualified_name,
        access_method=method,
        key_columns=tuple(IndexPart(position, value) for position, value in enumerate(keys, 1)),
        included_columns=tuple(
            IndexPart(position, value) for position, value in enumerate(included, 1)
        ),
        unique=flags[0] is True,
        primary=flags[1] is True,
        valid=flags[2] is True,
        ready=flags[3] is True,
        partial=flags[4] is True,
    )


def list_table_indexes(
    root: Path,
    source_name: str,
    reference: str,
    limit: int = DEFAULT_INDEX_LIMIT,
    offset: int = 0,
) -> TableIndexContext:
    """List a bounded page of saved indexes for one exact relation."""

    try:
        schema_name, relation_name = _table_reference(reference)
    except QueryError as error:
        if error.code != "INVALID_TABLE":
            raise
        raise QueryError("INVALID_INDEX_TARGET", "Use a table or materialized-view name.") from None
    if not 1 <= limit <= MAX_INDEX_LIMIT:
        raise QueryError("INVALID_LIMIT", f"Limit must be between 1 and {MAX_INDEX_LIMIT}.")
    if not 0 <= offset <= MAX_INDEX_OFFSET:
        raise QueryError("INVALID_OFFSET", f"Offset must be between 0 and {MAX_INDEX_OFFSET}.")
    source = show_source(root, source_name)
    path = require_store_path(root)
    try:
        with closing(sqlite3.connect(path, timeout=5)) as connection:
            connection.execute("PRAGMA query_only = ON")
            snapshot_id, version = _latest_snapshot(connection, source.name)
            sql = """SELECT id, object_type, qualified_name, metadata_json FROM objects
                WHERE snapshot_id = ? AND object_type IN ('TABLE', 'MATERIALIZED_VIEW')
                  AND object_name = ?"""
            parameters: list[object] = [snapshot_id, relation_name]
            if schema_name is not None:
                sql += " AND schema_name = ?"
                parameters.append(schema_name)
            relations = connection.execute(
                sql + " ORDER BY qualified_name LIMIT 2", parameters
            ).fetchall()
            if not relations:
                raise QueryError(
                    "INDEX_TARGET_NOT_FOUND",
                    f"Table or materialized view '{reference}' was not found in the latest scan.",
                )
            if len(relations) > 1:
                raise QueryError(
                    "AMBIGUOUS_INDEX_TARGET", "Relation exists in multiple schemas; qualify it."
                )
            relation_id, raw_kind, qualified_name, raw_metadata = relations[0]
            relation_metadata = _metadata(str(raw_metadata))
            if relation_metadata.get("in_scope") is not True:
                raise QueryError("INDEX_TARGET_NOT_FOUND", "Relation is outside scan scope.")
            count_row = connection.execute(
                """SELECT COUNT(*) FROM objects WHERE snapshot_id = ? AND parent_id = ?
                AND object_type = 'INDEX'""",
                (snapshot_id, relation_id),
            ).fetchone()
            if count_row is None or int(count_row[0]) > MAX_INDEX_OFFSET:
                raise QueryError("INDEX_BUDGET_EXCEEDED", "Saved index count exceeds the limit.")
            index_count = int(count_row[0])
            rows = connection.execute(
                """SELECT id, object_name, qualified_name, metadata_json FROM objects
                WHERE snapshot_id = ? AND parent_id = ? AND object_type = 'INDEX'
                ORDER BY qualified_name, id LIMIT ? OFFSET ?""",
                (snapshot_id, relation_id, limit, offset),
            ).fetchall()
            indexes = tuple(
                _index_detail(connection, snapshot_id, int(relation_id), str(qualified_name), row)
                for row in rows
            )
    except sqlite3.Error:
        raise QueryError("STORE_READ_FAILED", "Could not read the local graph store.") from None
    kind: Literal["TABLE", "MATERIALIZED_VIEW"] = (
        "TABLE" if raw_kind == "TABLE" else "MATERIALIZED_VIEW"
    )
    return TableIndexContext(
        source_name=source.name,
        snapshot_version=version,
        relation_kind=kind,
        qualified_name=str(qualified_name),
        indexes=indexes,
        index_count=index_count,
        offset=offset,
        truncated=offset + len(indexes) < index_count,
    )


def _direct_relationships(
    connection: sqlite3.Connection,
    snapshot_id: int,
    table_id: int,
    limit: int,
    *,
    incoming: bool,
) -> tuple[tuple[RelationshipDetail, ...], bool]:
    endpoint = "e.target_object_id" if incoming else "e.source_object_id"
    rows = connection.execute(
        f"""SELECT source.qualified_name, target.qualified_name, e.metadata_json
        FROM edges AS e
        JOIN objects AS source ON source.id = e.source_object_id
          AND source.snapshot_id = e.snapshot_id AND source.object_type = 'TABLE'
        JOIN objects AS target ON target.id = e.target_object_id
          AND target.snapshot_id = e.snapshot_id AND target.object_type = 'TABLE'
        WHERE e.snapshot_id = ? AND {endpoint} = ?
          AND e.edge_type = 'REFERENCES' AND e.origin = 'DATABASE'
          AND e.status = 'CONFIRMED'
        ORDER BY e.id LIMIT ?""",
        (snapshot_id, table_id, limit + 1),
    ).fetchall()
    relationships: list[RelationshipDetail] = []
    for source, target, raw_metadata in rows[:limit]:
        detail = _foreign_key_detail(str(target), str(raw_metadata))
        relationships.append(
            RelationshipDetail(
                name=detail.name,
                source_table=str(source),
                target_table=detail.target_table,
                column_pairs=detail.column_pairs,
                target_in_scope=detail.target_in_scope,
                validated=detail.validated,
                inherited=detail.inherited,
            )
        )
    return tuple(relationships), len(rows) > limit


def table_relationships(
    root: Path,
    source_name: str,
    reference: str,
    limit: int = DEFAULT_RELATIONSHIP_LIMIT,
) -> RelationshipContext:
    """Return bounded incoming/outgoing declared FKs for one saved table."""

    parsed = _table_reference(reference)
    if not 1 <= limit <= MAX_RELATIONSHIP_LIMIT:
        raise QueryError("INVALID_LIMIT", f"Limit must be between 1 and {MAX_RELATIONSHIP_LIMIT}.")
    source = show_source(root, source_name)
    path = require_store_path(root)
    try:
        with closing(sqlite3.connect(path, timeout=5)) as connection:
            connection.execute("PRAGMA query_only = ON")
            snapshot_id, version = _latest_snapshot(connection, source.name)
            table_id, _, qualified_name, raw_metadata = _resolve_table(
                connection, snapshot_id, reference, parsed
            )
            incoming, incoming_truncated = _direct_relationships(
                connection, snapshot_id, table_id, limit, incoming=True
            )
            outgoing, outgoing_truncated = _direct_relationships(
                connection, snapshot_id, table_id, limit, incoming=False
            )
    except sqlite3.Error:
        raise QueryError("STORE_READ_FAILED", "Could not read the local graph store.") from None
    in_scope, _ = _table_scope(raw_metadata)
    return RelationshipContext(
        source_name=source.name,
        snapshot_version=version,
        qualified_name=qualified_name,
        in_scope=in_scope,
        incoming=incoming,
        outgoing=outgoing,
        incoming_truncated=incoming_truncated,
        outgoing_truncated=outgoing_truncated,
    )


def table_impact(
    root: Path,
    source_name: str,
    reference: str,
    limit: int = DEFAULT_IMPACT_LIMIT,
) -> ImpactContext:
    """Show direct declared-FK dependents, not application or inferred lineage."""

    parsed = _table_reference(reference)
    if not 1 <= limit <= MAX_IMPACT_LIMIT:
        raise QueryError("INVALID_LIMIT", f"Limit must be between 1 and {MAX_IMPACT_LIMIT}.")
    source = show_source(root, source_name)
    path = require_store_path(root)
    try:
        with closing(sqlite3.connect(path, timeout=5)) as connection:
            connection.execute("PRAGMA query_only = ON")
            connection.execute("BEGIN")
            snapshot_id, version = _latest_snapshot(connection, source.name)
            table_id, _, qualified_name, raw_metadata = _resolve_table(
                connection, snapshot_id, reference, parsed
            )
            counts = connection.execute(
                """SELECT COUNT(*), COUNT(DISTINCT e.source_object_id)
                FROM edges AS e
                JOIN objects AS source_table ON source_table.id = e.source_object_id
                  AND source_table.snapshot_id = e.snapshot_id
                  AND source_table.object_type = 'TABLE'
                JOIN objects AS target_table ON target_table.id = e.target_object_id
                  AND target_table.snapshot_id = e.snapshot_id
                  AND target_table.object_type = 'TABLE'
                WHERE e.snapshot_id = ? AND e.target_object_id = ?
                  AND e.edge_type = 'REFERENCES' AND e.origin = 'DATABASE'
                  AND e.status = 'CONFIRMED'""",
                (snapshot_id, table_id),
            ).fetchone()
            dependents, truncated = _direct_relationships(
                connection, snapshot_id, table_id, limit, incoming=True
            )
    except sqlite3.Error:
        raise QueryError("STORE_READ_FAILED", "Could not read the local graph store.") from None
    if counts is None:
        raise QueryError("STORE_READ_FAILED", "Could not read the local graph store.")
    in_scope, _ = _table_scope(raw_metadata)
    return ImpactContext(
        source_name=source.name,
        snapshot_version=version,
        qualified_name=qualified_name,
        in_scope=in_scope,
        dependents=dependents,
        foreign_key_count=int(counts[0]),
        dependent_table_count=int(counts[1]),
        truncated=truncated,
    )


def column_impact(
    root: Path,
    source_name: str,
    reference: str,
    limit: int = DEFAULT_IMPACT_LIMIT,
) -> ColumnImpactContext:
    """Return exact incoming declared-FK column pairs for one saved column."""

    parsed_table, column_name, table_reference = _column_reference(reference)
    if not 1 <= limit <= MAX_IMPACT_LIMIT:
        raise QueryError("INVALID_LIMIT", f"Limit must be between 1 and {MAX_IMPACT_LIMIT}.")
    source = show_source(root, source_name)
    path = require_store_path(root)
    try:
        with closing(sqlite3.connect(path, timeout=5)) as connection:
            connection.execute("PRAGMA query_only = ON")
            connection.execute("BEGIN")
            snapshot_id, version = _latest_snapshot(connection, source.name)
            table_id, _, table_name, _ = _resolve_table(
                connection, snapshot_id, table_reference, parsed_table
            )
            rows = connection.execute(
                """SELECT o.qualified_name FROM objects AS o
                JOIN columns AS c ON c.object_id = o.id
                WHERE o.snapshot_id = ? AND o.parent_id = ?
                  AND o.object_type = 'COLUMN' AND o.object_name = ?
                LIMIT 2""",
                (snapshot_id, table_id, column_name),
            ).fetchall()
            if not rows:
                raise QueryError(
                    "COLUMN_NOT_FOUND", f"Column '{reference}' was not found in the latest scan."
                )
            if len(rows) > 1:
                raise QueryError("STORE_READ_FAILED", "Local column data is not unique.")
            links, overflow = _direct_relationships(
                connection, snapshot_id, table_id, MAX_COLUMN_IMPACT_FKS, incoming=True
            )
    except sqlite3.Error:
        raise QueryError("STORE_READ_FAILED", "Could not read the local graph store.") from None
    if overflow:
        raise QueryError(
            "IMPACT_BUDGET_EXCEEDED",
            f"Table has more than {MAX_COLUMN_IMPACT_FKS} incoming FKs; impact is not partial.",
        )
    qualified_name = str(rows[0][0])
    if qualified_name != f"{table_name}.{quote_identifier(column_name)}":
        raise QueryError("STORE_READ_FAILED", "Local column identity is invalid.")
    references: list[ColumnImpactReference] = []
    for link in links:
        for position, (source_column, target_column) in enumerate(link.column_pairs, start=1):
            if target_column != column_name:
                continue
            references.append(
                ColumnImpactReference(
                    foreign_key=link.name,
                    source_table=link.source_table,
                    source_column=f"{link.source_table}.{quote_identifier(source_column)}",
                    target_table=link.target_table,
                    target_column=qualified_name,
                    pair_position=position,
                    pair_count=len(link.column_pairs),
                    validated=link.validated,
                    inherited=link.inherited,
                )
            )
    return ColumnImpactContext(
        source_name=source.name,
        snapshot_version=version,
        qualified_name=qualified_name,
        references=tuple(references[:limit]),
        reference_count=len(references),
        truncated=len(references) > limit,
    )


def _path_neighbors(
    connection: sqlite3.Connection, snapshot_id: int, table_id: int, limit: int
) -> list[tuple[int, int, str, str, str]]:
    rows = connection.execute(
        """SELECT e.source_object_id, e.target_object_id,
               source.qualified_name, target.qualified_name, e.metadata_json,
               CASE WHEN e.source_object_id = ? THEN target.qualified_name
                    ELSE source.qualified_name END AS neighbor_name
        FROM edges AS e
        JOIN objects AS source ON source.id = e.source_object_id
          AND source.snapshot_id = e.snapshot_id AND source.object_type = 'TABLE'
        JOIN objects AS target ON target.id = e.target_object_id
          AND target.snapshot_id = e.snapshot_id AND target.object_type = 'TABLE'
        WHERE e.snapshot_id = ? AND (e.source_object_id = ? OR e.target_object_id = ?)
          AND e.edge_type = 'REFERENCES' AND e.origin = 'DATABASE'
          AND e.status = 'CONFIRMED'
        ORDER BY neighbor_name, e.id LIMIT ?""",
        (table_id, snapshot_id, table_id, table_id, limit),
    ).fetchall()
    return [(int(a), int(b), str(c), str(d), str(e)) for a, b, c, d, e, _ in rows]


def _reconstruct_path(
    start_id: int, end_id: int, predecessors: dict[int, tuple[int, PathStep]]
) -> tuple[PathStep, ...]:
    steps: list[PathStep] = []
    cursor = end_id
    while cursor != start_id:
        previous, step = predecessors[cursor]
        steps.append(step)
        cursor = previous
    steps.reverse()
    return tuple(steps)


def _shortest_path(
    connection: sqlite3.Connection,
    snapshot_id: int,
    start_id: int,
    end_id: int,
    max_hops: int,
) -> tuple[PathStep, ...]:
    if start_id == end_id:
        return ()
    queue = deque([(start_id, 0)])
    visited = {start_id}
    predecessors: dict[int, tuple[int, PathStep]] = {}
    examined_edges = 0
    while queue:
        current_id, depth = queue.popleft()
        if depth >= max_hops:
            continue
        remaining = MAX_PATH_EDGES - examined_edges
        rows = _path_neighbors(connection, snapshot_id, current_id, remaining + 1)
        for source_id, target_id, source_name, target_name, raw_metadata in rows[:remaining]:
            examined_edges += 1
            forward = current_id == source_id
            neighbor_id = target_id if forward else source_id
            if neighbor_id in visited:
                continue
            if len(visited) >= MAX_PATH_NODES:
                raise QueryError("PATH_BUDGET_EXCEEDED", "Path search visited too many tables.")
            foreign_key = _foreign_key_detail(target_name, raw_metadata)
            step = PathStep(
                foreign_key=foreign_key.name,
                from_table=source_name if forward else target_name,
                to_table=target_name if forward else source_name,
                direction="FORWARD" if forward else "REVERSE",
                source_table=source_name,
                target_table=target_name,
                column_pairs=foreign_key.column_pairs,
                target_in_scope=foreign_key.target_in_scope,
                validated=foreign_key.validated,
                inherited=foreign_key.inherited,
            )
            visited.add(neighbor_id)
            predecessors[neighbor_id] = (current_id, step)
            if neighbor_id == end_id:
                return _reconstruct_path(start_id, end_id, predecessors)
            queue.append((neighbor_id, depth + 1))
        if len(rows) > remaining:
            raise QueryError("PATH_BUDGET_EXCEEDED", "Path search examined too many FKs.")
    raise QueryError("PATH_NOT_FOUND", f"No declared FK path within {max_hops} hops.")


def _transitive_impact(
    connection: sqlite3.Connection,
    snapshot_id: int,
    root_id: int,
    max_hops: int,
) -> tuple[dict[int, tuple[int, PathStep]], dict[int, tuple[int, str]]]:
    """Walk incoming declared FKs; stop instead of returning a partial graph."""

    queue = deque([(root_id, 0)])
    visited: dict[int, tuple[int, str]] = {}
    predecessors: dict[int, tuple[int, PathStep]] = {}
    examined_edges = 0
    while queue:
        current_id, depth = queue.popleft()
        if depth >= max_hops:
            continue
        remaining = MAX_PATH_EDGES - examined_edges
        rows = connection.execute(
            """SELECT source.id, source.qualified_name, target.qualified_name,
                e.metadata_json
            FROM edges AS e
            JOIN objects AS source ON source.id = e.source_object_id
              AND source.snapshot_id = e.snapshot_id AND source.object_type = 'TABLE'
            JOIN objects AS target ON target.id = e.target_object_id
              AND target.snapshot_id = e.snapshot_id AND target.object_type = 'TABLE'
            WHERE e.snapshot_id = ? AND e.target_object_id = ?
              AND e.edge_type = 'REFERENCES' AND e.origin = 'DATABASE'
              AND e.status = 'CONFIRMED'
            ORDER BY source.qualified_name, e.id LIMIT ?""",
            (snapshot_id, current_id, remaining + 1),
        ).fetchall()
        if len(rows) > remaining:
            raise QueryError("IMPACT_BUDGET_EXCEEDED", "Impact walk examined too many FKs.")
        for source_id, source_name, target_name, raw_metadata in rows:
            examined_edges += 1
            foreign_key = _foreign_key_detail(str(target_name), str(raw_metadata))
            dependent_id = int(source_id)
            if dependent_id == root_id or dependent_id in visited:
                continue
            if len(visited) + 1 >= MAX_PATH_NODES:
                raise QueryError("IMPACT_BUDGET_EXCEEDED", "Impact walk visited too many tables.")
            step = PathStep(
                foreign_key=foreign_key.name,
                from_table=str(target_name),
                to_table=str(source_name),
                direction="REVERSE",
                source_table=str(source_name),
                target_table=str(target_name),
                column_pairs=foreign_key.column_pairs,
                target_in_scope=foreign_key.target_in_scope,
                validated=foreign_key.validated,
                inherited=foreign_key.inherited,
            )
            visited[dependent_id] = (depth + 1, str(source_name))
            predecessors[dependent_id] = (current_id, step)
            queue.append((dependent_id, depth + 1))
    return predecessors, visited


def transitive_table_impact(
    root: Path,
    source_name: str,
    reference: str,
    max_hops: int = DEFAULT_PATH_HOPS,
    limit: int = DEFAULT_IMPACT_LIMIT,
) -> TransitiveImpactContext:
    """Return shortest incoming-FK paths, not proven application dependencies."""

    parsed = _table_reference(reference)
    if not 0 <= max_hops <= MAX_PATH_HOPS:
        raise QueryError("INVALID_HOPS", f"Maximum hops must be between 0 and {MAX_PATH_HOPS}.")
    if not 1 <= limit <= MAX_IMPACT_LIMIT:
        raise QueryError("INVALID_LIMIT", f"Limit must be between 1 and {MAX_IMPACT_LIMIT}.")
    source = show_source(root, source_name)
    path = require_store_path(root)
    try:
        with closing(sqlite3.connect(path, timeout=5)) as connection:
            connection.execute("PRAGMA query_only = ON")
            connection.execute("BEGIN")
            snapshot_id, version = _latest_snapshot(connection, source.name)
            root_table_id, _, qualified_name, raw_metadata = _resolve_table(
                connection, snapshot_id, reference, parsed
            )
            predecessors, visited = _transitive_impact(
                connection, snapshot_id, root_table_id, max_hops
            )
    except sqlite3.Error:
        raise QueryError("STORE_READ_FAILED", "Could not read the local graph store.") from None
    in_scope, _ = _table_scope(raw_metadata)
    ranked = sorted(
        visited, key=lambda dependent_id: (visited[dependent_id][0], visited[dependent_id][1])
    )
    dependents = tuple(
        TransitiveImpactItem(
            dependent_table=visited[dependent_id][1],
            hops=visited[dependent_id][0],
            steps=_reconstruct_path(root_table_id, dependent_id, predecessors),
        )
        for dependent_id in ranked[:limit]
    )
    return TransitiveImpactContext(
        source_name=source.name,
        snapshot_version=version,
        qualified_name=qualified_name,
        in_scope=in_scope,
        max_hops=max_hops,
        dependents=dependents,
        dependent_table_count=len(visited),
        truncated=len(visited) > limit,
    )


def find_table_path(
    root: Path,
    source_name: str,
    from_reference: str,
    to_reference: str,
    max_hops: int = DEFAULT_PATH_HOPS,
) -> PathResult:
    """Find one deterministic shortest path through declared local FK facts."""

    from_parsed = _table_reference(from_reference)
    to_parsed = _table_reference(to_reference)
    if not 0 <= max_hops <= MAX_PATH_HOPS:
        raise QueryError("INVALID_HOPS", f"Maximum hops must be between 0 and {MAX_PATH_HOPS}.")
    source = show_source(root, source_name)
    path = require_store_path(root)
    try:
        with closing(sqlite3.connect(path, timeout=5)) as connection:
            connection.execute("PRAGMA query_only = ON")
            snapshot_id, version = _latest_snapshot(connection, source.name)
            start_id, _, start_name, _ = _resolve_table(
                connection, snapshot_id, from_reference, from_parsed
            )
            end_id, _, end_name, _ = _resolve_table(
                connection, snapshot_id, to_reference, to_parsed
            )
            steps = _shortest_path(connection, snapshot_id, start_id, end_id, max_hops)
    except sqlite3.Error:
        raise QueryError("STORE_READ_FAILED", "Could not read the local graph store.") from None
    return PathResult(source.name, version, start_name, end_name, steps)
