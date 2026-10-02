"""Explicit, source-scoped decisions about local relationship hypotheses."""

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from graphit.inference import MAX_PREVIEW_FK_EDGES, candidate_review_keys, preview_candidates
from graphit.queries import QueryError, _latest_snapshot, _metadata
from graphit.sources import require_store_path, show_source

DEFAULT_PROPOSAL_LIMIT = 20
MAX_PROPOSAL_LIMIT = 50
MAX_PROPOSAL_EVENTS = 5000
MAX_MANUAL_GRAPH_PAIRS = 200


@dataclass(frozen=True)
class ReviewResult:
    source_name: str
    snapshot_version: int
    source_column: str
    target_column: str
    decision: str
    changed: bool


@dataclass(frozen=True)
class ProposalResult:
    source_name: str
    snapshot_version: int
    source_column: str
    target_column: str
    reason: str
    origin: str
    status: str
    changed: bool


@dataclass(frozen=True)
class ProposalItem:
    source_column: str
    target_column: str
    reason: str
    origin: str
    status: str
    eligibility: str


@dataclass(frozen=True)
class ProposalList:
    source_name: str
    snapshot_version: int
    proposals: tuple[ProposalItem, ...]
    offset: int
    truncated: bool


@dataclass(frozen=True)
class ManualGraphLink:
    source_table: str
    target_table: str
    source_column: str
    target_column: str
    reason: str


@dataclass(frozen=True)
class ManualGraphLinks:
    snapshot_version: int
    links: tuple[ManualGraphLink, ...]
    truncated: bool


def current_manual_graph_links(
    root: Path, source_name: str, focus_table: str, limit: int
) -> ManualGraphLinks:
    """Read eligible approved manual links adjacent to one exact saved table."""

    if not 1 <= limit <= MAX_PROPOSAL_LIMIT:
        raise QueryError("INVALID_LIMIT", "Manual graph limit must be 1-50.")
    source = show_source(root, source_name)
    path = require_store_path(root)
    prefix = f"{source.name}:postgres:column:"
    focus_prefix = prefix + focus_table + "."
    try:
        with closing(sqlite3.connect(path, timeout=5)) as connection:
            connection.execute("PRAGMA query_only = ON")
            connection.execute("BEGIN")
            snapshot_id, version = _latest_snapshot(connection, source.name)
            rows = connection.execute(
                """SELECT source_logical_key, target_logical_key, decision, comment
                FROM review_decisions
                WHERE relationship_kind = 'MANUAL_PROPOSAL'
                  AND substr(source_logical_key, 1, ?) = ?
                ORDER BY id LIMIT ?""",
                (len(prefix), prefix, MAX_PROPOSAL_EVENTS + 1),
            ).fetchall()
            if len(rows) > MAX_PROPOSAL_EVENTS:
                raise QueryError("INFERENCE_BUDGET_EXCEEDED", "Too many manual proposal events.")
            latest: dict[tuple[str, str], tuple[str, str]] = {}
            for source_key, target_key, decision, reason in rows:
                if (
                    not isinstance(source_key, str)
                    or not isinstance(target_key, str)
                    or not source_key.startswith(prefix)
                    or not target_key.startswith(prefix)
                    or len(source_key) <= len(prefix)
                    or len(target_key) <= len(prefix)
                    or decision not in {"PROPOSED", "APPROVED", "REVOKED"}
                    or not _valid_manual_reason(reason)
                ):
                    raise QueryError("STORE_READ_FAILED", "Local proposal history is invalid.")
                latest[(source_key, target_key)] = (str(decision), reason)
            adjacent = sorted(
                (source_key[len(prefix) :], target_key[len(prefix) :], reason)
                for (source_key, target_key), (decision, reason) in latest.items()
                if decision == "APPROVED"
                and (source_key.startswith(focus_prefix) or target_key.startswith(focus_prefix))
            )
            if len(adjacent) > MAX_MANUAL_GRAPH_PAIRS:
                raise QueryError("INFERENCE_BUDGET_EXCEEDED", "Too many adjacent manual links.")
            pairs = [(source_column, target_column) for source_column, target_column, _ in adjacent]
            eligibility = _proposal_eligibility(connection, snapshot_id, pairs)
            eligible = [item for item in adjacent if eligibility[item[:2]] == "ELIGIBLE"]
            page = eligible[: limit + 1]
            names = sorted({name for item in page for name in item[:2]})
            tables: dict[str, str] = {}
            if names:
                placeholders = ",".join("?" for _ in names)
                table_rows = connection.execute(
                    f"""SELECT column_object.qualified_name, table_object.qualified_name
                    FROM objects AS column_object
                    JOIN columns AS c ON c.object_id = column_object.id
                    JOIN objects AS table_object ON table_object.id = c.table_object_id
                    WHERE column_object.snapshot_id = ?
                      AND column_object.object_type = 'COLUMN'
                      AND column_object.qualified_name IN ({placeholders})""",
                    (snapshot_id, *names),
                ).fetchall()
                tables = {str(name): str(table_name) for name, table_name in table_rows}
                if any(name not in tables for name in names):
                    raise QueryError("STORE_READ_FAILED", "Manual link endpoint is invalid.")
            links = tuple(
                ManualGraphLink(
                    tables[source_column],
                    tables[target_column],
                    source_column,
                    target_column,
                    reason,
                )
                for source_column, target_column, reason in page[:limit]
            )
            return ManualGraphLinks(version, links, len(eligible) > limit)
    except sqlite3.Error:
        raise QueryError("STORE_READ_FAILED", "Could not read local manual links.") from None


