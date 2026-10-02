"""Project-local Codex MCP setup leaves other settings untouched."""

import sys
import tomllib
from pathlib import Path

import anyio
import pytest
from mcp import Client, StdioServerParameters
from typer.testing import CliRunner

from graphit.cli import app
from graphit.codex import COMPACT_CODEX_TOOLS
from graphit.project import initialize_project

runner = CliRunner()


def _setup_project(root: Path) -> Path:
    initialize_project(root)
    return root / ".codex" / "config.toml"


def test_setup_creates_project_only_config(tmp_path: Path) -> None:
    path = _setup_project(tmp_path)

    result = runner.invoke(app, ["mcp", "setup-codex", "--project", str(tmp_path)])

    assert result.exit_code == 0
    assert path.is_file()
    entry = tomllib.loads(path.read_text(encoding="utf-8"))["mcp_servers"]["graphit"]
    assert entry["command"] == str(Path(sys.executable).resolve())
    assert entry["args"] == ["-m", "graphit", "mcp", "serve", "--project", str(tmp_path)]
    assert "password" not in path.read_text(encoding="utf-8").lower()


def test_setup_preserves_existing_config_and_is_idempotent(tmp_path: Path) -> None:
    path = _setup_project(tmp_path)
    path.parent.mkdir()
    original = b"[features]\r\nhooks = true\r\n# Keep this comment\r\n"
    path.write_bytes(original)

    first = runner.invoke(app, ["mcp", "setup-codex", "--project", str(tmp_path)])
    changed = path.read_bytes()
    again = runner.invoke(app, ["mcp", "setup-codex", "--project", str(tmp_path)])

    assert first.exit_code == again.exit_code == 0
    assert changed.startswith(original)
    assert b"\r\n[mcp_servers.graphit]\r\n" in changed
    assert path.read_bytes() == changed
    assert "already configured" in again.stdout


def test_setup_compact_tools_is_explicit_and_idempotent(tmp_path: Path) -> None:
    path = _setup_project(tmp_path)
    arguments = ["mcp", "setup-codex", "--project", str(tmp_path), "--compact-tools"]

    first = runner.invoke(app, arguments)
    changed = path.read_bytes()
    again = runner.invoke(app, arguments)

    assert first.exit_code == again.exit_code == 0
    entry = tomllib.loads(changed.decode("utf-8"))["mcp_servers"]["graphit"]
    assert entry["enabled_tools"] == list(COMPACT_CODEX_TOOLS)
    assert path.read_bytes() == changed
    assert "already configured" in again.stdout


def test_setup_switches_between_compact_and_full_only_with_refresh(tmp_path: Path) -> None:
    path = _setup_project(tmp_path)
    base = ["mcp", "setup-codex", "--project", str(tmp_path)]
    assert runner.invoke(app, [*base, "--compact-tools"]).exit_code == 0
    compact = path.read_bytes()

    rejected = runner.invoke(app, base)
    assert rejected.exit_code == 2
    assert path.read_bytes() == compact

    full = runner.invoke(app, [*base, "--refresh"])
    assert full.exit_code == 0
    entry = tomllib.loads(path.read_text(encoding="utf-8"))["mcp_servers"]["graphit"]
    assert "enabled_tools" not in entry

    compact_again = runner.invoke(app, [*base, "--refresh", "--compact-tools"])
    assert compact_again.exit_code == 0
    entry = tomllib.loads(path.read_text(encoding="utf-8"))["mcp_servers"]["graphit"]
    assert entry["enabled_tools"] == list(COMPACT_CODEX_TOOLS)


def test_compact_refresh_rejects_custom_tool_allowlist(tmp_path: Path) -> None:
    path = _setup_project(tmp_path)
    arguments = ["mcp", "setup-codex", "--project", str(tmp_path), "--compact-tools"]
    assert runner.invoke(app, arguments).exit_code == 0
    custom = path.read_text(encoding="utf-8").replace(
        '"find_path"]', '"find_path", "get_index_context"]'
    )
    path.write_text(custom, encoding="utf-8")

    result = runner.invoke(app, [*arguments, "--refresh"])

    assert result.exit_code == 2
    assert "not a generated launcher" in result.stderr
    assert path.read_text(encoding="utf-8") == custom


