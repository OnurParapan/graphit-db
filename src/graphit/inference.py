"""Conservative, read-only relationship candidate preview over one snapshot."""

import sqlite3
from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from graphit.queries import QueryError, _latest_snapshot, _metadata
from graphit.sources import require_store_path, show_source

DEFAULT_CANDIDATE_LIMIT = 20
MAX_CANDIDATE_LIMIT = 50
MAX_PREVIEW_TABLES = 2000
MAX_PREVIEW_COLUMNS = 20000
MAX_PREVIEW_PAIRS = 2000
MAX_PREVIEW_FK_EDGES = 20000
MAX_TARGETS_PER_COLUMN = 3
MAX_REVIEW_DECISIONS = 20000
MAX_APPROVED_SOURCE_COLUMNS = 200
MAX_APPROVED_RELEVANT_COLUMNS = 6000

_KEY_TYPES = {
    "smallint": "smallint",
    "int2": "smallint",
    "integer": "integer",
    "int4": "integer",
    "bigint": "bigint",
    "int8": "bigint",
    "uuid": "uuid",
}


@dataclass(frozen=True)
class CandidateEvidence:
    signal: str
    score: float
    weight: float
    detail: str


@dataclass(frozen=True)
class RelationshipCandidate:
    source_table: str
    source_column: str
    target_table: str
    target_column: str
    confidence: float
    alternatives_for_source: int
    evidence: tuple[CandidateEvidence, ...]
    origin: str = "INFERRED"
    status: str = "PENDING"


@dataclass(frozen=True)
class CandidatePreview:
    source_name: str
    snapshot_version: int
    scoring_method: str
    include_ambiguous: bool
    candidates: tuple[RelationshipCandidate, ...]
    truncated: bool
    skipped_ambiguous_columns: int


@dataclass(frozen=True)
class _Column:
    object_id: int
    table_id: int
    table_name: str
    schema_name: str
    table_qualified: str
    name: str
    qualified: str
    key_type: str | None
    unique_value: bool


def candidate_review_keys(
    source_name: str, source_column: str, target_column: str
) -> tuple[str, str]:
    """Keep review identity stable across snapshots and separate between sources."""

    prefix = f"{source_name}:postgres:column:"
    return prefix + source_column, prefix + target_column


def _review_decisions(
    connection: sqlite3.Connection, source_name: str
) -> dict[tuple[str, str], str]:
    prefix = f"{source_name}:postgres:column:"
    rows = connection.execute(
        """SELECT source_logical_key, target_logical_key, decision
        FROM review_decisions
        WHERE relationship_kind = 'LIKELY_REFERENCES'
          AND substr(source_logical_key, 1, ?) = ?
        ORDER BY id LIMIT ?""",
        (len(prefix), prefix, MAX_REVIEW_DECISIONS + 1),
    ).fetchall()
    if len(rows) > MAX_REVIEW_DECISIONS:
        raise QueryError("INFERENCE_BUDGET_EXCEEDED", "Too many review decisions for source.")
    latest = {(str(row[0]), str(row[1])): str(row[2]) for row in rows}
    if any(
        decision not in {"REJECTED", "RESTORED", "APPROVED", "REVOKED"}
        for decision in latest.values()
    ):
        raise QueryError("STORE_READ_FAILED", "A local review decision is invalid.")
    return latest


def current_approval_presence(
    root: Path, source_name: str, table_qualified: str
) -> tuple[int, bool]:
    """Check latest local review events adjacent to one table without inference work."""

    source = show_source(root, source_name)
    path = require_store_path(root)
    source_prefix = f"{source.name}:postgres:column:"
    table_prefix = f"{source_prefix}{table_qualified}."
    try:
        with closing(sqlite3.connect(path, timeout=5)) as connection:
            connection.execute("PRAGMA query_only = ON")
            connection.execute("BEGIN")
            _, version = _latest_snapshot(connection, source.name)
            rows = connection.execute(
                """SELECT source_logical_key, target_logical_key, decision
                FROM review_decisions
                WHERE relationship_kind = 'LIKELY_REFERENCES'
                  AND substr(source_logical_key, 1, ?) = ?
                  AND (substr(source_logical_key, 1, ?) = ?
                    OR substr(target_logical_key, 1, ?) = ?)
                ORDER BY id LIMIT ?""",
                (
                    len(source_prefix),
                    source_prefix,
                    len(table_prefix),
                    table_prefix,
                    len(table_prefix),
                    table_prefix,
                    MAX_REVIEW_DECISIONS + 1,
                ),
            ).fetchall()
    except sqlite3.Error:
        raise QueryError("STORE_READ_FAILED", "Could not read local review decisions.") from None
    if len(rows) > MAX_REVIEW_DECISIONS:
        raise QueryError("INFERENCE_BUDGET_EXCEEDED", "Too many reviews for this table.")
    latest = {(str(row[0]), str(row[1])): str(row[2]) for row in rows}
    if any(
        decision not in {"REJECTED", "RESTORED", "APPROVED", "REVOKED"}
        for decision in latest.values()
    ):
        raise QueryError("STORE_READ_FAILED", "A local review decision is invalid.")
    return version, "APPROVED" in latest.values()