def list_proposals(
    root: Path,
    source_name: str,
    limit: int = DEFAULT_PROPOSAL_LIMIT,
    offset: int = 0,
) -> ProposalList:
    """Read latest manual proposal events and label current snapshot eligibility."""

    if limit < 1 or limit > MAX_PROPOSAL_LIMIT or offset < 0 or offset > MAX_PROPOSAL_EVENTS:
        raise QueryError("INVALID_LIMIT", "Use limit 1-50 and offset 0-5000.")
    source = show_source(root, source_name)
    path = require_store_path(root)
    prefix = f"{source.name}:postgres:column:"
    try:
        with closing(sqlite3.connect(path, timeout=5)) as connection:
            connection.execute("PRAGMA query_only = ON")
            connection.execute("BEGIN")
            snapshot_id, version = _latest_snapshot(connection, source.name)
            rows = connection.execute(
                """SELECT id, source_logical_key, target_logical_key, decision, comment
                FROM review_decisions
                WHERE relationship_kind = 'MANUAL_PROPOSAL'
                  AND substr(source_logical_key, 1, ?) = ?
                ORDER BY id LIMIT ?""",
                (len(prefix), prefix, MAX_PROPOSAL_EVENTS + 1),
            ).fetchall()
            if len(rows) > MAX_PROPOSAL_EVENTS:
                raise QueryError("INFERENCE_BUDGET_EXCEEDED", "Too many manual proposal events.")
            latest: dict[tuple[str, str], tuple[int, str, str]] = {}
            for event_id, source_key, target_key, decision, comment in rows:
                if (
                    not isinstance(source_key, str)
                    or not isinstance(target_key, str)
                    or not source_key.startswith(prefix)
                    or not target_key.startswith(prefix)
                    or len(source_key) <= len(prefix)
                    or len(target_key) <= len(prefix)
                    or decision not in {"PROPOSED", "APPROVED", "REVOKED"}
                    or not _valid_manual_reason(comment)
                ):
                    raise QueryError("STORE_READ_FAILED", "Local proposal history is invalid.")
                latest[(source_key[len(prefix) :], target_key[len(prefix) :])] = (
                    int(event_id),
                    comment,
                    str(decision),
                )
            ordered = sorted(latest.items(), key=lambda item: item[1][0], reverse=True)
            page = ordered[offset : offset + limit]
            statuses = _proposal_eligibility(connection, snapshot_id, [pair for pair, _ in page])
            items = tuple(
                ProposalItem(*pair, value[1], "MANUAL", value[2], statuses[pair])
                for pair, value in page
            )
            return ProposalList(source.name, version, items, offset, len(ordered) > offset + limit)
    except sqlite3.Error:
        raise QueryError("STORE_READ_FAILED", "Could not read local proposals.") from None


