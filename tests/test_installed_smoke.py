"""Contract for the clean-wheel smoke script using the development installation."""

from pathlib import Path

from scripts.smoke_installed import run_smoke


def test_installed_smoke_checks_init_current_mcp_tools_and_local_fk(tmp_path: Path) -> None:
    run_smoke(tmp_path, require_installed=False)
