"""Opt-in, project-scoped Claude Code MCP configuration."""

import json
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from graphit.mcp_launcher import is_graphit_mcp_launch, local_mcp_launch


class ClaudeSetupError(Exception):
    """A Claude project configuration cannot be changed safely."""


@dataclass(frozen=True)
class ClaudeSetupResult:
    """Outcome of project-local Claude wiring."""

    path: Path
    changed: bool


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ClaudeSetupError("Existing Claude config has duplicate JSON keys.")
        result[key] = value
    return result


def setup_claude(root: Path, *, refresh: bool = False) -> ClaudeSetupResult:
    """Add one server to .mcp.json without changing other project settings."""

    root = root.resolve()
    path = root / ".mcp.json"
    if path.is_symlink():
        raise ClaudeSetupError("Refusing symlinked Claude project configuration.")
    if path.exists() and not path.is_file():
        raise ClaudeSetupError(f"{path} is not a regular file.")
    try:
        original = path.read_bytes() if path.exists() else b""
    except OSError as error:
        raise ClaudeSetupError("Could not read Claude project configuration.") from error
    try:
        config = (
            json.loads(original.decode("utf-8-sig"), object_pairs_hook=_unique_pairs)
            if path.exists()
            else {}
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ClaudeSetupError("Existing Claude config is not valid UTF-8 JSON.") from error
    if not isinstance(config, dict):
        raise ClaudeSetupError("Existing Claude config must be a JSON object.")
    servers = config.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise ClaudeSetupError("Existing mcpServers setting must be a JSON object.")

    command, args = local_mcp_launch(root)
    entry = {"type": "stdio", "command": command, "args": args}
    if "graphit" in servers:
        old_entry = servers["graphit"]
        if old_entry == entry:
            return ClaudeSetupResult(path, changed=False)
        if not refresh:
            raise ClaudeSetupError(
                "A different Graphit MCP entry exists; use --refresh for an old Graphit launcher."
            )
        if not isinstance(old_entry, dict) or set(old_entry) != {"type", "command", "args"}:
            raise ClaudeSetupError("Existing Graphit MCP entry is not a generated launcher.")
        if old_entry["type"] != "stdio" or not is_graphit_mcp_launch(
            old_entry["command"], old_entry["args"]
        ):
            raise ClaudeSetupError("Existing Graphit MCP entry is not a generated launcher.")
    servers["graphit"] = entry

    newline = "\r\n" if b"\r\n" in original else "\n"
    content = (json.dumps(config, ensure_ascii=False, indent=2) + "\n").replace("\n", newline)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=root, prefix=".graphit-mcp-", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(content.encode("utf-8"))
        if path.exists():
            temporary.chmod(stat.S_IMODE(path.stat().st_mode))
        os.replace(temporary, path)
    except OSError as error:
        raise ClaudeSetupError("Could not write Claude project configuration.") from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return ClaudeSetupResult(path, changed=True)