@pytest.mark.parametrize(
    "existing",
    [
        '[mcp_servers.graphit]\ncommand = "other"\nargs = []\n',
        '[mcp_servers.graphit]\ncommand = "graphit"\nargs = []\n',
        '[mcp_servers.graphit]\ncommand = "/old/bin/python"\n'
        'args = ["-m", "graphit", "mcp", "serve", "--project", "/old/project"]\n'
        'env = { TOKEN = "x" }\n',
        "[mcp_servers.graphit\n",
    ],
)
@pytest.mark.parametrize("refresh", [False, True])
def test_setup_rejects_conflicting_or_invalid_config(
    tmp_path: Path, existing: str, refresh: bool
) -> None:
    path = _setup_project(tmp_path)
    path.parent.mkdir()
    path.write_text(existing, encoding="utf-8")

    arguments = ["mcp", "setup-codex", "--project", str(tmp_path)]
    result = runner.invoke(app, arguments + (["--refresh"] if refresh else []))

    assert result.exit_code == 2
    assert "CODEX_SETUP_FAILED" in result.stderr
    assert path.read_text(encoding="utf-8") == existing


def test_refresh_replaces_only_old_generated_codex_launcher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _setup_project(tmp_path)
    path.parent.mkdir()
    original = b'model = "existing-model"\r\n# Keep this comment\r\n'
    path.write_bytes(original)
    old_args = ["-m", "graphit", "mcp", "serve", "--project", r"C:\old\project"]
    with monkeypatch.context() as patcher:
        patcher.setattr(
            "graphit.codex.local_mcp_launch", lambda root: (r"C:\old\python.exe", old_args)
        )
        assert runner.invoke(app, ["mcp", "setup-codex", "--project", str(tmp_path)]).exit_code == 0
    stale = path.read_bytes()
    arguments = ["mcp", "setup-codex", "--project", str(tmp_path)]

    rejected = runner.invoke(app, arguments)
    assert rejected.exit_code == 2
    assert path.read_bytes() == stale

    refreshed = runner.invoke(app, arguments + ["--refresh"])
    assert refreshed.exit_code == 0
    updated = path.read_bytes()
    assert updated.startswith(original)
    entry = tomllib.loads(updated.decode("utf-8"))["mcp_servers"]["graphit"]
    assert entry["command"] == str(Path(sys.executable).resolve())
    assert entry["args"][-1] == str(tmp_path)
    assert runner.invoke(app, arguments + ["--refresh"]).exit_code == 0
    assert path.read_bytes() == updated


def test_refresh_rejects_edited_codex_stanza(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _setup_project(tmp_path)
    with monkeypatch.context() as patcher:
        patcher.setattr(
            "graphit.codex.local_mcp_launch",
            lambda root: (
                r"C:\old\python.exe",
                ["-m", "graphit", "mcp", "serve", "--project", r"C:\old\project"],
            ),
        )
        assert runner.invoke(app, ["mcp", "setup-codex", "--project", str(tmp_path)]).exit_code == 0
    altered = path.read_bytes().replace(b"command = ", b"command= ")
    path.write_bytes(altered)

    result = runner.invoke(app, ["mcp", "setup-codex", "--project", str(tmp_path), "--refresh"])

    assert result.exit_code == 2
    assert path.read_bytes() == altered


def test_setup_requires_initialized_project(tmp_path: Path) -> None:
    result = runner.invoke(app, ["mcp", "setup-codex", "--project", str(tmp_path)])

    assert result.exit_code == 3
    assert not (tmp_path / ".codex").exists()


def test_setup_rejects_symlinked_config(tmp_path: Path) -> None:
    path = _setup_project(tmp_path)
    path.parent.mkdir()
    target = tmp_path / "external.toml"
    target.write_text("# untouched\n", encoding="utf-8")
    try:
        path.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("Symlink creation is unavailable")

    result = runner.invoke(app, ["mcp", "setup-codex", "--project", str(tmp_path)])

    assert result.exit_code == 2
    assert target.read_text(encoding="utf-8") == "# untouched\n"


@pytest.mark.anyio
async def test_configured_command_starts_real_mcp_process(tmp_path: Path) -> None:
    path = _setup_project(tmp_path)
    result = runner.invoke(app, ["mcp", "setup-codex", "--project", str(tmp_path)])
    assert result.exit_code == 0
    entry = tomllib.loads(path.read_text(encoding="utf-8"))["mcp_servers"]["graphit"]
    process = StdioServerParameters(command=entry["command"], args=entry["args"])

    with anyio.fail_after(15):
        async with Client(process) as client:
            tools = await client.list_tools()
            names = {tool.name for tool in tools.tools}
            assert "database_overview" in names
            assert set(COMPACT_CODEX_TOOLS) <= names


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
