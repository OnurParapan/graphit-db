"""Stable local subprocess command for the installed Graphit package."""

import re
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath


def local_mcp_launch(root: Path) -> tuple[str, list[str]]:
    """Launch Graphit without relying on an agent's PATH or working directory."""

    command = str(Path(sys.executable).resolve())
    args = ["-m", "graphit", "mcp", "serve", "--project", str(root.resolve())]
    return command, args


def is_graphit_mcp_launch(command: object, args: object) -> bool:
    """Recognize the exact generated launch shape, even after a machine move."""

    if not isinstance(command, str) or not isinstance(args, list):
        return False
    if len(args) != 6 or any(not isinstance(arg, str) for arg in args):
        return False
    if args[:5] != ["-m", "graphit", "mcp", "serve", "--project"]:
        return False
    command_paths = (PurePosixPath(command), PureWindowsPath(command))
    project_paths = (PurePosixPath(args[5]), PureWindowsPath(args[5]))
    if not any(path.is_absolute() for path in command_paths):
        return False
    if not any(path.is_absolute() for path in project_paths):
        return False
    return any(
        re.fullmatch(r"python(?:\d+(?:\.\d+)?)?(?:\.exe)?", path.name, flags=re.IGNORECASE)
        for path in command_paths
    )
