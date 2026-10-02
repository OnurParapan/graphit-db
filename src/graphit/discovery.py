"""Secret-safe discovery of project PostgreSQL connection URLs."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, unquote, urlsplit

_URL_ENV_NAME = re.compile(
    r"(?:^|_)(?:DATABASE|POSTGRES|POSTGRESQL)_URL\Z|^PGURL\Z",
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
_SSL_MODES = frozenset({"disable", "prefer", "require", "verify-ca", "verify-full"})

if TYPE_CHECKING:
    from graphit.sources import SourceConfig


class CredentialResolutionError(Exception):
    """A saved non-secret credential reference cannot be resolved safely."""


@dataclass(frozen=True)
class PostgreSQLCandidate:
    """One transient connection candidate; its password is never represented."""

    variable_name: str
    origin: str
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


def _safe_text(value: str) -> bool:
    return bool(value) and not any(ord(character) < 32 for character in value)


def _parse_candidate(variable_name: str, value: str, origin: str) -> PostgreSQLCandidate | None:
    try:
        parsed = urlsplit(value)
        if parsed.scheme.lower() not in {"postgres", "postgresql"}:
            return None
        host = parsed.hostname
        port = parsed.port or 5432
        username = unquote(parsed.username) if parsed.username is not None else ""
        password = unquote(parsed.password) if parsed.password is not None else None
        database_name = unquote(parsed.path.removeprefix("/"))
        query = parse_qs(parsed.query, keep_blank_values=True)
        ssl_values = query.get("sslmode", ["prefer"])
        ssl_mode = ssl_values[-1]
    except (TypeError, ValueError):
        return None

    if (
        parsed.fragment
        or host is None
        or not _safe_text(host)
        or not _safe_text(username)
        or not _safe_text(database_name)
        or not 1 <= port <= 65535
        or ssl_mode not in _SSL_MODES
    ):
        return None
    return PostgreSQLCandidate(
        variable_name=variable_name,
        origin=origin,
        host=host,
        port=port,
        database_name=database_name,
        username=username,
        password=password,
        ssl_mode=ssl_mode,
    )


def discover_postgresql(
    root: Path, *, environ: Mapping[str, str] | None = None
) -> tuple[PostgreSQLCandidate, ...]:
    """Find bounded PostgreSQL URL candidates without persisting or printing secrets."""

    root = root.resolve()
    process_environment = os.environ if environ is None else environ
    locations: list[tuple[str, Mapping[str, str]]] = [
        ("environment", process_environment),
    ]
    for name in _DOTENV_NAMES:
        path = root / name
        dotenv_values = _read_dotenv(path)
        if dotenv_values:
            locations.append((name, dotenv_values))

    candidates: list[PostgreSQLCandidate] = []
    seen_variables: set[str] = set()
    seen_connections: set[tuple[str, int, str, str]] = set()
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


def resolve_postgresql_password(
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
        if root is None or source.credential_file not in _DOTENV_NAMES:
            raise CredentialResolutionError("The saved dotenv credential reference is unavailable.")
        value = _read_dotenv(root.resolve() / source.credential_file).get(source.credential_env)
        origin = source.credential_file
    else:
        raise CredentialResolutionError("The saved credential reference kind is unsupported.")

    if not value:
        raise CredentialResolutionError(
            f"Credential variable {source.credential_env} is unavailable at its saved origin."
        )
    candidate = _parse_candidate(source.credential_env, value, origin)
    if candidate is None:
        raise CredentialResolutionError("The saved PostgreSQL URL is invalid.")
    expected = (
        source.host.casefold(),
        source.port,
        source.database_name,
        source.username,
        source.ssl_mode,
    )
    actual = (
        candidate.host.casefold(),
        candidate.port,
        candidate.database_name,
        candidate.username,
        candidate.ssl_mode,
    )
    if actual != expected:
        raise CredentialResolutionError(
            "The PostgreSQL URL identity changed; run init again before connecting."
        )
    if not candidate.has_password or candidate.password is None:
        raise CredentialResolutionError("The saved PostgreSQL URL has no password.")
    return candidate.password
