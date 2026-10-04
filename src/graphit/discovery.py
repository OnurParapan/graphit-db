"""Secret-safe discovery of supported project database connection URLs."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, unquote, urlsplit

from graphit.sources import valid_dotenv_credential_file

_URL_ENV_NAME = re.compile(
    r"(?:^|_)(?:DATABASE|DB|POSTGRES|POSTGRESQL|MSSQL|SQLSERVER|ORACLE)_(?:URL|URI)\Z"
    r"|^SQLALCHEMY_DATABASE_URI\Z|^PGURL\Z",
    re.IGNORECASE,
)
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_DOTENV_NAMES = (
    ".env",
    ".env.local",
    ".env.development",
    ".env.development.local",
    ".env.test",
    ".env.test.local",
    ".env.production",
    ".env.production.local",
)
_MAX_DOTENV_BYTES = 1024 * 1024
_MAX_DOTENV_FILES = 32
_MAX_DOTENV_DEPTH = 3
_EXCLUDED_DIRECTORIES = frozenset(
    {
        ".git",
        ".graphit",
        ".hg",
        ".svn",
        ".tox",
        ".venv",
        "build",
        "dist",
        "node_modules",
        "venv",
        "vendor",
    }
)
_POSTGRES_SSL_MODES = frozenset({"disable", "prefer", "require", "verify-ca", "verify-full"})
_MSSQL_SSL_MODES = frozenset({"disable", "require", "require-trust-server-certificate"})
_ORACLE_SSL_MODES = frozenset({"disable", "require"})

if TYPE_CHECKING:
    from graphit.sources import SourceConfig


class CredentialResolutionError(Exception):
    """A saved non-secret credential reference cannot be resolved safely."""


@dataclass(frozen=True)
class DatabaseCandidate:
    """One transient connection candidate; its password is never represented."""

    variable_name: str
    origin: str
    engine: str
    host: str
    port: int
    database_name: str
    username: str
    password: str | None = field(repr=False)
    ssl_mode: str

    @property
    def has_password(self) -> bool:
        """Report presence without exposing the password value."""

        return self.password is not None and self.password != ""


def _dotenv_value(raw: str) -> str | None:
    value = raw.strip()
    if not value:
        return ""
    if value[0] in {'"', "'"}:
        quote = value[0]
        if len(value) < 2 or value[-1] != quote:
            return None
        return value[1:-1]
    comment = value.find(" #")
    return value if comment < 0 else value[:comment].rstrip()


def _read_dotenv(path: Path) -> dict[str, str]:
    if not path.is_file() or path.is_symlink() or path.stat().st_size > _MAX_DOTENV_BYTES:
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return {}

    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        name, separator, raw_value = line.partition("=")
        name = name.strip()
        if not separator or not _ENV_NAME.fullmatch(name):
            continue
        value = _dotenv_value(raw_value)
        if value is not None:
            values[name] = value
    return values


def _dotenv_files(root: Path) -> tuple[tuple[str, Path], ...]:
    """Return deterministic, bounded dotenv files without following links."""

    found: list[tuple[str, Path]] = []
    pending: list[tuple[Path, int]] = [(root, 0)]
    while pending and len(found) < _MAX_DOTENV_FILES:
        directory, depth = pending.pop(0)
        try:
            entries = sorted(directory.iterdir(), key=lambda item: item.name.casefold())
        except OSError:
            continue
        files = {entry.name: entry for entry in entries if entry.name in _DOTENV_NAMES}
        for name in _DOTENV_NAMES:
            path = files.get(name)
            if path is not None and path.is_file() and not path.is_symlink():
                found.append((path.relative_to(root).as_posix(), path))
                if len(found) == _MAX_DOTENV_FILES:
                    break
        if depth >= _MAX_DOTENV_DEPTH:
            continue
        for entry in entries:
            if (
                entry.name.casefold() not in _EXCLUDED_DIRECTORIES
                and entry.is_dir()
                and not entry.is_symlink()
            ):
                pending.append((entry, depth + 1))
    return tuple(found)


def _safe_text(value: str) -> bool:
    return bool(value) and not any(ord(character) < 32 for character in value)


def _parse_candidate(variable_name: str, value: str, origin: str) -> DatabaseCandidate | None:
    try:
        parsed = urlsplit(value)
        scheme = parsed.scheme.lower()
        engine = {
            "postgres": "postgresql",
            "postgresql": "postgresql",
            "postgresql+asyncpg": "postgresql",
            "postgresql+psycopg": "postgresql",
            "mssql": "mssql",
            "mssql+aioodbc": "mssql",
            "mssql+pyodbc": "mssql",
            "sqlserver": "mssql",
            "oracle": "oracle",
            "oracle+oracledb": "oracle",
            "oracle+cx_oracle": "oracle",
            "oracles": "oracle",
        }.get(scheme)
        if engine is None:
            return None
        host = parsed.hostname
        port = parsed.port or {"postgresql": 5432, "mssql": 1433, "oracle": 1521}[engine]
        username = unquote(parsed.username) if parsed.username is not None else ""
        password = unquote(parsed.password) if parsed.password is not None else None
        query = {
            name.casefold(): values
            for name, values in parse_qs(parsed.query, keep_blank_values=True).items()
        }
        database_name = unquote(parsed.path.removeprefix("/"))
        if engine == "oracle" and not database_name:
            database_name = unquote(query.get("service_name", [""])[-1])
        if engine == "postgresql":
            ssl_mode = query.get("sslmode", ["prefer"])[-1].lower()
            valid_ssl_modes = _POSTGRES_SSL_MODES
        elif engine == "mssql":
            encrypt = query.get("encrypt", ["true"])[-1].lower()
            trust_certificate = query.get("trustservercertificate", ["false"])[-1].lower()
            if encrypt not in {"true", "yes", "1", "false", "no", "0"}:
                return None
            if trust_certificate not in {"true", "yes", "1", "false", "no", "0"}:
                return None
            ssl_mode = (
                "disable"
                if encrypt in {"false", "no", "0"}
                else "require-trust-server-certificate"
                if trust_certificate in {"true", "yes", "1"}
                else "require"
            )
            valid_ssl_modes = _MSSQL_SSL_MODES
        else:
            ssl_mode = "require" if scheme == "oracles" else "disable"
            valid_ssl_modes = _ORACLE_SSL_MODES
    except (TypeError, ValueError):
        return None

    if (
        parsed.fragment
        or host is None
        or not _safe_text(host)
        or not _safe_text(username)
        or not _safe_text(database_name)
        or not 1 <= port <= 65535
        or ssl_mode not in valid_ssl_modes
    ):
        return None
    return DatabaseCandidate(
        variable_name=variable_name,
        origin=origin,
        engine=engine,
        host=host,
        port=port,
        database_name=database_name,
        username=username,
        password=password,
        ssl_mode=ssl_mode,
    )


def discover_databases(
    root: Path, *, environ: Mapping[str, str] | None = None
) -> tuple[DatabaseCandidate, ...]:
    """Find bounded supported URL candidates without persisting or printing secrets."""

    root = root.resolve()
    process_environment = os.environ if environ is None else environ
    locations: list[tuple[str, Mapping[str, str]]] = [
        ("environment", process_environment),
    ]
    for name, path in _dotenv_files(root):
        dotenv_values = _read_dotenv(path)
        if dotenv_values:
            locations.append((name, dotenv_values))

    candidates: list[DatabaseCandidate] = []
    seen_variables: set[str] = set()
    seen_connections: set[tuple[str, str, int, str, str]] = set()
    for origin, values in locations:
        for variable_name in sorted(values):
            normalized_name = variable_name.upper()
            if normalized_name in seen_variables or not _URL_ENV_NAME.search(normalized_name):
                continue
            seen_variables.add(normalized_name)
            candidate = _parse_candidate(variable_name, values[variable_name], origin)
            if candidate is None:
                continue
            identity = (
                candidate.engine,
                candidate.host.casefold(),
                candidate.port,
                candidate.database_name,
                candidate.username,
            )
            if identity in seen_connections:
                continue
            seen_connections.add(identity)
            candidates.append(candidate)
    return tuple(candidates)


PostgreSQLCandidate = DatabaseCandidate


def discover_postgresql(
    root: Path, *, environ: Mapping[str, str] | None = None
) -> tuple[DatabaseCandidate, ...]:
    """Backward-compatible PostgreSQL-only discovery helper."""

    return tuple(
        candidate
        for candidate in discover_databases(root, environ=environ)
        if candidate.engine == "postgresql"
    )


def resolve_database_password(
    source: SourceConfig,
    *,
    root: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Resolve one password at call time and fail if its URL identity changed."""

    process_environment = os.environ if environ is None else environ
    if source.credential_kind == "password_env":
        password = process_environment.get(source.credential_env)
        if not password:
            raise CredentialResolutionError(
                f"Set the {source.credential_env} environment variable before using this source."
            )
        return password

    if source.credential_kind == "url_env":
        value = process_environment.get(source.credential_env)
        origin = "environment"
    elif source.credential_kind == "url_dotenv":
        credential_file = source.credential_file
        if root is None or not valid_dotenv_credential_file(credential_file):
            raise CredentialResolutionError("The saved dotenv credential reference is unavailable.")
        assert credential_file is not None
        project_root = root.resolve()
        credential_path = project_root.joinpath(*Path(credential_file).parts)
        current = project_root
        for part in Path(credential_file).parts:
            current /= part
            if current.is_symlink():
                raise CredentialResolutionError(
                    "The saved dotenv credential reference is unavailable."
                )
        value = _read_dotenv(credential_path).get(source.credential_env)
        origin = credential_file
    else:
        raise CredentialResolutionError("The saved credential reference kind is unsupported.")

    if not value:
        raise CredentialResolutionError(
            f"Credential variable {source.credential_env} is unavailable at its saved origin."
        )
    candidate = _parse_candidate(source.credential_env, value, origin)
    if candidate is None:
        raise CredentialResolutionError("The saved database URL is invalid.")
    expected = (
        source.engine,
        source.host.casefold(),
        source.port,
        source.database_name,
        source.username,
        source.ssl_mode,
    )
    actual = (
        candidate.engine,
        candidate.host.casefold(),
        candidate.port,
        candidate.database_name,
        candidate.username,
        candidate.ssl_mode,
    )
    if actual != expected:
        raise CredentialResolutionError(
            "The database URL identity changed; run init again before connecting."
        )
    if not candidate.has_password or candidate.password is None:
        raise CredentialResolutionError("The saved database URL has no password.")
    return candidate.password


def resolve_postgresql_password(
    source: SourceConfig,
    *,
    root: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Backward-compatible alias for resolving a saved source password."""

    return resolve_database_password(source, root=root, environ=environ)