def _proposal_eligibility(
    connection: sqlite3.Connection,
    snapshot_id: int,
    pairs: list[tuple[str, str]],
) -> dict[tuple[str, str], str]:
    if not pairs:
        return {}
    names = sorted({name for pair in pairs for name in pair})
    placeholders = ",".join("?" for _ in names)
    columns = connection.execute(
        f"""SELECT o.qualified_name, o.object_name, c.table_object_id,
                   c.data_type, c.unique_value
        FROM objects AS o JOIN columns AS c ON c.object_id = o.id
        WHERE o.snapshot_id = ? AND o.object_type = 'COLUMN'
          AND o.qualified_name IN ({placeholders})""",
        (snapshot_id, *names),
    ).fetchall()
    by_name = {str(row[0]): row for row in columns}
    table_ids = sorted({int(by_name[pair[0]][2]) for pair in pairs if pair[0] in by_name})
    fk_sources: set[tuple[int, str]] = set()
    if table_ids:
        fk_placeholders = ",".join("?" for _ in table_ids)
        fk_rows = connection.execute(
            f"""SELECT source_object_id, metadata_json FROM edges
            WHERE snapshot_id = ? AND source_object_id IN ({fk_placeholders})
              AND edge_type = 'REFERENCES' AND origin = 'DATABASE'
              AND status = 'CONFIRMED' ORDER BY id LIMIT ?""",
            (snapshot_id, *table_ids, MAX_PREVIEW_FK_EDGES + 1),
        ).fetchall()
        if len(fk_rows) > MAX_PREVIEW_FK_EDGES:
            raise QueryError("INFERENCE_BUDGET_EXCEEDED", "Too many proposal-table FKs.")
        for table_id, raw_metadata in fk_rows:
            metadata = _metadata(str(raw_metadata))
            column_pairs = metadata.get("column_pairs")
            if (
                metadata.get("level") != "TABLE"
                or not isinstance(column_pairs, list)
                or not column_pairs
            ):
                raise QueryError("STORE_READ_FAILED", "Declared FK metadata is invalid.")
            for columns_pair in column_pairs:
                if (
                    not isinstance(columns_pair, list)
                    or len(columns_pair) != 2
                    or not all(isinstance(name, str) for name in columns_pair)
                ):
                    raise QueryError("STORE_READ_FAILED", "Declared FK pairs are invalid.")
                fk_sources.add((int(table_id), columns_pair[0]))
    statuses: dict[tuple[str, str], str] = {}
    for pair in pairs:
        source_row = by_name.get(pair[0])
        target_row = by_name.get(pair[1])
        if source_row is None:
            status = "SOURCE_COLUMN_MISSING"
        elif target_row is None:
            status = "TARGET_COLUMN_MISSING"
        elif not bool(target_row[4]):
            status = "TARGET_KEY_REMOVED"
        elif str(source_row[3]).casefold() != str(target_row[3]).casefold():
            status = "TYPE_CHANGED"
        elif (int(source_row[2]), str(source_row[1])) in fk_sources:
            status = "DECLARED_FK_ADDED"
        else:
            status = "ELIGIBLE"
        statuses[pair] = status
    return statuses


def _valid_manual_reason(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and len(value) <= 300
        and all(ord(char) >= 32 for char in value)
    )


def approve_manual_proposal(
    root: Path,
    source_name: str,
    source_column: str,
    target_column: str,
    snapshot_version: int,
) -> ProposalResult:
    """Approve one current human proposal locally, without graph exposure."""

    pair = _validate_request(source_column, target_column, snapshot_version)
    source = show_source(root, source_name)
    source_key, target_key = candidate_review_keys(source.name, *pair)
    path = require_store_path(root)
    try:
        with closing(sqlite3.connect(path, timeout=5)) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            snapshot_id, current_version = _latest_snapshot(connection, source.name)
            if current_version != snapshot_version:
                raise QueryError("SNAPSHOT_CHANGED", "Refresh proposals from the latest snapshot.")
            row = connection.execute(
                """SELECT decision, comment FROM review_decisions
                WHERE source_logical_key = ? AND target_logical_key = ?
                  AND relationship_kind = 'MANUAL_PROPOSAL'
                ORDER BY id DESC LIMIT 1""",
                (source_key, target_key),
            ).fetchone()
            if row is None:
                raise QueryError("PROPOSAL_NOT_FOUND", "No manual proposal exists for this pair.")
            decision, reason = row
            if decision not in {"PROPOSED", "APPROVED", "REVOKED"} or not _valid_manual_reason(
                reason
            ):
                raise QueryError("STORE_READ_FAILED", "Local proposal history is invalid.")
            if decision == "REVOKED":
                raise QueryError("PROPOSAL_REVOKED", "Re-propose this pair before approving again.")
            if _latest_decision(connection, source_key, target_key) in {"REJECTED", "APPROVED"}:
                raise QueryError("REVIEW_CONFLICT", "Pair has a conflicting inferred review.")
            eligibility = _proposal_eligibility(connection, snapshot_id, [pair])[pair]
            if eligibility != "ELIGIBLE":
                raise QueryError(
                    "PROPOSAL_STALE", f"Proposal is no longer eligible: {eligibility}."
                )
            changed = decision != "APPROVED"
            if changed:
                connection.execute(
                    """INSERT INTO review_decisions
                    (source_logical_key, target_logical_key,
                     relationship_kind, decision, comment)
                    VALUES (?, ?, 'MANUAL_PROPOSAL', 'APPROVED', ?)""",
                    (source_key, target_key, reason),
                )
    except sqlite3.Error:
        raise QueryError("STORE_WRITE_FAILED", "Could not approve the local proposal.") from None
    return ProposalResult(
        source.name, snapshot_version, *pair, reason, "MANUAL", "APPROVED", changed
    )


