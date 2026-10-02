"""Bounded, source-scoped discovery of completed local snapshot versions."""

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from graphit.queries import QueryError
from graphit.sources import require_store_path, show_source

DEFAULT_HISTORY_LIMIT = 20
MAX_HISTORY_LIMIT = 100


@dataclass(frozen=True)
class SnapshotSummary:
    version: int
    status: Literal["COMPLETED"]
    completed_at: str


@dataclass(frozen=True)
class SnapshotHistory:
    source_name: str
    snapshots: tuple[SnapshotSummary, ...]
    snapshot_count: int
    truncated: bool


def list_snapshots(
    root: Path, source_name: str, limit: int = DEFAULT_HISTORY_LIMIT
) -> SnapshotHistory:
    """List completed versions without connecting to the source database."""

    if not 1 <= limit <= MAX_HISTORY_LIMIT:
        raise QueryError("INVALID_LIMIT", f"Limit must be between 1 and {MAX_HISTORY_LIMIT}.")
    source = show_source(root, source_name)
    path = require_store_path(root)
    try:
        with closing(sqlite3.connect(path, timeout=5)) as connection:
            connection.execute("PRAGMA query_only = ON")
            connection.execute("BEGIN")
            count_row = connection.execute(
                """SELECT COUNT(*) FROM snapshots AS s
                JOIN scan_runs AS r ON r.id = s.scan_run_id
                JOIN sources AS src ON src.id = s.source_id
                WHERE src.name = ? AND r.source_id = s.source_id
                  AND r.status = 'COMPLETED'""",
                (source.name,),
            ).fetchone()
            rows = connection.execute(
                """SELECT s.version, r.completed_at FROM snapshots AS s
                JOIN scan_runs AS r ON r.id = s.scan_run_id
                JOIN sources AS src ON src.id = s.source_id
                WHERE src.name = ? AND r.source_id = s.source_id
                  AND r.status = 'COMPLETED'
                ORDER BY s.version DESC LIMIT ?""",
                (source.name, limit + 1),
            ).fetchall()
    except sqlite3.Error:
        raise QueryError("STORE_READ_FAILED", "Could not read the local graph store.") from None
    if count_row is None:
        raise QueryError("STORE_READ_FAILED", "Could not read the local graph store.")
    summaries: list[SnapshotSummary] = []
    for version, completed_at in rows[:limit]:
        if (
            type(version) is not int
            or version < 1
            or not isinstance(completed_at, str)
            or not completed_at
        ):
            raise QueryError("STORE_READ_FAILED", "Local snapshot history is invalid.")
        summaries.append(SnapshotSummary(version, "COMPLETED", completed_at))
    return SnapshotHistory(source.name, tuple(summaries), int(count_row[0]), len(rows) > limit)