def _table_scope(value: str) -> bool:
    metadata = _metadata(value)
    scope = metadata.get("in_scope")
    if not isinstance(scope, bool):
        raise QueryError("STORE_READ_FAILED", "Local table scope is invalid.")
    return scope


def _read_columns(connection: sqlite3.Connection, snapshot_id: int) -> list[_Column]:
    table_rows = connection.execute(
        """SELECT id, metadata_json FROM objects
        WHERE snapshot_id = ? AND object_type = 'TABLE'
        ORDER BY id LIMIT ?""",
        (snapshot_id, MAX_PREVIEW_TABLES + 1),
    ).fetchall()
    if len(table_rows) > MAX_PREVIEW_TABLES:
        raise QueryError("INFERENCE_BUDGET_EXCEEDED", "Snapshot has too many tables for preview.")
    table_scope = {int(row[0]): _table_scope(str(row[1])) for row in table_rows}
    rows = connection.execute(
        """SELECT c.object_id, c.table_object_id, t.object_name,
               t.schema_name, t.qualified_name, o.object_name, o.qualified_name,
               c.data_type, c.unique_value
        FROM columns AS c
        JOIN objects AS o ON o.id = c.object_id AND o.snapshot_id = ?
          AND o.object_type = 'COLUMN'
        JOIN objects AS t ON t.id = c.table_object_id AND t.snapshot_id = ?
          AND t.object_type = 'TABLE'
        ORDER BY o.qualified_name LIMIT ?""",
        (snapshot_id, snapshot_id, MAX_PREVIEW_COLUMNS + 1),
    ).fetchall()
    if len(rows) > MAX_PREVIEW_COLUMNS:
        raise QueryError("INFERENCE_BUDGET_EXCEEDED", "Snapshot has too many columns for preview.")
    columns: list[_Column] = []
    for row in rows:
        table_id = int(row[1])
        if table_id not in table_scope:
            raise QueryError("STORE_READ_FAILED", "A column references an unknown table.")
        if not table_scope[table_id]:
            continue
        columns.append(
            _Column(
                object_id=int(row[0]),
                table_id=table_id,
                table_name=str(row[2]),
                schema_name=str(row[3]),
                table_qualified=str(row[4]),
                name=str(row[5]),
                qualified=str(row[6]),
                key_type=_KEY_TYPES.get(str(row[7]).casefold()),
                unique_value=bool(row[8]),
            )
        )
    return columns


def _approved_source_names(
    source_name: str,
    table_qualified: str,
    decisions: dict[tuple[str, str], str],
) -> tuple[str, ...]:
    prefix = f"{source_name}:postgres:column:"
    adjacent_prefix = f"{prefix}{table_qualified}."
    names = sorted(
        {
            source_key[len(prefix) :]
            for (source_key, target_key), decision in decisions.items()
            if decision == "APPROVED"
            and (source_key.startswith(adjacent_prefix) or target_key.startswith(adjacent_prefix))
        }
    )
    if len(names) > MAX_APPROVED_SOURCE_COLUMNS:
        raise QueryError("INFERENCE_BUDGET_EXCEEDED", "Too many approved source columns.")
    return tuple(names)