def revoke_manual_proposal(
    root: Path,
    source_name: str,
    source_column: str,
    target_column: str,
    snapshot_version: int,
) -> ProposalResult:
    """Withdraw an approved human proposal without deleting its audit history."""

    pair = _validate_request(source_column, target_column, snapshot_version)
    source = show_source(root, source_name)
    source_key, target_key = candidate_review_keys(source.name, *pair)
    path = require_store_path(root)
    try:
        with closing(sqlite3.connect(path, timeout=5)) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            _, current_version = _latest_snapshot(connection, source.name)
            if current_version != snapshot_version:
                raise QueryError("SNAPSHOT_CHANGED", "Refresh proposals from the latest snapshot.")
            row = connection.execute(
                """SELECT decision, comment FROM review_decisions
                WHERE source_logical_key = ? AND target_logical_key = ?
                  AND relationship_kind = 'MANUAL_PROPOSAL'
                ORDER BY id DESC LIMIT 1""",
                (source_key, target_key),
            ).fetchone()
            if row is None or row[0] == "PROPOSED":
                raise QueryError("MANUAL_APPROVAL_NOT_FOUND", "Pair has no manual approval.")
            decision, reason = row
            if decision not in {"APPROVED", "REVOKED"} or not _valid_manual_reason(reason):
                raise QueryError("STORE_READ_FAILED", "Local proposal history is invalid.")
            changed = decision == "APPROVED"
            if changed:
                connection.execute(
                    """INSERT INTO review_decisions
                    (source_logical_key, target_logical_key,
                     relationship_kind, decision, comment)
                    VALUES (?, ?, 'MANUAL_PROPOSAL', 'REVOKED', ?)""",
                    (source_key, target_key, reason),
                )
    except sqlite3.Error:
        raise QueryError("STORE_WRITE_FAILED", "Could not revoke the local approval.") from None
    return ProposalResult(
        source.name, snapshot_version, *pair, reason, "MANUAL", "REVOKED", changed
    )


