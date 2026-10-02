"""Opt-in, project-scoped Codex MCP configuration."""

import json
import os
import stat
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path

from graphit.mcp_launcher import is_graphit_mcp_launch, local_mcp_launch

COMPACT_CODEX_TOOLS = (
    "database_overview",
    "get_relevant_context",
    "search_objects",
    "get_table",
    "get_view",
    "get_relationships",
    "get_graph_context",
    "find_path",
)


class CodexSetupError(Exception):
    """A Codex project configuration cannot be changed safely."""


@dataclass(frozen=True)
class CodexSetupResult:
    """Outcome of project-local Codex wiring."""

    path: Path
    changed: bool


def _graphit_entry(command: str, args: list[str], *, compact_tools: bool) -> dict[str, object]:
    entry: dict[str, object] = {"command": command, "args": args}
    if compact_tools:
        entry["enabled_tools"] = list(COMPACT_CODEX_TOOLS)
    return entry


def _graphit_stanza(command: str, args: list[str], newline: bytes, *, compact_tools: bool) -> bytes:
    enabled_tools = ""
    if compact_tools:
        enabled_tools = f"enabled_tools = {json.dumps(COMPACT_CODEX_TOOLS, ensure_ascii=False)}\n"
    return (
        (
            "[mcp_servers.graphit]\n"
            f"command = {json.dumps(command, ensure_ascii=False)}\n"
            f"args = {json.dumps(args, ensure_ascii=False)}\n"
            f"{enabled_tools}"
        )
        .replace("\n", newline.decode("ascii"))
        .encode("utf-8")
    )


def setup_codex(
    root: Path, *, refresh: bool = False, compact_tools: bool = False
) -> CodexSetupResult:
    """Add one MCP server without altering other project or global settings."""

    root = root.resolve()
    directory = root / ".codex"
    path = directory / "config.toml"
    if directory.is_symlink() or path.is_symlink():
        raise CodexSetupError("Refusing symlinked Codex configuration.")
    if directory.exists() and not directory.is_dir():
        raise CodexSetupError(f"{directory} is not a directory.")
    if path.exists() and not path.is_file():
        raise CodexSetupError(f"{path} is not a regular file.")

    try:
        existing = path.read_bytes() if path.exists() else b""
    except OSError as error:
        raise CodexSetupError("Could not read Codex project configuration.") from error
    try:
        config = tomllib.loads(existing.decode("utf-8-sig"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise CodexSetupError("Existing Codex config is not valid UTF-8 TOML.") from error

    command, args = local_mcp_launch(root)
    desired_entry = _graphit_entry(command, args, compact_tools=compact_tools)
    servers = config.get("mcp_servers", {})
    if not isinstance(servers, dict):
        raise CodexSetupError("Existing mcp_servers setting is not a TOML table.")
    newline = b"\r\n" if b"\r\n" in existing else b"\n"
    if "graphit" in servers:
        entry = servers["graphit"]
        if isinstance(entry, dict) and entry == desired_entry:
            return CodexSetupResult(path, changed=False)
        if not refresh:
            raise CodexSetupError(
                "A different Graphit MCP entry exists; use --refresh for an old Graphit launcher."
            )
        generated_keys = {"command", "args"}
        compact_keys = generated_keys | {"enabled_tools"}
        entry_keys = set(entry) if isinstance(entry, dict) else set()
        if not isinstance(entry, dict) or entry_keys not in (generated_keys, compact_keys):
            raise CodexSetupError("Existing Graphit MCP entry is not a generated launcher.")
        old_compact = "enabled_tools" in entry
        if not is_graphit_mcp_launch(entry["command"], entry["args"]) or (
            old_compact and entry["enabled_tools"] != list(COMPACT_CODEX_TOOLS)
        ):
            raise CodexSetupError("Existing Graphit MCP entry is not a generated launcher.")
        old_stanza = _graphit_stanza(
            entry["command"], entry["args"], newline, compact_tools=old_compact
        )
        if existing.count(old_stanza) != 1:
            raise CodexSetupError(
                "Existing Graphit stanza was edited; refresh requires manual review."
            )
        content = existing.replace(
            old_stanza,
            _graphit_stanza(command, args, newline, compact_tools=compact_tools),
            1,
        )
        try:
            refreshed = tomllib.loads(content.decode("utf-8-sig"))
        except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
            raise CodexSetupError(
                "Refreshed Codex config is invalid; no changes were made."
            ) from error
        if refreshed.get("mcp_servers", {}).get("graphit") != desired_entry:
            raise CodexSetupError("Graphit stanza refresh did not update the parsed entry.")
    else:
        separator = b"" if not existing or existing.endswith((b"\r", b"\n")) else newline
        content = (
            existing
            + separator
            + _graphit_stanza(command, args, newline, compact_tools=compact_tools)
        )

    temporary: Path | None = None
    try:
        if not directory.exists():
            directory.mkdir()
        with tempfile.NamedTemporaryFile(dir=directory, prefix=".graphit-", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(content)
        if path.exists():
            temporary.chmod(stat.S_IMODE(path.stat().st_mode))
        os.replace(temporary, path)
    except OSError as error:
        raise CodexSetupError(
            f"Could not write Codex project configuration: {error.strerror}"
        ) from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return CodexSetupResult(path, changed=True)