def _read_approved_relevant_columns(
    connection: sqlite3.Connection, snapshot_id: int, source_names: tuple[str, ...]
) -> list[_Column]:
    """Read only approved source columns and possible `id` targets."""

    if not source_names:
        return []
    placeholders = ",".join("?" for _ in source_names)
    rows = connection.execute(
        f"""SELECT c.object_id, c.table_object_id, t.object_name,
               t.schema_name, t.qualified_name, o.object_name, o.qualified_name,
               c.data_type, c.unique_value, t.metadata_json
        FROM columns AS c
        JOIN objects AS o ON o.id = c.object_id AND o.snapshot_id = ?
          AND o.object_type = 'COLUMN'
        JOIN objects AS t ON t.id = c.table_object_id AND t.snapshot_id = ?
          AND t.object_type = 'TABLE'
        WHERE o.object_name COLLATE NOCASE = 'id'
           OR o.qualified_name IN ({placeholders})
        ORDER BY o.qualified_name LIMIT ?""",
        (snapshot_id, snapshot_id, *source_names, MAX_APPROVED_RELEVANT_COLUMNS + 1),
    ).fetchall()
    if len(rows) > MAX_APPROVED_RELEVANT_COLUMNS:
        raise QueryError("INFERENCE_BUDGET_EXCEEDED", "Too many relevant columns for approval.")
    columns: list[_Column] = []
    for row in rows:
        if not _table_scope(str(row[9])):
            continue
        columns.append(
            _Column(
                object_id=int(row[0]),
                table_id=int(row[1]),
                table_name=str(row[2]),
                schema_name=str(row[3]),
                table_qualified=str(row[4]),
                name=str(row[5]),
                qualified=str(row[6]),
                key_type=_KEY_TYPES.get(str(row[7]).casefold()),
                unique_value=bool(row[8]),
            )
        )
    return columns


def _declared_source_columns(
    connection: sqlite3.Connection,
    snapshot_id: int,
    columns: list[_Column],
    source_table_ids: set[int] | None = None,
) -> set[int]:
    """Map table-level FK pairs to source columns, including external targets."""

    by_table_and_name = {(column.table_id, column.name): column.object_id for column in columns}
    table_filter = ""
    parameters: list[object] = [snapshot_id]
    if source_table_ids is not None:
        if not source_table_ids:
            return set()
        table_filter = (
            " AND e.source_object_id IN (" + ",".join("?" for _ in source_table_ids) + ")"
        )
        parameters.extend(sorted(source_table_ids))
    parameters.append(MAX_PREVIEW_FK_EDGES + 1)
    rows = connection.execute(
        """SELECT e.source_object_id, e.metadata_json FROM edges AS e
        JOIN objects AS source ON source.id = e.source_object_id
          AND source.snapshot_id = e.snapshot_id AND source.object_type = 'TABLE'
        JOIN objects AS target ON target.id = e.target_object_id
          AND target.snapshot_id = e.snapshot_id AND target.object_type = 'TABLE'
        WHERE e.snapshot_id = ? AND e.edge_type = 'REFERENCES'
          AND e.origin = 'DATABASE' AND e.status = 'CONFIRMED'"""
        + table_filter
        + " ORDER BY e.id LIMIT ?",
        parameters,
    ).fetchall()
    if len(rows) > MAX_PREVIEW_FK_EDGES:
        raise QueryError("INFERENCE_BUDGET_EXCEEDED", "Snapshot has too many declared FKs.")
    declared: set[int] = set()
    for table_id, raw_metadata in rows:
        metadata = _metadata(str(raw_metadata))
        pairs = metadata.get("column_pairs")
        if metadata.get("level") != "TABLE" or not isinstance(pairs, list) or not pairs:
            raise QueryError("STORE_READ_FAILED", "Declared FK metadata is invalid.")
        for pair in pairs:
            if (
                not isinstance(pair, list)
                or len(pair) != 2
                or not isinstance(pair[0], str)
                or not isinstance(pair[1], str)
            ):
                raise QueryError("STORE_READ_FAILED", "Declared FK column pairs are invalid.")
            column_id = by_table_and_name.get((int(table_id), pair[0]))
            if column_id is None:
                if source_table_ids is None:
                    raise QueryError("STORE_READ_FAILED", "Declared FK source column is missing.")
                continue
            declared.add(column_id)
    return declared


def _candidate(
    source: _Column, target: _Column, alternatives: int, status: str
) -> RelationshipCandidate:
    same_schema = source.schema_name == target.schema_name
    evidence = (
        CandidateEvidence("EXACT_TABLE_STEM", 1.0, 0.30, "Column stem matches target table."),
        CandidateEvidence("EXACT_KEY_TYPE", 1.0, 0.20, "Known key types match exactly."),
        CandidateEvidence("TARGET_UNIQUE", 1.0, 0.25, "Target id is a single-column key."),
        CandidateEvidence("SAME_SCHEMA", float(same_schema), 0.05, "Same schema preference."),
    )
    confidence = round(sum(item.score * item.weight for item in evidence), 2)
    return RelationshipCandidate(
        source_table=source.table_qualified,
        source_column=source.qualified,
        target_table=target.table_qualified,
        target_column=target.qualified,
        confidence=confidence,
        alternatives_for_source=alternatives,
        evidence=evidence,
        status=status,
    )


