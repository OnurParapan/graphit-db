"""Regression coverage for portable local MCP subprocess launchers."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pytest import MonkeyPatch

from graphit.mcp_launcher import local_mcp_launch


def test_local_mcp_launch_preserves_virtualenv_interpreter_symlink(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    base_python = tmp_path / "base" / "python3.14"
    base_python.parent.mkdir()
    base_python.write_bytes(b"")
    venv_python = tmp_path / "venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    try:
        venv_python.symlink_to(base_python)
    except OSError as error:
        pytest.skip(f"Interpreter symlinks are unavailable: {error}")
    monkeypatch.setattr(sys, "executable", str(venv_python))

    command, args = local_mcp_launch(tmp_path / "project")

    assert command == str(venv_python.absolute())
    assert command != str(venv_python.resolve())
    assert args[:5] == ["-m", "graphit", "mcp", "serve", "--project"]
