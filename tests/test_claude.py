"""Claude Code project MCP setup preserves existing settings and starts Graphit."""

import json
import sys
from pathlib import Path

import anyio
import pytest
from mcp import Client, StdioServerParameters
from typer.testing import CliRunner

from graphit.cli import app
from graphit.project import initialize_project

runner = CliRunner()


def _setup_project(root: Path) -> Path:
    initialize_project(root)
    return root / ".mcp.json"


def test_setup_creates_project_only_config(tmp_path: Path) -> None:
    path = _setup_project(tmp_path)

    result = runner.invoke(app, ["mcp", "setup-claude", "--project", str(tmp_path)])

    assert result.exit_code == 0
    entry = json.loads(path.read_text(encoding="utf-8"))["mcpServers"]["graphit"]
    assert entry == {
        "type": "stdio",
        "command": str(Path(sys.executable).resolve()),
        "args": ["-m", "graphit", "mcp", "serve", "--project", str(tmp_path)],
    }
    assert "machine-specific" in result.stdout
    assert not (tmp_path / ".codex").exists()


def test_setup_preserves_existing_settings_and_is_idempotent(tmp_path: Path) -> None:
    path = _setup_project(tmp_path)
    existing = {
        "mcpServers": {"other": {"type": "stdio", "command": "other", "args": []}},
        "custom": {"nested": [1, "two"]},
    }
    path.write_text(json.dumps(existing, indent=4) + "\n", encoding="utf-8")

    first = runner.invoke(app, ["mcp", "setup-claude", "--project", str(tmp_path)])
    changed = path.read_bytes()
    again = runner.invoke(app, ["mcp", "setup-claude", "--project", str(tmp_path)])

    assert first.exit_code == again.exit_code == 0
    updated = json.loads(changed)
    assert updated["mcpServers"]["other"] == {
        "type": "stdio",
        "command": "other",
        "args": [],
    }
    assert updated["custom"] == existing["custom"]
    assert path.read_bytes() == changed
    assert "already configured" in again.stdout


@pytest.mark.parametrize(
    "existing",
    [
        '{"mcpServers":{"graphit":{"command":"other","args":[]}}}',
        '{"mcpServers":{"graphit":null}}',
        '{"mcpServers":{"graphit":{"type":"stdio","command":"/old/bin/python",'
        '"args":["-m","graphit","mcp","serve","--project","/old/project"],'
        '"env":{"TOKEN":"x"}}}}',
        '{"mcpServers": []}',
        '{"mcpServers":{},"mcpServers":{}}',
        "{invalid",
        "",
        "[]",
    ],
)
@pytest.mark.parametrize("refresh", [False, True])
def test_setup_rejects_conflicting_or_invalid_config(
    tmp_path: Path, existing: str, refresh: bool
) -> None:
    path = _setup_project(tmp_path)
    path.write_text(existing, encoding="utf-8")

    arguments = ["mcp", "setup-claude", "--project", str(tmp_path)]
    result = runner.invoke(app, arguments + (["--refresh"] if refresh else []))

    assert result.exit_code == 2
    assert "CLAUDE_SETUP_FAILED" in result.stderr
    assert path.read_text(encoding="utf-8") == existing


def test_refresh_replaces_only_old_generated_claude_launcher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _setup_project(tmp_path)
    existing = {"projectSetting": "keep", "mcpServers": {"other": {"command": "other"}}}
    path.write_text(json.dumps(existing), encoding="utf-8")
    old_args = ["-m", "graphit", "mcp", "serve", "--project", "/old/project"]
    with monkeypatch.context() as patcher:
        patcher.setattr(
            "graphit.claude.local_mcp_launch", lambda root: ("/old/bin/python3.14", old_args)
        )
        assert (
            runner.invoke(app, ["mcp", "setup-claude", "--project", str(tmp_path)]).exit_code == 0
        )
    stale = path.read_bytes()
    arguments = ["mcp", "setup-claude", "--project", str(tmp_path)]

    assert runner.invoke(app, arguments).exit_code == 2
    assert path.read_bytes() == stale

    refreshed = runner.invoke(app, arguments + ["--refresh"])
    assert refreshed.exit_code == 0
    updated = path.read_bytes()
    config = json.loads(updated)
    assert config["projectSetting"] == "keep"
    assert config["mcpServers"]["other"] == {"command": "other"}
    assert config["mcpServers"]["graphit"] == {
        "type": "stdio",
        "command": str(Path(sys.executable).resolve()),
        "args": ["-m", "graphit", "mcp", "serve", "--project", str(tmp_path)],
    }
    assert runner.invoke(app, arguments + ["--refresh"]).exit_code == 0
    assert path.read_bytes() == updated


def test_setup_requires_initialized_project(tmp_path: Path) -> None:
    result = runner.invoke(app, ["mcp", "setup-claude", "--project", str(tmp_path)])

    assert result.exit_code == 3
    assert not (tmp_path / ".mcp.json").exists()


def test_setup_rejects_symlinked_config(tmp_path: Path) -> None:
    path = _setup_project(tmp_path)
    target = tmp_path / "external.json"
    target.write_text("{}\n", encoding="utf-8")
    try:
        path.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("Symlink creation is unavailable")

    result = runner.invoke(app, ["mcp", "setup-claude", "--project", str(tmp_path)])

    assert result.exit_code == 2
    assert target.read_text(encoding="utf-8") == "{}\n"


@pytest.mark.anyio
async def test_configured_command_starts_real_mcp_process(tmp_path: Path) -> None:
    path = _setup_project(tmp_path)
    result = runner.invoke(app, ["mcp", "setup-claude", "--project", str(tmp_path)])
    assert result.exit_code == 0
    entry = json.loads(path.read_text(encoding="utf-8"))["mcpServers"]["graphit"]
    process = StdioServerParameters(command=entry["command"], args=entry["args"])

    with anyio.fail_after(15):
        async with Client(process) as client:
            tools = await client.list_tools()
            assert "get_relevant_context" in {tool.name for tool in tools.tools}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
