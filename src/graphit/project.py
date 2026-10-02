"""Safe, project-local Graphit initialization."""

from dataclasses import dataclass
from pathlib import Path

from graphit.store import StoreError, initialize_store

CONFIG_NAME = "graphit.toml"
STATE_NAME = ".graphit"
STORE_NAME = "graphit.db"
IGNORE_ENTRY = ".graphit/"

DEFAULT_CONFIG = """# Graphit project configuration. Never put passwords in this file.
[scan]
schemas = ["public"]
include_views = true
include_indexes = true
infer_relationships = false

[sampling]
enabled = false
max_rows = 500
statement_timeout_ms = 5000

[context]
default_object_limit = 20
default_edge_limit = 50
"""


class ProjectError(Exception):
    """A project cannot be initialized without overriding or risking user data."""


@dataclass(frozen=True)
class InitResult:
    """Paths actually changed by an initialization operation."""

    root: Path
    created: tuple[Path, ...]
    updated: tuple[Path, ...]


def find_project_root(start: Path) -> Path:
    """Find existing Graphit config, then Git root, falling back to the start."""

    directory = start.resolve()
    if not directory.is_dir():
        raise ProjectError(f"Project directory does not exist: {directory}")

    for candidate in (directory, *directory.parents):
        if (candidate / CONFIG_NAME).exists() or (candidate / CONFIG_NAME).is_symlink():
            return candidate
        if (candidate / ".git").exists():
            return candidate
    return directory


def _is_git_project(root: Path) -> bool:
    return any((directory / ".git").exists() for directory in (root, *root.parents))


def initialize_project(root: Path, *, force: bool = False) -> InitResult:
    """Create safe configuration and local state, preserving existing user data."""

    root = root.resolve()
    if not root.is_dir():
        raise ProjectError(f"Project directory does not exist: {root}")

    config = root / CONFIG_NAME
    state = root / STATE_NAME
    ignore = root / ".gitignore"

    if config.is_symlink() or state.is_symlink() or ignore.is_symlink():
        raise ProjectError("Initialization refuses symlinked project files or state.")
    if config.exists() and not force:
        raise ProjectError(f"{config} already exists. Use --force to replace it.")
    if config.exists() and not config.is_file():
        raise ProjectError(f"{config} is not a regular file.")
    if state.exists() and not state.is_dir():
        raise ProjectError(f"{state} is not a directory.")
    if ignore.exists() and not ignore.is_file():
        raise ProjectError(f"{ignore} is not a regular file.")

    ignore_content: bytes | None = None
    if _is_git_project(root):
        if ignore.exists():
            content = ignore.read_bytes()
            text = content.decode("utf-8")
            if IGNORE_ENTRY not in (line.strip() for line in text.splitlines()):
                separator = b"\r\n" if b"\r\n" in content else b"\n"
                prefix = b"" if not content or content.endswith((b"\r", b"\n")) else separator
                ignore_content = content + prefix + IGNORE_ENTRY.encode("utf-8") + separator
        else:
            ignore_content = (IGNORE_ENTRY + "\n").encode("utf-8")

    store = state / STORE_NAME
    if store.is_symlink():
        raise ProjectError(f"Initialization refuses symlinked store: {store}")

    created: list[Path] = []
    updated: list[Path] = []
    if not state.exists():
        state.mkdir()
        created.append(state)
    try:
        store_created = initialize_store(store)
    except StoreError as error:
        raise ProjectError(str(error)) from error
    if store_created:
        created.append(store)

    if config.exists():
        config.write_text(DEFAULT_CONFIG, encoding="utf-8", newline="\n")
        updated.append(config)
    else:
        config.write_text(DEFAULT_CONFIG, encoding="utf-8", newline="\n")
        created.append(config)

    if ignore_content is not None:
        existed = ignore.exists()
        ignore.write_bytes(ignore_content)
        (updated if existed else created).append(ignore)

    return InitResult(root, tuple(created), tuple(updated))
