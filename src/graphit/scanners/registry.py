"""Select a database adapter from a validated source definition."""

from pathlib import Path

from graphit.scanners.mssql import MSSQLScanner
from graphit.scanners.oracle import OracleScanner
from graphit.scanners.postgresql import PostgreSQLScanner
from graphit.scanners.protocol import ConnectionTestResult, DatabaseScanner
from graphit.sources import SourceConfig


def scanner_for(source: SourceConfig, project_root: Path | None = None) -> DatabaseScanner:
    """Return the scanner registered for one supported source engine."""

    if source.engine == "postgresql":
        return PostgreSQLScanner(project_root)
    if source.engine == "mssql":
        return MSSQLScanner(project_root)
    if source.engine == "oracle":
        return OracleScanner(project_root)
    raise ValueError(f"Unsupported source engine: {source.engine}")


def verify_connection(
    source: SourceConfig, *, project_root: Path | None = None
) -> ConnectionTestResult:
    """Verify one supported source through its engine-specific adapter."""

    if source.engine == "postgresql":
        from graphit.scanners.postgresql import verify_connection as verify
    elif source.engine == "mssql":
        from graphit.scanners.mssql import verify_connection as verify
    elif source.engine == "oracle":
        from graphit.scanners.oracle import verify_connection as verify
    else:
        raise ValueError(f"Unsupported source engine: {source.engine}")
    return verify(source, project_root=project_root)