def preview_candidates(
    root: Path,
    source_name: str,
    limit: int = DEFAULT_CANDIDATE_LIMIT,
    *,
    include_ambiguous: bool = False,
    pair: tuple[str, str] | None = None,
    include_rejected: bool = False,
    adjacent_table: str | None = None,
    approved_only: bool = False,
) -> CandidatePreview:
    """Find bounded metadata-only hypotheses; never write edges or source data."""

    if not 1 <= limit <= MAX_CANDIDATE_LIMIT:
        raise QueryError("INVALID_LIMIT", f"Limit must be between 1 and {MAX_CANDIDATE_LIMIT}.")
    source = show_source(root, source_name)
    path = require_store_path(root)
    try:
        with closing(sqlite3.connect(path, timeout=5)) as connection:
            connection.execute("PRAGMA query_only = ON")
            connection.execute("BEGIN")
            snapshot_id, version = _latest_snapshot(connection, source.name)
            decisions = {} if include_rejected else _review_decisions(connection, source.name)
            if approved_only and adjacent_table is not None:
                selected_names = _approved_source_names(source.name, adjacent_table, decisions)
                columns = _read_approved_relevant_columns(connection, snapshot_id, selected_names)
                selected_tables = {
                    column.table_id for column in columns if column.qualified in selected_names
                }
                declared_sources = _declared_source_columns(
                    connection, snapshot_id, columns, selected_tables
                )
            else:
                columns = _read_columns(connection, snapshot_id)
                declared_sources = _declared_source_columns(connection, snapshot_id, columns)
            targets: dict[str, list[_Column]] = defaultdict(list)
            for column in columns:
                if column.name.casefold() == "id" and column.unique_value and column.key_type:
                    targets[column.table_name.casefold()].append(column)

            candidates: list[RelationshipCandidate] = []
            skipped_ambiguous = 0
            evaluated_pairs = 0
            for column in columns:
                if column.object_id in declared_sources:
                    continue
                name = column.name.casefold()
                if not name.endswith("_id") or not column.key_type:
                    continue
                stem = name[:-3]
                if not stem:
                    continue
                matches = [
                    target
                    for target in targets.get(stem, ())
                    if target.table_id != column.table_id and target.key_type == column.key_type
                ]
                matches = [
                    target
                    for target in matches
                    if decisions.get(
                        candidate_review_keys(source.name, column.qualified, target.qualified)
                    )
                    != "REJECTED"
                ]
                approved = [
                    target
                    for target in matches
                    if decisions.get(
                        candidate_review_keys(source.name, column.qualified, target.qualified)
                    )
                    == "APPROVED"
                ]
                evaluated_pairs += len(matches)
                if evaluated_pairs > MAX_PREVIEW_PAIRS:
                    raise QueryError(
                        "INFERENCE_BUDGET_EXCEEDED", "Too many candidate pairs for preview."
                    )
                if len(matches) > MAX_TARGETS_PER_COLUMN or (
                    len(matches) > 1 and not include_ambiguous and not approved
                ):
                    skipped_ambiguous += 1
                    continue
                visible = matches if include_ambiguous or not approved else approved
                for target in visible:
                    if pair is not None and (column.qualified, target.qualified) != pair:
                        continue
                    if adjacent_table is not None and adjacent_table not in {
                        column.table_qualified,
                        target.table_qualified,
                    }:
                        continue
                    status = (
                        "APPROVED"
                        if decisions.get(
                            candidate_review_keys(source.name, column.qualified, target.qualified)
                        )
                        == "APPROVED"
                        else "PENDING"
                    )
                    if approved_only and status != "APPROVED":
                        continue
                    candidates.append(_candidate(column, target, len(matches), status))
    except sqlite3.Error:
        raise QueryError("STORE_READ_FAILED", "Could not read the local graph store.") from None
    candidates.sort(
        key=lambda item: (
            item.status != "APPROVED",
            -item.confidence,
            item.source_column,
            item.target_column,
        )
    )
    return CandidatePreview(
        source_name=source.name,
        snapshot_version=version,
        scoring_method="metadata_exact_key_v3",
        include_ambiguous=include_ambiguous,
        candidates=tuple(candidates[:limit]),
        truncated=len(candidates) > limit,
        skipped_ambiguous_columns=skipped_ambiguous,
    )
