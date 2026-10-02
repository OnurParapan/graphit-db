"""Regression tests for the unpublished distribution archive gate."""

from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path

import pytest

from scripts.check_dist import check_distributions


def make_archives(
    root: Path,
    *,
    extra_sdist: str | None = None,
    license_expression: str = "Apache-2.0",
    entry_point: str = "graphit.cli:app",
    metadata_author: str = "Onur Parapan",
    metadata_repository: str = "https://github.com/OnurParapan/graphit-db",
    include_notice: bool = True,
) -> None:
    (root / "pyproject.toml").write_text(
        '[project]\nname = "graphit-db"\nversion = "0.1.0"\n'
        'authors = [{name = "Onur Parapan"}]\n'
        '[project.urls]\nRepository = "https://github.com/OnurParapan/graphit-db"\n',
        encoding="utf-8",
    )
    (root / "LICENSE").write_bytes(b"approved license text")
    if include_notice:
        (root / "NOTICE").write_bytes(b"approved notice text")
    dist = root / "dist"
    dist.mkdir()
    stem = "graphit_db-0.1.0"
    sdist_members = {
        f"{stem}/LICENSE": b"approved license text",
        f"{stem}/README.md": b"Graphit",
        f"{stem}/pyproject.toml": b"project metadata",
        f"{stem}/src/graphit/cli.py": b"app = object()",
    }
    if include_notice:
        sdist_members[f"{stem}/NOTICE"] = b"approved notice text"
    if extra_sdist is not None:
        sdist_members[f"{stem}/{extra_sdist}"] = b"private state"
    with tarfile.open(dist / f"{stem}.tar.gz", "w:gz") as archive:
        for name, data in sdist_members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))

    metadata_dir = f"{stem}.dist-info"
    with zipfile.ZipFile(dist / f"{stem}-py3-none-any.whl", "w") as archive:
        archive.writestr("graphit/__init__.py", "")
        archive.writestr("graphit/cli.py", "app = object()")
        archive.writestr(
            f"{metadata_dir}/METADATA",
            f"Name: graphit-db\nVersion: 0.1.0\nAuthor: {metadata_author}\n"
            f"Project-URL: Repository, {metadata_repository}\n"
            f"License-Expression: {license_expression}\nLicense-File: LICENSE\n"
            + ("License-File: NOTICE\n" if include_notice else ""),
        )
        archive.writestr(
            f"{metadata_dir}/entry_points.txt",
            f"[console_scripts]\ngraphit = {entry_point}\n",
        )
        archive.writestr(f"{metadata_dir}/licenses/LICENSE", b"approved license text")
        if include_notice:
            archive.writestr(f"{metadata_dir}/licenses/NOTICE", b"approved notice text")


def test_distribution_check_accepts_scoped_archives(tmp_path: Path) -> None:
    make_archives(tmp_path)

    check_distributions(tmp_path, tmp_path / "dist")


def test_distribution_check_rejects_private_source_state(tmp_path: Path) -> None:
    make_archives(tmp_path, extra_sdist=".wolf/STATUS.md")

    with pytest.raises(ValueError, match="Unsafe or private archive member"):
        check_distributions(tmp_path, tmp_path / "dist")


def test_distribution_check_rejects_nested_private_source_state(tmp_path: Path) -> None:
    make_archives(tmp_path, extra_sdist="docs/.codex/config.toml")

    with pytest.raises(ValueError, match="Unsafe or private archive member"):
        check_distributions(tmp_path, tmp_path / "dist")


def test_distribution_check_rejects_missing_license_expression(tmp_path: Path) -> None:
    make_archives(tmp_path, license_expression="")

    with pytest.raises(ValueError, match="Apache-2.0 license expression"):
        check_distributions(tmp_path, tmp_path / "dist")


def test_distribution_check_rejects_wrong_cli_entry_point(tmp_path: Path) -> None:
    make_archives(tmp_path, entry_point="graphit.cli:wrong")

    with pytest.raises(ValueError, match="graphit CLI entry point"):
        check_distributions(tmp_path, tmp_path / "dist")


def test_distribution_check_rejects_wrong_author(tmp_path: Path) -> None:
    make_archives(tmp_path, metadata_author="Someone Else")

    with pytest.raises(ValueError, match="Wheel author differs"):
        check_distributions(tmp_path, tmp_path / "dist")


def test_distribution_check_rejects_wrong_project_url(tmp_path: Path) -> None:
    make_archives(tmp_path, metadata_repository="https://example.invalid/wrong")

    with pytest.raises(ValueError, match="Wheel project URLs differ"):
        check_distributions(tmp_path, tmp_path / "dist")


def test_distribution_check_rejects_missing_notice(tmp_path: Path) -> None:
    make_archives(tmp_path, include_notice=False)

    with pytest.raises(ValueError, match="NOTICE file is missing"):
        check_distributions(tmp_path, tmp_path / "dist")