def propose_relationship(
    root: Path,
    source_name: str,
    source_column: str,
    target_column: str,
    snapshot_version: int,
    reason: str,
) -> ProposalResult:
    """Save a human hypothesis locally; never approve or expose it as a graph fact."""

    pair = _validate_request(source_column, target_column, snapshot_version)
    if pair[0] == pair[1]:
        raise QueryError("INVALID_PROPOSAL", "Source and target columns must differ.")
    if not _valid_manual_reason(reason):
        raise QueryError("INVALID_PROPOSAL", "Give a short, single-line business reason.")
    source = show_source(root, source_name)
    source_key, target_key = candidate_review_keys(source.name, *pair)
    path = require_store_path(root)
    try:
        with closing(sqlite3.connect(path, timeout=5)) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            snapshot_id, current_version = _latest_snapshot(connection, source.name)
            if current_version != snapshot_version:
                raise QueryError(
                    "SNAPSHOT_CHANGED", "Refresh the latest snapshot before proposing."
                )
            rows = connection.execute(
                """SELECT o.qualified_name, o.object_name, c.table_object_id,
                       c.data_type, c.unique_value
                FROM objects AS o JOIN columns AS c ON c.object_id = o.id
                WHERE o.snapshot_id = ? AND o.object_type = 'COLUMN'
                  AND o.qualified_name IN (?, ?)""",
                (snapshot_id, *pair),
            ).fetchall()
            by_name = {str(row[0]): row for row in rows}
            if any(name not in by_name for name in pair):
                raise QueryError("INVALID_PROPOSAL", "Both exact columns must exist in this scan.")
            source_row, target_row = by_name[pair[0]], by_name[pair[1]]
            if not bool(target_row[4]):
                raise QueryError("INVALID_PROPOSAL", "Target must be a single-column key.")
            if str(source_row[3]).casefold() != str(target_row[3]).casefold():
                raise QueryError("INVALID_PROPOSAL", "Column data types must match exactly.")
            fk_rows = connection.execute(
                """SELECT metadata_json FROM edges
                WHERE snapshot_id = ? AND source_object_id = ?
                  AND edge_type = 'REFERENCES' AND origin = 'DATABASE'
                  AND status = 'CONFIRMED' ORDER BY id LIMIT ?""",
                (snapshot_id, int(source_row[2]), MAX_PREVIEW_FK_EDGES + 1),
            ).fetchall()
            if len(fk_rows) > MAX_PREVIEW_FK_EDGES:
                raise QueryError("INFERENCE_BUDGET_EXCEEDED", "Too many source-table FKs.")
            for (raw_metadata,) in fk_rows:
                metadata = _metadata(str(raw_metadata))
                pairs = metadata.get("column_pairs")
                if metadata.get("level") != "TABLE" or not isinstance(pairs, list) or not pairs:
                    raise QueryError("STORE_READ_FAILED", "Declared FK metadata is invalid.")
                for columns in pairs:
                    if (
                        not isinstance(columns, list)
                        or len(columns) != 2
                        or not all(isinstance(name, str) for name in columns)
                    ):
                        raise QueryError("STORE_READ_FAILED", "Declared FK pairs are invalid.")
                    if columns[0] == source_row[1]:
                        raise QueryError(
                            "INVALID_PROPOSAL", "Source column already has a declared FK."
                        )
            previous = connection.execute(
                """SELECT decision, comment FROM review_decisions
                WHERE source_logical_key = ? AND target_logical_key = ?
                  AND relationship_kind = 'MANUAL_PROPOSAL'
                ORDER BY id DESC LIMIT 1""",
                (source_key, target_key),
            ).fetchone()
            if previous is not None and previous[0] == "APPROVED":
                raise QueryError(
                    "REVIEW_CONFLICT", "Revoke the approved proposal before changing it."
                )
            if previous is not None and previous[0] not in {"PROPOSED", "REVOKED"}:
                raise QueryError("STORE_READ_FAILED", "Local proposal history is invalid.")
            changed = previous is None or previous[0] == "REVOKED" or previous[1] != reason
            if changed:
                connection.execute(
                    """INSERT INTO review_decisions
                    (source_logical_key, target_logical_key,
                     relationship_kind, decision, comment)
                    VALUES (?, ?, 'MANUAL_PROPOSAL', 'PROPOSED', ?)""",
                    (source_key, target_key, reason),
                )
    except sqlite3.Error:
        raise QueryError("STORE_WRITE_FAILED", "Could not save the local proposal.") from None
    return ProposalResult(
        source.name, snapshot_version, *pair, reason, "MANUAL", "PROPOSED", changed
    )


def _validate_request(
    source_column: str, target_column: str, snapshot_version: int
) -> tuple[str, str]:
    if snapshot_version < 1:
        raise QueryError("INVALID_SNAPSHOT_VERSION", "Snapshot version must be positive.")
    if any(
        not value or len(value) > 512 or any(ord(char) < 32 for char in value)
        for value in (source_column, target_column)
    ):
        raise QueryError("INVALID_CANDIDATE", "Use exact qualified source and target columns.")
    return source_column, target_column


def _latest_decision(
    connection: sqlite3.Connection, source_key: str, target_key: str
) -> str | None:
    row = connection.execute(
        """SELECT decision FROM review_decisions
        WHERE source_logical_key = ? AND target_logical_key = ?
          AND relationship_kind = 'LIKELY_REFERENCES'
        ORDER BY id DESC LIMIT 1""",
        (source_key, target_key),
    ).fetchone()
    return str(row[0]) if row is not None else None


def _append_decision(
    connection: sqlite3.Connection, source_key: str, target_key: str, decision: str
) -> None:
    connection.execute(
        """INSERT INTO review_decisions
        (source_logical_key, target_logical_key, relationship_kind, decision)
        VALUES (?, ?, 'LIKELY_REFERENCES', ?)""",
        (source_key, target_key, decision),
    )


