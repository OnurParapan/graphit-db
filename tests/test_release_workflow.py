"""Static safety contract for the first PyPI release workflow."""

from __future__ import annotations

import re
from pathlib import Path


def test_release_workflow_uses_scoped_trusted_publishing() -> None:
    workflow = (Path(__file__).parents[1] / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )

    assert "types: [published]" in workflow
    assert "pull_request_target" not in workflow
    assert "PYPI_TOKEN" not in workflow
    assert "password:" not in workflow
    assert "name: pypi" in workflow
    assert "id-token: write" in workflow
    assert workflow.index("  build:") < workflow.index("  publish:")
    assert "needs: build" in workflow
    assert "build==1.6.1 twine==7.0.0" in workflow
    assert "python scripts/check_dist.py" in workflow
    assert "python -m twine check --strict dist/*" in workflow
    assert "scripts/smoke_installed.py" in workflow


def test_release_workflow_pins_every_action_to_a_commit() -> None:
    workflow = (Path(__file__).parents[1] / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    action_refs = re.findall(r"uses:\s+[^\s@]+@([^\s]+)", workflow)

    assert len(action_refs) == 5
    assert all(re.fullmatch(r"[0-9a-f]{40}", reference) for reference in action_refs)