def _decide_current_candidate(
    root: Path,
    source_name: str,
    source_column: str,
    target_column: str,
    snapshot_version: int,
    decision: str,
) -> ReviewResult:
    """Record a human decision for one current hypothesis, never a database FK."""

    pair = _validate_request(source_column, target_column, snapshot_version)
    preview = preview_candidates(
        root,
        source_name,
        limit=1,
        include_ambiguous=True,
        pair=pair,
        include_rejected=True,
    )
    if preview.snapshot_version != snapshot_version:
        raise QueryError("SNAPSHOT_CHANGED", "Refresh candidates from the latest snapshot.")
    if not preview.candidates:
        raise QueryError("CANDIDATE_NOT_FOUND", "Pair is not a current inferred candidate.")

    source_key, target_key = candidate_review_keys(source_name, *pair)
    path = require_store_path(root)
    try:
        with closing(sqlite3.connect(path, timeout=5)) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            _, current_version = _latest_snapshot(connection, source_name)
            if current_version != snapshot_version:
                raise QueryError("SNAPSHOT_CHANGED", "Refresh candidates from the latest snapshot.")
            current = _latest_decision(connection, source_key, target_key)
            if current not in {None, decision, "RESTORED", "REVOKED"}:
                raise QueryError("REVIEW_CONFLICT", "Pair already has a different review decision.")
            changed = current != decision
            if changed:
                _append_decision(connection, source_key, target_key, decision)
    except sqlite3.Error:
        raise QueryError(
            "STORE_WRITE_FAILED", "Could not save the local review decision."
        ) from None
    return ReviewResult(source_name, snapshot_version, *pair, decision, changed)


def reject_candidate(
    root: Path,
    source_name: str,
    source_column: str,
    target_column: str,
    snapshot_version: int,
) -> ReviewResult:
    """Reject one current, exact candidate without touching the target database."""

    return _decide_current_candidate(
        root, source_name, source_column, target_column, snapshot_version, "REJECTED"
    )


def approve_candidate(
    root: Path,
    source_name: str,
    source_column: str,
    target_column: str,
    snapshot_version: int,
) -> ReviewResult:
    """Approve one current logical hypothesis, not a database-declared FK."""

    return _decide_current_candidate(
        root, source_name, source_column, target_column, snapshot_version, "APPROVED"
    )


def _reverse_decision(
    root: Path,
    source_name: str,
    source_column: str,
    target_column: str,
    snapshot_version: int,
    expected: str,
    event: str,
    missing_code: str,
) -> ReviewResult:
    """Append a reversal event without deleting local review history."""

    pair = _validate_request(source_column, target_column, snapshot_version)
    source = show_source(root, source_name)
    source_key, target_key = candidate_review_keys(source.name, *pair)
    path = require_store_path(root)
    try:
        with closing(sqlite3.connect(path, timeout=5)) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            _, current_version = _latest_snapshot(connection, source.name)
            if current_version != snapshot_version:
                raise QueryError("SNAPSHOT_CHANGED", "Refresh candidates from the latest snapshot.")
            current = _latest_decision(connection, source_key, target_key)
            if current is None:
                raise QueryError(missing_code, "Pair has no decision of this kind to reverse.")
            if current not in {expected, event}:
                raise QueryError("REVIEW_CONFLICT", "Pair has a different review decision.")
            changed = current == expected
            if changed:
                _append_decision(connection, source_key, target_key, event)
    except sqlite3.Error:
        raise QueryError(
            "STORE_WRITE_FAILED", "Could not save the local review decision."
        ) from None
    return ReviewResult(source.name, snapshot_version, *pair, event, changed)


def restore_candidate(
    root: Path,
    source_name: str,
    source_column: str,
    target_column: str,
    snapshot_version: int,
) -> ReviewResult:
    """Restore a previously rejected pair without deleting review history."""

    return _reverse_decision(
        root,
        source_name,
        source_column,
        target_column,
        snapshot_version,
        "REJECTED",
        "RESTORED",
        "REJECTION_NOT_FOUND",
    )


def revoke_approval(
    root: Path,
    source_name: str,
    source_column: str,
    target_column: str,
    snapshot_version: int,
) -> ReviewResult:
    """Revoke human approval while leaving database facts untouched."""

    return _reverse_decision(
        root,
        source_name,
        source_column,
        target_column,
        snapshot_version,
        "APPROVED",
        "REVOKED",
        "APPROVAL_NOT_FOUND",
    )
